import numpy as np, pytest
import groq_asr as G

class R:
    def __init__(self, code, payload=None, headers=None): self.status_code, self._p, self.headers = code, payload or {}, headers or {}
    def json(self): return self._p

OK = {"language": "en", "words": [{"word": " Hello", "start": 0.1, "end": 0.4}, {"word": "world", "start": 0.5, "end": 0.9, "probability": 0.8}]}

class Sess:
    def __init__(self, script): self.script, self.calls = list(script), []
    def post(self, url, headers=None, data=None, files=None, timeout=None):
        self.calls.append(headers["Authorization"]); s = self.script.pop(0)
        if isinstance(s, Exception): raise s
        return s

def client(script, keys=("k1", "k2")):
    t = [0.0]
    return G.GroqClient(list(keys), session=Sess(script), sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0]), t

def test_ok_words_and_language():
    c, _ = client([R(200, OK)])
    words, lang = c.transcribe_bytes(b"x", "a.wav")
    assert lang == "en" and words[0][0] == "Hello" and words[1][3] == 0.8

def test_429_rotates_key_then_succeeds():
    c, _ = client([R(429, headers={"retry-after": "3"}), R(200, OK)])
    c.transcribe_bytes(b"x", "a.wav")
    assert c.session.calls == ["Bearer k1", "Bearer k2"]

def test_401_retires_key():
    c, _ = client([R(401), R(200, OK)])
    c.transcribe_bytes(b"x", "a.wav")
    assert "k1" in c.retired and c.available

def test_all_401_raises():
    c, _ = client([R(401), R(401)])
    with pytest.raises(G.GroqError): c.transcribe_bytes(b"x", "a.wav")
    assert not c.available

def test_5xx_and_network_retry():
    c, _ = client([R(503), ConnectionError("boom"), R(200, OK)])
    assert c.transcribe_bytes(b"x", "a.wav")[1] == "en"

def test_gives_up_after_max_attempts():
    c, _ = client([R(500)] * 6)
    with pytest.raises(G.GroqError): c.transcribe_bytes(b"x", "a.wav")

def test_long_cooldown_is_not_waited_forever():
    c, _ = client([R(429, headers={"retry-after": "500"}), R(429, headers={"retry-after": "500"}), R(200, OK)], keys=("k1", "k2"))
    with pytest.raises(G.GroqError): c.transcribe_bytes(b"x", "a.wav")

def test_keys_from_env():
    assert G.load_groq_keys({"GROQ_API_KEY_1": "a", "GROQ_API_KEY_2": "b", "GROQ_API_KEYS": "b, c"}) == ["a", "b", "c"]
    assert G.load_groq_keys({}) == []

def test_hybrid_groq_then_fallback():
    audio = (np.random.rand(16000) * 0.1).astype("float32")
    c, _ = client([R(200, OK), R(500), R(500), R(500), R(500), R(500), R(500)], keys=("k1",))
    h = G.HybridASR(c, local_loader=lambda: "LOCAL")
    words, lang = h.words_for_chunk(audio)
    assert words and h.used["groq"] == 1
    words, lang = h.words_for_chunk(audio)               # Groq keeps failing -> None => caller uses local model
    assert words is None and h.fallback_reasons

def test_hybrid_without_key_uses_local():
    h = G.HybridASR(None, local_loader=lambda: "LOCAL")
    assert h.words_for_chunk(np.zeros(100, dtype="float32"))[0] is None

def test_large_chunk_goes_flac(monkeypatch):
    big = np.zeros(16000 * 60 * 14, dtype="float32")     # ~26.9 MB as 16-bit wav
    seen = {}
    class C:
        available = True
        def transcribe_bytes(self, payload, name, language=None):
            seen["name"], seen["size"] = name, len(payload); return [("a", 0, 1, .9)], "en"
    h = G.HybridASR(C(), local_loader=lambda: None)
    words, _ = h.words_for_chunk(big)
    assert seen["name"] == "chunk.flac" and seen["size"] < G.MAX_UPLOAD_BYTES and words
