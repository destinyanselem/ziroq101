# CELL 4 — Authorized source input, metadata, cached download, and source probing
from urllib.parse import urlparse, parse_qs
import yt_dlp


def extract_video_id(url):
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    video_id = ""
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "music.youtube.com"):
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith(("/shorts/", "/embed/", "/live/")):
            parts = parsed.path.split("/")
            video_id = parts[2] if len(parts) > 2 else ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        raise ValueError("Enter one valid YouTube video URL (playlists are not supported).")
    return video_id


def probe_source_file(path):
    info = ffprobe_json(path)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not video:
        raise RuntimeError("Source has no video stream")
    if not audio:
        raise RuntimeError("Source has no audio stream; transcription requires audio")
    return float(info["format"]["duration"]), int(video["width"]), int(video["height"])


def find_downloaded_source(folder):
    candidates = [p for p in glob.glob(os.path.join(folder, "source.*"))
                  if not p.endswith((".part", ".ytdl", ".json", ".wav"))]
    return max(candidates, key=os.path.getsize) if candidates else None

print("Paste an authorized YouTube video URL OR a local video path uploaded to this Colab runtime.")
USER_INPUT = (os.environ.get("ZIROQ_URL", "").strip() or input("YouTube URL or local video path: ")).strip().strip("\"'")
if not USER_INPUT:
    raise ValueError("A YouTube URL or local video path is required")

SOURCE_META = {}
if os.path.isfile(USER_INPUT):
    video_path = os.path.abspath(USER_INPUT)
    source_title = Path(video_path).stem
    video_id = safe_filename(Path(video_path).stem, 28) + "_" + sha256_file(video_path)[:10]
    SOURCE_DIR = os.path.join(CACHE_ROOT, video_id)
    os.makedirs(SOURCE_DIR, exist_ok=True)
    log("source", "using supplied local media file")
else:
    video_id = extract_video_id(USER_INPUT)
    SOURCE_DIR = os.path.join(CACHE_ROOT, video_id)
    os.makedirs(SOURCE_DIR, exist_ok=True)
    video_path = find_downloaded_source(SOURCE_DIR)
    if video_path:
        log("download", f"reusing cached file {os.path.basename(video_path)}")
    else:
        log("metadata", "retrieving video metadata; no download started yet")
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True}) as ydl:
                meta = ydl.extract_info(USER_INPUT, download=False)
        except Exception as exc:
            raise RuntimeError(f"Could not access video metadata: {exc}. For an authorized copy, upload a local file.") from exc
        if meta.get("is_live"):
            raise RuntimeError("Live streams are not supported")
        if meta.get("availability") in ("needs_auth", "subscriber_only", "premium_only"):
            raise RuntimeError("This video requires authentication or membership. Access controls are not bypassed.")
        if meta.get("duration") and meta["duration"] > 4 * 3600:
            log("warning", "Very long source: use chunk checkpoints and expect Colab runtime limits")
        source_title = meta.get("title") or video_id
        SOURCE_META = {"channel": meta.get("channel") or meta.get("uploader") or "", "tags": list(meta.get("tags") or [])[:20],
                       "categories": list(meta.get("categories") or [])[:3], "description": str(meta.get("description") or "")[:600]}
        try:
            atomic_write_json(os.path.join(SOURCE_DIR, "source_meta.json"), SOURCE_META)
        except Exception:
            pass
        opts = {
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "outtmpl": os.path.join(SOURCE_DIR, "source.%(ext)s"),
            "format": "bv*+ba/b",
            "merge_output_format": "mp4", "retries": 5,
        }
        log("download", "downloading source with yt-dlp")
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([USER_INPUT])
        video_path = find_downloaded_source(SOURCE_DIR)
        if not video_path:
            raise RuntimeError("Download returned but no complete source media file was found")

source_duration, source_w, source_h = probe_source_file(video_path)
SOURCE = Source(video_id=video_id, title=source_title, duration=source_duration,
                video_path=video_path, width=source_w, height=source_h)
SOURCE_DIR = os.path.join(CACHE_ROOT, video_id)
if not SOURCE_META:
    try:
        SOURCE_META = json.load(open(os.path.join(SOURCE_DIR, "source_meta.json"), encoding="utf-8"))
    except Exception:
        SOURCE_META = {}
TRANSCRIPT_PATH = os.path.join(SOURCE_DIR, "transcript_full.json")
print(f"Source: {SOURCE.title}\nDuration: {SOURCE.duration/60:.1f} minutes | Resolution: {SOURCE.width}x{SOURCE.height}")
save_state("source_ready", source=asdict(SOURCE))
