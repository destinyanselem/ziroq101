"""YouTube URL validation + yt-dlp option helper (cookies from a secret, never logged)."""
import os
import re
import tempfile
from urllib.parse import parse_qs, urlparse

PLAIN_ERRORS = {
    "sign in to confirm": "YouTube blocked the download from the cloud server (it asked the server to prove it is not a bot). "
                          "Refresh the cookies secret: see SETUP GUIDE, section 'Refresh YouTube cookies'.",
    "not a bot": "YouTube blocked the download from the cloud server. Refresh the cookies secret (SETUP GUIDE, 'Refresh YouTube cookies').",
    "private video": "This video is private, so it cannot be downloaded.",
    "members-only": "This video is for channel members only. ZIROQ does not bypass access controls.",
    "video unavailable": "This video is unavailable (removed, region-blocked or private).",
    "live event": "Live streams are not supported. Try again after the stream has ended.",
    "http error 429": "YouTube is rate-limiting this server right now. Try again in an hour.",
}


def validate_youtube_url(url):
    """Returns (video_id, canonical_url). Rejects local paths, playlists, channels and non-YouTube links."""
    text = str(url or "").strip().strip("\"'")
    if not text or len(text) > 300 or any(c in text for c in "\n\r\t "):
        raise ValueError("Enter one YouTube video link.")
    if re.match(r"^(file:|/|\.|~|[A-Za-z]:\\)", text) or "\\" in text:
        raise ValueError("Only YouTube links are accepted (not file paths).")
    if not re.match(r"^https?://", text):
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http/https YouTube links are accepted.")
    host = (parsed.hostname or "").lower()
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
        elif parsed.path.startswith("/playlist"):
            raise ValueError("Playlists are not supported. Paste one video link.")
    else:
        raise ValueError("Only youtube.com and youtu.be links are accepted.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        raise ValueError("That does not look like a single YouTube video link.")
    return video_id, f"https://www.youtube.com/watch?v={video_id}"


def plain_download_error(message):
    low = str(message).lower()
    for needle, text in PLAIN_ERRORS.items():
        if needle in low:
            return text
    return "The download failed: " + str(message)[:240]


def write_cookie_file(cookies_text, directory=None):
    """Writes the secret cookies to a private temp file (mode 600). Returns the path or None. Content is never logged."""
    text = (cookies_text or "").strip()
    if not text:
        return None
    if "\t" not in text and "# Netscape" not in text:
        raise ValueError("The cookies secret is not in cookies.txt (Netscape) format.")
    fd, path = tempfile.mkstemp(prefix="yt_cookies_", suffix=".txt", dir=directory)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    os.chmod(path, 0o600)
    return path


def ydl_extra_opts(env=None):
    """Extra yt-dlp options for the worker: cookies if present, and a JS runtime hint when deno is installed."""
    env = os.environ if env is None else env
    opts = {}
    cookie_path = env.get("ZIROQ_COOKIE_FILE", "")
    if cookie_path and os.path.isfile(cookie_path):
        opts["cookiefile"] = cookie_path
    import shutil
    if shutil.which("deno"):
        opts["js_runtimes"] = {"deno": {}}
    opts["socket_timeout"] = 30
    return opts
