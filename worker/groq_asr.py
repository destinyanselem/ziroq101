"""Groq hosted Whisper backend for the engine's chunked transcription, with local faster-whisper as fallback.

Plugs into the engine's two seams: load_whisper_model(device, model_name) and
transcribe_chunk(model, audio_array, offset_sec, keep_start, keep_end) -> (words, language).
Behaviour (all covered by tests with a fake HTTP session):
  * keys GROQ_API_KEY_1..N / GROQ_API_KEY(S) are rotated; a key that returns 401 is retired, a 429 puts it on cooldown
  * 5xx / network errors are retried with backoff; Retry-After is honoured but capped
  * audio above ~24 MB is compressed to FLAC before upload (the hosted limit is about 25 MB)
  * any unrecoverable failure falls back to local faster-whisper for that chunk (never blocks the job)
"""
import io
import os
import re
import subprocess
import tempfile
import time
import wave

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"
MAX_UPLOAD_BYTES = 24 * 1024 * 1024


class GroqError(Exception):
    pass


def load_groq_keys(env=None):
    env = os.environ if env is None else env
    found = []
    names = [f"GROQ_API_KEY_{i}" for i in range(1, 9)] + ["GROQ_API_KEY"]
    for n in names:
        v = str(env.get(n, "")).strip().strip("\"'")
        if v:
            found.append(v)
    for part in re.split(r"[,\s;]+", str(env.get("GROQ_API_KEYS", ""))):
        if part.strip():
            found.append(part.strip())
    seen, out = set(), []
    for k in found:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def pcm_to_wav_bytes(audio_array):
    import numpy as np
    pcm = (np.clip(audio_array, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm)
    return buf.getvalue()


def to_flac_bytes(wav_bytes):
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "in.wav"), os.path.join(d, "out.flac")
        with open(src, "wb") as f:
            f.write(wav_bytes)
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", "16000",
                        "-c:a", "flac", "-compression_level", "8", dst], check=True, capture_output=True, timeout=300)
        with open(dst, "rb") as f:
            return f.read()


class GroqClient:
    def __init__(self, keys, session=None, sleep=time.sleep, clock=time.monotonic, max_attempts=6, max_wait=60.0):
        import requests
        self.keys = list(keys)
        self.session = session or requests.Session()
        self.sleep, self.clock = sleep, clock
        self.retired = set()
        self.cooldown_until = {}
        self.max_attempts, self.max_wait = max_attempts, max_wait
        self.cursor = 0
        self.disabled_reason = "" if self.keys else "no Groq key configured"

    @property
    def available(self):
        return any(k not in self.retired for k in self.keys) and not self.disabled_reason

    def _next_key(self):
        live = [k for k in self.keys if k not in self.retired]
        if not live:
            raise GroqError("all Groq keys were rejected (401)")
        now = self.clock()
        for _ in range(len(live)):
            k = live[self.cursor % len(live)]
            self.cursor += 1
            if self.cooldown_until.get(k, 0) <= now:
                return k, 0.0
        soonest = min(self.cooldown_until.get(k, 0) for k in live) - now
        return live[self.cursor % len(live)], max(0.0, soonest)

    def transcribe_bytes(self, audio_bytes, filename, language=None):
        """Returns (words, language) with words = [(text, start, end, prob)] on the chunk's own timeline."""
        last = "no attempt made"
        for attempt in range(1, self.max_attempts + 1):
            key, wait = self._next_key()
            if wait > self.max_wait:
                raise GroqError(f"all Groq keys are rate-limited for {int(wait)} s")
            if wait > 0:
                self.sleep(min(wait, self.max_wait))
            data = {"model": GROQ_MODEL, "response_format": "verbose_json", "timestamp_granularities[]": "word", "temperature": "0"}
            if language:
                data["language"] = language
            try:
                resp = self.session.post(GROQ_URL, headers={"Authorization": f"Bearer {key}"}, data=data,
                                         files={"file": (filename, audio_bytes)}, timeout=180)
            except Exception as exc:
                last = f"network error {type(exc).__name__}"
                self.sleep(min(2 ** attempt, 20))
                continue
            code = getattr(resp, "status_code", 0)
            if code == 200:
                return self._parse(resp.json())
            if code == 401:
                self.retired.add(key)
                last = "401 key rejected"
                continue
            if code == 429:
                ra = 0.0
                try:
                    ra = float((getattr(resp, "headers", {}) or {}).get("retry-after", 0) or 0)
                except Exception:
                    pass
                self.cooldown_until[key] = self.clock() + max(ra, 5.0)
                last = "429 rate limited"
                continue
            if code == 413:
                raise GroqError("413 file too large for Groq")
            if code >= 500:
                last = f"{code} server error"
                self.sleep(min(2 ** attempt, 20))
                continue
            raise GroqError(f"Groq returned {code}")
        raise GroqError(f"Groq failed after {self.max_attempts} attempts ({last})")

    @staticmethod
    def _parse(payload):
        words = []
        for w in payload.get("words") or []:
            text = str(w.get("word", "")).strip()
            if text and "start" in w and "end" in w:
                words.append((text, float(w["start"]), float(w["end"]), float(w.get("probability", 0.9))))
        return words, str(payload.get("language") or "unknown")


class HybridASR:
    """What the engine's load_whisper_model() returns in the worker."""

    def __init__(self, groq, local_loader):
        self.groq, self.local_loader, self._local = groq, local_loader, None
        self.used = {"groq": 0, "local": 0}
        self.fallback_reasons = []

    def local_model(self):
        if self._local is None:
            self._local = self.local_loader()
        return self._local

    def words_for_chunk(self, audio_array, language=None):
        """Groq first. Returns (words_or_None, language). None means: use the local model."""
        if self.groq is None or not self.groq.available:
            return None, "unknown"
        try:
            wav = pcm_to_wav_bytes(audio_array)
            payload, name = (wav, "chunk.wav")
            if len(wav) > MAX_UPLOAD_BYTES:
                payload, name = to_flac_bytes(wav), "chunk.flac"
            if len(payload) > MAX_UPLOAD_BYTES:
                raise GroqError("chunk still above the upload limit after FLAC compression")
            words, lang = self.groq.transcribe_bytes(payload, name, language)
            self.used["groq"] += 1
            return words, lang
        except Exception as exc:
            self.fallback_reasons.append(f"{type(exc).__name__}: {str(exc)[:120]}")
            return None, "unknown"
