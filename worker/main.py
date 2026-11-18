"""ZIROQ worker entry point.

  python main.py check     -> exit code 0 if a job is waiting, 10 if the queue is empty (fast; used by the workflow's first step)
  python main.py run       -> fail stale jobs, claim ONE job, run the engine, upload to Drive, write clip documents
Environment (GitHub Actions secrets): FIREBASE_SERVICE_ACCOUNT (JSON), GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET,
GOOGLE_DRIVE_REFRESH_TOKEN, GROQ_API_KEY_1.., GEMINI_API_KEY_1.., YT_COOKIES (optional), ZIROQ_MAX_SOURCE_MIN (default 90).
"""
import json
import os
import re
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from progress import Progress, scrub, register_secrets_from_env   # noqa: E402
from ytdlp_opts import validate_youtube_url, plain_download_error, write_cookie_file   # noqa: E402

LAGOS = timezone(timedelta(hours=1))
OUTPUT_ROOT_NAME = "ZIROQ Output"


def plain_failure(exc):
    """Every failure becomes a sentence plus a next step."""
    text = str(exc)
    low = text.lower()
    if "invalid_grant" in low or "refresh token" in low:
        return text
    if any(k in low for k in ("sign in to confirm", "not a bot", "private video", "members-only", "video unavailable", "live event", "http error 429")):
        return plain_download_error(text)
    if "only youtube" in low or "valid youtube" in low or "youtube video link" in low or "file paths" in low or "playlist" in low:
        return text
    if "qa" in low and "failed" in low:
        return "A clip failed the quality checks even after a retry, so nothing was delivered. Try again, or try a different video. Details: " + text[:200]
    if "timed out" in low or "timeout" in low:
        return "A step took too long and was stopped. Try again; if it repeats, choose a shorter video."
    if "no space left" in low:
        return "The worker ran out of disk space. Try a shorter video."
    if "no words were transcribed" in low:
        return "No speech was found in this video, so there is nothing to clip. Try a video where people talk."
    return "Something went wrong: " + text[:240] + ". Tap 'Make clips' to try again."


def precheck_source(url, max_minutes, extra_opts):
    """Fails early (plain message) for live streams, access-restricted or too-long videos, before any heavy work."""
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True}
    opts.update(extra_opts)
    with yt_dlp.YoutubeDL(opts) as ydl:
        meta = ydl.extract_info(url, download=False)
    if meta.get("is_live"):
        raise RuntimeError("Live event: live streams are not supported.")
    if meta.get("availability") in ("needs_auth", "subscriber_only", "premium_only"):
        raise RuntimeError("members-only: this video needs a sign-in or membership. Access controls are not bypassed.")
    minutes = (meta.get("duration") or 0) / 60.0
    if minutes > max_minutes:
        raise RuntimeError(f"This video is {minutes:.0f} minutes long; the limit is {max_minutes} minutes (change it in Settings).")
    return {"title": meta.get("title") or "", "channel": meta.get("channel") or meta.get("uploader") or "", "minutes": round(minutes, 1)}


def clip_documents(job_id, job, result, mapping, folder_ids, drive):
    """Builds one Firestore document per clip from the engine result and the Drive upload mapping."""
    docs = {}
    pack_folder = folder_ids.get("PUBLISHING_PACK")
    for item in result["clips"]:
        idx = int(item["index"])
        video_name = os.path.basename(item["final_path"])
        cover_name = os.path.basename(item["cover_path"])
        video = next((v for k, v in mapping.items() if k.endswith("/" + video_name) or k == video_name), {})
        cover = next((v for k, v in mapping.items() if k.endswith("/" + cover_name) or k == cover_name), {})
        review = item.get("review") or {}
        packs = {plat: rows.get(str(idx)) for plat, rows in (result.get("packs") or {}).items() if rows.get(str(idx))}
        docs[f"{job_id}_{idx:02d}"] = {
            "jobId": job_id, "index": idx, "title": str(item["title"])[:200], "duration": round(float(item["duration"]), 1),
            "driveUrl": video.get("url", ""), "coverUrl": cover.get("url", ""),
            "packUrl": drive.folder_url(pack_folder) if (pack_folder and drive) else "",
            "reviewFlags": [str(f)[:300] for f in item.get("review_flags", [])][:10],
            "geminiReview": {"verdict": review.get("verdict"), "score": review.get("score"), "summary": str(review.get("summary", ""))[:400]} if review else None,
            "editorialScore": item.get("editorial_score"), "longClip": bool(item.get("long_clip")),
            "qaSummary": f"technical {'pass' if item.get('technical_pass') else 'FAIL'}, editorial {'pass' if item.get('editorial_pass') else 'FAIL'}",
            "lengthNotes": (result.get("length_notes") or {}).get(str(idx), []),
            "pack": packs, "sourceTitle": result.get("source_title", ""), "sourceVideoId": result.get("source_video_id", ""),
            "status": "candidate", "createdAt": time.time(),
        }
    return docs


