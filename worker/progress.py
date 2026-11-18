"""Throttled, secret-scrubbed progress reporting. The worker pushes at most one update every `min_interval` seconds
(Firestore Spark allows 20,000 writes/day), always pushes stage changes, and never lets a secret reach a log tail."""
import re
import time

_KEY_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),             # Google API keys
    re.compile(r"gsk_[0-9A-Za-z]{20,}"),                 # Groq keys
    re.compile(r"gh[pousr]_[0-9A-Za-z]{20,}"),           # GitHub tokens
    re.compile(r"github_pat_[0-9A-Za-z_]{20,}"),
    re.compile(r"ya29\.[0-9A-Za-z_\-]{20,}"),            # Google OAuth access tokens
    re.compile(r"1//[0-9A-Za-z_\-]{20,}"),               # Google OAuth refresh tokens
    re.compile(r"(?i)(api[_-]?key|token|secret|authorization|cookie)(\s*[=:]\s*|\s+)(Bearer\s+)?[^\s,;'\"]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
]
_KNOWN_SECRETS = set()


def register_secret(value):
    value = str(value or "").strip()
    if len(value) >= 8:
        _KNOWN_SECRETS.add(value)


def register_secrets_from_env(env):
    for name, value in env.items():
        if any(w in name.upper() for w in ("KEY", "TOKEN", "SECRET", "COOKIE", "PASSWORD", "CREDENTIAL")):
            for part in re.split(r"[\s,;]+", str(value or "")):
                register_secret(part)


def scrub(text):
    text = str(text)
    for secret in sorted(_KNOWN_SECRETS, key=len, reverse=True):
        text = text.replace(secret, "[hidden]")
    for pat in _KEY_PATTERNS:
        text = pat.sub(lambda m: "[hidden]", text)
    return text


# stage -> (low, high) percent band for the progress bar
STAGE_BANDS = {
    "initialized": (1, 2), "gemini_ready": (3, 4), "source_ready": (6, 10), "transcribing": (10, 30), "transcribed": (30, 30),
    "content_map_ready": (32, 34), "five_story_plans_ready": (40, 45), "rendering": (50, 92),
    "delivered": (94, 94), "local_outputs_verified": (94, 94),
}


class Progress:
    """sink(update_dict) is called with {progress, stage, tail}. Keeps the last 25 scrubbed log lines."""

    def __init__(self, sink, min_interval=6.0, clock=time.monotonic, tail_len=25):
        self.sink, self.min_interval, self.clock, self.tail_len = sink, float(min_interval), clock, tail_len
        self.tail, self.percent, self.stage, self._last, self._dirty = [], 0, "starting", None, False

    def line(self, text):
        for piece in scrub(text).splitlines() or [""]:
            if piece.strip():
                self.tail.append(piece[:300])
        self.tail = self.tail[-self.tail_len:]
        self._dirty = True
        self._maybe_push()

    def state(self, stage, **kw):
        lo, hi = STAGE_BANDS.get(stage, (self.percent, self.percent))
        if stage == "transcribing" and kw.get("total_chunks"):
            pct = lo + (hi - lo) * kw.get("completed_chunks", 0) / max(1, kw["total_chunks"])
        elif stage == "rendering" and kw.get("total_clips"):
            pct = lo + (hi - lo) * kw.get("completed_clips", 0) / max(1, kw["total_clips"])
        else:
            pct = lo
        changed = stage != self.stage
        self.stage, self.percent = stage, max(self.percent, int(pct))     # a bar never goes backwards
        self._dirty = True
        self._maybe_push(force=changed)

    def _maybe_push(self, force=False):
        now = self.clock()
        if force or self._last is None or now - self._last >= self.min_interval:
            self.flush(now)

    def flush(self, now=None):
        if not self._dirty and self._last is not None:
            return
        self._last = self.clock() if now is None else now
        self._dirty = False
        try:
            self.sink({"progress": self.percent, "stage": self.stage, "tail": list(self.tail)})
        except Exception:
            pass                                    # progress must never crash a job
