# CELL 10c — FINAL CLIP REVIEWER: Gemini watches each finished clip, then approves it, asks for one bounded fix, or flags it
# (ChatGPT's free plan cannot watch video; Gemini can. The reviewer never blocks delivery: it approves, repairs once, or flags.)
import copy

REVIEW_ACTIONS = ["trim_start", "trim_end", "disable_broll", "force_full_frame", "lower_music", "remove_sfx"]
REVIEW_PROBLEMS = ["hook_weak", "starts_mid_thought", "ends_abruptly", "caption_error", "bad_crop", "music_too_loud", "dead_air",
                   "confusing_context", "drags_in_middle", "risky_or_misleading", "visual_issue", "audio_issue"]
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "fix", "reject"]},
        "score": {"type": "number"},
        "hook": {"type": "number"}, "clarity": {"type": "number"}, "pacing": {"type": "number"}, "polish": {"type": "number"},
        "summary": {"type": "string"},
        "problems": {"type": "array", "items": {"type": "object", "properties": {
            "type": {"type": "string", "enum": REVIEW_PROBLEMS}, "at": {"type": "string"}, "detail": {"type": "string"}},
            "required": ["type", "detail"]}},
        "directives": {"type": "array", "items": {"type": "object", "properties": {
            "action": {"type": "string", "enum": REVIEW_ACTIONS}, "sentences": {"type": "integer"}, "reason": {"type": "string"}},
            "required": ["action", "reason"]}},
    },
    "required": ["verdict", "score", "summary", "problems", "directives"],
}

REVIEW_PROMPT = """You are the final quality reviewer for a short-form video studio. WATCH this finished vertical clip (captions are burned in) and decide
whether it is good enough to post on YouTube Shorts / TikTok / Instagram Reels.
Judge, scoring each 0-10: hook (do the first 3 seconds make a stranger stay?), clarity (does it make sense without the full video?),
pacing (any dead air, repetition, or a slow middle?), polish (captions readable and correct, speaker framed well, music not covering speech, clean start and end).
Be strict and honest: 8+ means you would post it as is; 6-7.9 is acceptable with small issues; below 6 is weak.
{long_note}
Verdict rules: "approve" if score >= {min_score} and nothing needs fixing. "fix" only if a SMALL change from the allowed list would clearly help.
"reject" only for serious problems a trim cannot fix (misleading, risky, confusing, broken).
Allowed directives (use none if not needed, at most 3): trim_start (drop 1-2 opening sentences; give sentences=1 or 2), trim_end (drop 1-2 closing sentences),
disable_broll (stock footage hurts the clip), force_full_frame (the speaker crop is wrong; show the whole frame), lower_music, remove_sfx.
Report real problems with the time ("0:42") when you can. Do not invent problems to look useful. Clip title: {title}. Length: {duration:.0f} seconds.
What the speaker says (transcript): {transcript}"""


def _validate_review(d):
    errs = []
    if not isinstance(d.get("score"), (int, float)) or not (0 <= float(d["score"]) <= 10):
        errs.append("score must be a number from 0 to 10")
    for dd in d.get("directives") or []:
        if dd.get("action") not in REVIEW_ACTIONS:
            errs.append(f"unknown directive action {dd.get('action')!r}")
    return errs


def _clean_review(d, reviewer="gemini"):
    score = round(max(0.0, min(10.0, float(d.get("score", 0)))), 1)
    directives = [{"action": x["action"], "sentences": max(1, min(2, int(x.get("sentences") or 1))), "reason": str(x.get("reason", ""))[:160]}
                  for x in (d.get("directives") or []) if x.get("action") in REVIEW_ACTIONS][:3]
    verdict = d.get("verdict") if d.get("verdict") in ("approve", "fix", "reject") else "approve"
    if verdict == "fix" and not directives:
        verdict = "approve" if score >= float(CFG.get("review_min_score", 6.0)) else "reject"
    return {"verdict": verdict, "score": score, "reviewer": reviewer, "summary": str(d.get("summary", ""))[:300],
            "scores": {k: round(float(d.get(k, 0) or 0), 1) for k in ("hook", "clarity", "pacing", "polish")},
            "problems": [{"type": p.get("type"), "at": str(p.get("at", ""))[:12], "detail": str(p.get("detail", ""))[:200]} for p in (d.get("problems") or [])][:8],
            "directives": directives}


def local_review(reason="Gemini unavailable"):
    return {"verdict": "approve", "score": None, "reviewer": "local (technical QA only)", "summary": f"{reason}; the clip passed the technical and editorial gates but was not watched by an AI reviewer.",
            "scores": {}, "problems": [], "directives": []}


def _review_proxy(path):
    out = os.path.splitext(path)[0] + "_review_proxy.mp4"
    run(["ffmpeg", "-y", "-i", path, "-vf", "scale=-2:540", "-r", "24", "-c:v", "libx264", "-preset", "veryfast", "-crf", "33",
         "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", out], quiet=True)
    return out