def process_job(store, job, drive, env, work_root):
    job_id = job["id"]
    progress = Progress(lambda upd: store.update_job(job_id, upd), min_interval=6.0)
    settings = store.get_settings()
    try:
        video_id, canonical = validate_youtube_url(job["url"])
        cookie_path = write_cookie_file(env.get("YT_COOKIES", ""), directory=work_root)
        if cookie_path:
            os.environ["ZIROQ_COOKIE_FILE"] = cookie_path
        from ytdlp_opts import ydl_extra_opts
        progress.line("checking the video")
        meta = precheck_source(canonical, int(env.get("ZIROQ_MAX_SOURCE_MIN") or settings.get("maxSourceMinutes", 90)), ydl_extra_opts(env))
        store.update_job(job_id, {"title": meta["title"][:150], "channel": meta["channel"][:100], "sourceId": video_id})

        job_dir = os.path.join(work_root, job_id)
        os.makedirs(job_dir, exist_ok=True)
        from run_engine import run_job
        result = run_job(canonical, job_dir, bool(settings.get("geminiConsent")), progress)

        progress.state("delivered")
        progress.line("uploading to Google Drive")
        root_id = drive.find_or_create_folder(OUTPUT_ROOT_NAME)
        today = datetime.now(LAGOS).strftime("%Y-%m-%d")
        title = re.sub(r"[^\w\s\-]", "", result.get("source_title") or meta["title"] or video_id).strip()[:60] or video_id
        run_folder = drive.create_folder(f"{title} - {today}", root_id)
        mapping, folder_ids = drive.upload_tree(result["delivery_dir"], run_folder)
        for item in result["clips"]:                 # every clip's video and cover must really be on Drive before we say 'done'
            for key in ("final_path", "cover_path"):
                name = os.path.basename(item[key])
                if not any(k == name or k.endswith("/" + name) for k in mapping):
                    raise RuntimeError(f"Delivery check failed: {name} did not reach Google Drive")
        docs = clip_documents(job_id, job, result, mapping, folder_ids, drive)
        for cid, doc in docs.items():
            store.write_clip(cid, doc)
        note = f"{len(docs)} clips ready" + ("" if result.get("gemini_enabled") else " (made without Gemini: local mode)")
        store.update_job(job_id, {"status": "done", "progress": 100, "stage": "done", "error": "", "note": note,
                                  "folderUrl": drive.folder_url(run_folder), "tail": progress.tail[-25:],
                                  "asrUsage": result.get("asr_usage", {})})
        return True
    except Exception as exc:
        traceback.print_exc()
        store.update_job(job_id, {"status": "failed", "stage": "failed", "error": scrub(plain_failure(exc))[:600],
                                  "tail": [scrub(l) for l in progress.tail[-25:]]})
        return False
    finally:
        shutil.rmtree(os.path.join(work_root, job_id), ignore_errors=True)


REQUIRED_SECRETS = ["FIREBASE_SERVICE_ACCOUNT", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_DRIVE_REFRESH_TOKEN"]


def check_setup(env):
    """Returns a plain-language list of problems with the GitHub secrets (empty list = fine)."""
    problems = []
    for name in REQUIRED_SECRETS:
        if not (env.get(name) or "").strip():
            problems.append(f"The GitHub secret {name} is empty. Add it in GitHub: Settings > Secrets and variables > Actions (SETUP_GUIDE.md, Part G).")
    raw = (env.get("FIREBASE_SERVICE_ACCOUNT") or "").strip()
    if raw:
        try:
            json.loads(raw)
        except ValueError:
            problems.append("The secret FIREBASE_SERVICE_ACCOUNT is not valid JSON. Paste the WHOLE text of the downloaded key file, from the first { to the last }.")
    return problems


def main(argv=None, env=None):
    argv = argv if argv is not None else sys.argv[1:]
    env = env if env is not None else os.environ
    mode = argv[0] if argv else "run"
    register_secrets_from_env(env)
    problems = check_setup(env)
    if problems:
        for line in problems:
            print("SETUP PROBLEM: " + line)
        return 2
    from store import FirestoreStore
    store = FirestoreStore(json.loads(env["FIREBASE_SERVICE_ACCOUNT"]))
    store.fail_stale_jobs()
    if mode == "check":
        return 0 if store.has_queued() else 10
    from drive_upload import DriveClient
    drive = DriveClient(env["GOOGLE_OAUTH_CLIENT_ID"], env["GOOGLE_OAUTH_CLIENT_SECRET"], env["GOOGLE_DRIVE_REFRESH_TOKEN"])
    job = store.claim_next_job(worker_id=env.get("GITHUB_RUN_ID", "local"))
    if not job:
        print("No queued job.")
        return 0
    work_root = env.get("ZIROQ_WORK_ROOT", "/tmp/ziroq_jobs")
    os.makedirs(work_root, exist_ok=True)
    ok = process_job(store, job, drive, env, work_root)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
