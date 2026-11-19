import pytest
from ytdlp_opts import (YOUTUBE_FORMAT_SELECTOR, validate_youtube_url,
                        plain_download_error, write_cookie_file, ydl_extra_opts)
from progress import Progress, scrub, register_secret

@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtu.be/dQw4w9WgXcQ", "https://m.youtube.com/shorts/dQw4w9WgXcQ",
                                 "https://youtube.com/watch?v=dQw4w9WgXcQ&list=PL123"])
def test_valid(url):
    vid, canon = validate_youtube_url(url)
    assert vid == "dQw4w9WgXcQ" and canon.endswith(vid)

@pytest.mark.parametrize("url", ["/etc/passwd", "C:\\video.mp4", "~/x.mp4", "file:///x.mp4", "https://vimeo.com/123456789",
                                 "https://www.youtube.com/playlist?list=PL1", "https://www.youtube.com/@channel", "https://youtube.com/watch?v=short",
                                 "", "https://evil.com/?u=youtube.com/watch?v=dQw4w9WgXcQ", "https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ", "a b"])
def test_rejected(url):
    with pytest.raises(ValueError):
        validate_youtube_url(url)

def test_plain_errors():
    assert "cookies" in plain_download_error("ERROR: Sign in to confirm you're not a bot").lower()
    assert "private" in plain_download_error("Private video").lower()
    assert "downloadable formats" in plain_download_error("Requested format is not available").lower()

def test_cookie_file(tmp_path):
    assert write_cookie_file("", str(tmp_path)) is None
    with pytest.raises(ValueError):
        write_cookie_file("garbage", str(tmp_path))
    p = write_cookie_file("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tA\tB", str(tmp_path))
    import os, stat
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600

def test_scrub():
    register_secret("supersecretvalue123")
    out = scrub("key AIzaSyA1234567890abcdefghijklmnop and supersecretvalue123 and gsk_abcdefghijklmnopqrstuv and Authorization: Bearer abcdefgh12345 ghp_abcdefghijklmnopqrstuvwx")
    for bad in ("AIzaSy", "supersecret", "gsk_abc", "abcdefgh12345", "ghp_abc"):
        assert bad not in out

def test_progress_throttle_and_monotonic():
    now = [0.0]; pushes = []
    p = Progress(pushes.append, min_interval=6.0, clock=lambda: now[0])
    p.state("initialized")                      # stage change -> push
    for i in range(20):
        now[0] += 0.1; p.line(f"line {i}")      # within 6 s -> no extra push
    assert len(pushes) == 1
    now[0] += 7; p.line("later")
    assert len(pushes) == 2 and len(pushes[-1]["tail"]) <= 25
    p.state("transcribing", completed_chunks=1, total_chunks=2); hi = p.percent
    p.state("source_ready")                     # earlier stage must not move the bar backwards
    assert p.percent >= hi

def test_progress_sink_failure_never_raises():
    def bad(_): raise RuntimeError("firestore down")
    p = Progress(bad, min_interval=0); p.line("x"); p.state("rendering", completed_clips=1, total_clips=5)

def test_progress_scrubs_tail():
    pushes = []; p = Progress(pushes.append, min_interval=0)
    p.line("using AIzaSyA1234567890abcdefghijklmnop now")
    assert "AIza" not in str(pushes[-1])


def test_ydl_extra_opts_keep_mweb_and_add_notebook_fallback_clients():
    opts = ydl_extra_opts({})
    assert opts["extractor_args"]["youtube"]["player_client"] == ["mweb", "tv", "web_safari"]
    assert opts["extractor_args"]["youtubepot-bgutilhttp"]["base_url"] == "http://127.0.0.1:4416"
    assert opts["socket_timeout"] == 30


def test_youtube_format_selector_has_progressive_fallback():
    assert YOUTUBE_FORMAT_SELECTOR == "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