def review_final_clip(plan, final_path, idx, round_no=1):
    pool = globals().get("GEMINI")
    if pool is None or not getattr(pool, "enabled", False) or not CFG.get("enable_final_review", True):
        return local_review("The final review is off or Gemini is unavailable")
    uploaded = None
    try:
        proxy = _review_proxy(final_path)
        uploaded = pool.upload_file(proxy, poll_timeout=90)
        media = {"type": "video", "uri": uploaded["uri"], "mime_type": uploaded["mime_type"]}
        long_note = ("This is a LONG clip. Check that it holds attention for its whole length; name any dead zones with times. If it drags, use trim_end or trim_start."
                     if plan.duration > float(CFG.get("long_clip_threshold_sec", 90.0)) else "")
        prompt = REVIEW_PROMPT.format(long_note=long_note, min_score=CFG.get("review_min_score", 6.0), title=plan.title, duration=plan.duration,
                                      transcript=" ".join(s.transcript for s in plan.segments)[:5000])
        res = gemini_structured(f"final_review_{RUN_ID}_{idx}_r{round_no}", prompt, REVIEW_SCHEMA, tier="reasoning", media=media, pin=uploaded["label"],
                                validator=_validate_review, max_repairs=1, temperature=0.2, cache_media={"sha": sha256_file(proxy), "round": round_no})
        if not res.get("ok"):
            return local_review(f"Gemini review unavailable ({res.get('error_kind')})")
        return _clean_review(res["data"])
    except Exception as exc:
        return local_review(f"Gemini review failed ({type(exc).__name__})")
    finally:
        try:
            if uploaded:
                pool.delete_file(uploaded)
        except Exception:
            pass


def apply_review_directives(plan, directives, transcript, source):
    """Bounded edits only. Returns a list of what was applied; skips anything that cannot be done safely."""
    applied = []
    sents = transcript.sentences
    for d in directives:
        act, k = d["action"], int(d.get("sentences") or 1)
        if act == "disable_broll":
            plan.notes["disable_broll"] = True; applied.append("removed stock footage")
        elif act == "force_full_frame":
            plan.notes["gemini_framing"] = {"layout": "slides_or_screen", "points": [], "speakers_x": []}; applied.append("switched to full-frame layout")
        elif act == "lower_music":
            plan.notes["music_volume"] = 0.10; applied.append("lowered the music")
        elif act == "remove_sfx":
            plan.notes["no_sfx"] = True; applied.append("removed sound effects")
        elif act == "trim_end":
            seg = plan.segments[-1]
            r = _sentence_range(transcript, seg.source_start, seg.source_end)
            if r:
                si, ei = r
                for _ in range(k):
                    if ei > si and plan.duration - (seg.source_end - (sents[ei - 1].end + 0.10)) >= 20.0:
                        ei -= 1
                        _set_bounds(seg, transcript, si, ei, source.duration)
                if ei != r[1]:
                    applied.append(f"trimmed the ending by {r[1] - ei} sentence(s)")
        elif act == "trim_start":
            seg = plan.segments[0]
            r = _sentence_range(transcript, seg.source_start, seg.source_end)
            if r:
                si, ei = r
                for _ in range(k):
                    if si < ei and plan.duration - (sents[si + 1].start - seg.source_start) >= 20.0:
                        si += 1
                        _set_bounds(seg, transcript, si, ei, source.duration)
                if si != r[0]:
                    applied.append(f"trimmed the opening by {si - r[0]} sentence(s)")
    if applied:
        plan.validate(source.duration)
    return applied


def run_final_review(plan, idx, final_path, rebuild, review_fn=None, editorial_fn=None):
    """Review the finished clip. If a fix is requested: apply it, re-render through the same gates, re-review, and KEEP it only if
    it scored at least as well; otherwise restore the original. Returns {"review": {...}, optional new render_info/technical_checks/editorial_checks}."""
    review_fn = review_fn or review_final_clip
    editorial_fn = editorial_fn or (lambda p: run_editorial_qa(p, SOURCE, TRANSCRIPT))
    first = review_fn(plan, final_path, idx, 1)
    out = {"review": dict(first, rounds=1, applied=[])}
    if first["verdict"] != "fix" or int(CFG.get("review_max_fix_rounds", 1)) < 1:
        return out
    backup_segments, backup_notes = copy.deepcopy(plan.segments), copy.deepcopy(plan.notes)
    backup_file = final_path + ".before_review.mp4"
    shutil.copy2(final_path, backup_file)

    def restore(why):
        plan.segments, plan.notes = backup_segments, backup_notes
        shutil.copy2(backup_file, final_path)
        out["review"].update(rounds=2, applied=[], summary=(first["summary"] + f" (An automatic fix was tried but not kept: {why}.)")[:300])
        log("review", f"clip {idx}: fix not kept ({why})")
    try:
        applied = apply_review_directives(plan, first["directives"], TRANSCRIPT, SOURCE)
        if not applied:
            restore("nothing could be applied safely")
        else:
            checks = editorial_fn(plan)
            if not all(c["passed"] for c in checks):
                restore("the edited clip failed editorial QA")
            else:
                new_render, new_tech = rebuild(plan)
                second = review_fn(plan, final_path, idx, 2)
                s1, s2 = first.get("score"), second.get("score")
                if s1 is not None and s2 is not None and s2 < s1:
                    restore(f"the edit scored lower ({s2} vs {s1})")
                else:
                    log("review", f"clip {idx}: fix applied ({'; '.join(applied)}); score {s1} -> {s2}")
                    out.update(render_info=new_render, technical_checks=new_tech, editorial_checks=checks)
                    out["review"] = dict(second, rounds=2, applied=applied, first_score=s1)
                    if second["verdict"] == "fix":
                        out["review"]["verdict"] = "approve" if (second.get("score") or 0) >= float(CFG.get("review_min_score", 6.0)) else "reject"
    except Exception as exc:
        restore(f"{type(exc).__name__}")
    finally:
        try:
            os.remove(backup_file)
        except OSError:
            pass
    return out


print("Final reviewer ready: Gemini watches each finished clip, can request one bounded fix, and never blocks delivery.")
