# CELL 3c — Gemini core tests (fake client; makes NO network calls and uses NO real keys)
def run_gemini_core_tests():
    import types, tempfile
    global GEM_SLEEP, GEM_NOW, GEMINI
    real_sleep, real_now, real_gemini = GEM_SLEEP, GEM_NOW, globals().get("GEMINI")
    old_cfg = {k: CFG.get(k) for k in ("gemini_min_interval_sec", "gemini_max_requests_per_run", "gemini_max_wait_sec", "gemini_call_budget_sec")}
    clock = {"t": 1_800_000_000.0}
    slept = []
    GEM_NOW = lambda: clock["t"]
    def fake_sleep(s):
        slept.append(s); clock["t"] += s
    GEM_SLEEP = fake_sleep
    CFG.update(gemini_min_interval_sec=0.0, gemini_max_requests_per_run=50, gemini_max_wait_sec=120)
    tmp = tempfile.mkdtemp()
    global GEMINI_CACHE_DIR
    old_cache = GEMINI_CACHE_DIR
    GEMINI_CACHE_DIR = os.path.join(tmp, "cache")

    class Err(Exception):
        def __init__(self, msg, code=None):
            super().__init__(msg); self.code = code

    class Fake:
        """script: dict key -> list of outcomes (str = text, Exception = raise); last item repeats."""
        def __init__(self, key, script, log_):
            self.key, self.script, self.log_ = key, script, log_
            self.interactions = types.SimpleNamespace(create=self.create)
            self.models = types.SimpleNamespace(list=lambda: iter([1]))
        def create(self, **kw):
            self.log_.append((self.key, kw["model"], kw.get("store")))
            seq = self.script.get(self.key, ['{"ok": true}'])
            item = seq.pop(0) if len(seq) > 1 else seq[0]
            if isinstance(item, Exception):
                raise item
            return types.SimpleNamespace(output_text=item)

    def make(keys, script, projects=None):
        calls = []
        pool = GeminiPool([(f"key{i}", k) for i, k in enumerate(keys, 1)], lambda k: Fake(k, script, calls), None,
                          projects or {f"key{i}": f"P{i}" for i in range(1, len(keys) + 1)})
        return pool, calls

    SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    FAKEKEY1, FAKEKEY2, FAKEKEY3 = "AIza" + "A" * 35, "AIza" + "B" * 35, "AIza" + "C" * 35
    try:
        # 1. error classification
        assert classify_gemini_error(Err("API key not valid. Please pass a valid API key", 400))["kind"] == "auth"
        assert classify_gemini_error(Err("RESOURCE_EXHAUSTED retry in 17s", 429))["retry_after"] == 17.0
        assert classify_gemini_error(Err("429 quota exceeded for metric per day", 429))["kind"] == "quota_daily"
        assert classify_gemini_error(Err("quota exceeded, limit: 0", 429))["kind"] == "quota_zero"
        assert classify_gemini_error(Err("model not found", 404))["kind"] == "model_unavailable"
        assert classify_gemini_error(Err("boom", 503))["kind"] == "transient"
        assert classify_gemini_error(TimeoutError("x"))["kind"] == "timeout"
        assert classify_gemini_error(Err("bad schema", 400))["kind"] == "bad_request"

        # 2. scrubbing: a key never survives gem_scrub, even inside exception text
        GeminiPool([("key1", FAKEKEY1)], lambda k: None, None, {})
        assert FAKEKEY1 not in gem_scrub(f"failed with {FAKEKEY1} and AIza{'Z'*30}")

        # 3. no keys / disabled -> graceful fallback result, never an exception
        GEMINI = GeminiPool([], None, None)
        r = gemini_structured("t_nokeys", "x", SCHEMA)
        assert not r["ok"] and r["fallback_required"] and r["error_kind"] == "disabled"

        # 4. success + store=False + cache hit performs no second request
        pool, calls = make([FAKEKEY1], {FAKEKEY1: ['{"n": 3}']})
        GEMINI = pool
        r = gemini_structured("t_ok", "hello", SCHEMA)
        assert r["ok"] and r["data"] == {"n": 3} and calls[0][2] is False
        r2 = gemini_structured("t_ok", "hello", SCHEMA)
        assert r2["ok"] and r2["source"] == "cache" and len(calls) == 1

        # 5. invalid key fails over to the next key; the bad key is excluded afterwards
        pool, calls = make([FAKEKEY1, FAKEKEY2], {FAKEKEY1: [Err("API key not valid", 400)], FAKEKEY2: ['{"n": 1}']})
        GEMINI = pool
        r = gemini_structured("t_auth", "p1", SCHEMA)
        assert r["ok"] and pool.entries[0]["auth_failed"] and not pool.entries[1]["auth_failed"]
        gemini_structured("t_auth2", "p2", SCHEMA)
        assert [c[0] for c in calls].count(FAKEKEY1) == 1

        # 6. rate limit: cooldown is per PROJECT, so a second key of the SAME project is not used to bypass it,
        #    while a key from a DIFFERENT project serves immediately.
        same = {"key1": "PA", "key2": "PA", "key3": "PB"}
        pool, calls = make([FAKEKEY1, FAKEKEY2, FAKEKEY3],
                           {FAKEKEY1: [Err("429 RESOURCE_EXHAUSTED retry in 40s", 429)], FAKEKEY2: ['{"n": 2}'], FAKEKEY3: ['{"n": 9}']}, same)
        GEMINI = pool
        r = gemini_structured("t_rl", "p3", SCHEMA)
        assert r["ok"] and r["project"] == "PB" and r["data"] == {"n": 9}
        assert all(c[0] != FAKEKEY2 for c in calls)

        # 7. all projects rate limited -> waits (simulated clock) within max_wait, then succeeds
        pool, calls = make([FAKEKEY1], {FAKEKEY1: [Err("RESOURCE_EXHAUSTED retry in 20s", 429), '{"n": 5}']})
        GEMINI = pool
        r = gemini_structured("t_wait", "p4", SCHEMA)
        assert r["ok"] and sum(slept) >= 20

        # 8. wait too long -> fallback_required with waiting_for_quota
        CFG["gemini_max_wait_sec"] = 5
        pool, calls = make([FAKEKEY1], {FAKEKEY1: [Err("RESOURCE_EXHAUSTED retry in 90s", 429)]})
        GEMINI = pool
        r = gemini_structured("t_wait2", "p5", SCHEMA)
        assert not r["ok"] and r["fallback_required"] and r["error_kind"] == "waiting_for_quota"
        CFG["gemini_max_wait_sec"] = 120

        # 9. daily quota blocks until Pacific midnight (never retried before then); quota_zero model falls through
        pool, calls = make([FAKEKEY1], {FAKEKEY1: [Err("quota exceeded per day", 429)]})
        GEMINI = pool
        r = gemini_structured("t_day", "p6", SCHEMA)
        assert not r["ok"] and pool.cooldowns
        pool, calls = make([FAKEKEY1], {FAKEKEY1: [Err("quota exceeded, limit: 0", 429), '{"n": 4}']})
        GEMINI = pool
        r = gemini_structured("t_zero", "p7", SCHEMA)
        assert r["ok"] and len({c[1] for c in calls}) == 2, calls   # first model blocked, second model served

        # 10. malformed JSON -> one repair attempt succeeds; persistent garbage -> invalid_output fallback
        pool, calls = make([FAKEKEY1], {FAKEKEY1: ["not json at all", '{"n": 7}']})
        GEMINI = pool
        r = gemini_structured("t_rep", "p8", SCHEMA)
        assert r["ok"] and r["data"] == {"n": 7} and len(calls) == 2
        pool, calls = make([FAKEKEY1], {FAKEKEY1: ['{"n": "wrong"}']})
        GEMINI = pool
        r = gemini_structured("t_bad", "p9", SCHEMA)
        assert not r["ok"] and r["error_kind"] == "invalid_output" and r["fallback_required"]

        # 11. semantic validator (e.g. hallucinated sentence ids) triggers repair, then rejection
        pool, calls = make([FAKEKEY1], {FAKEKEY1: ['{"n": 99}', '{"n": 1}']})
        GEMINI = pool
        r = gemini_structured("t_val", "p10", SCHEMA, validator=lambda d: [] if d["n"] < 10 else ["n refers to a non-existent id"])
        assert r["ok"] and r["data"]["n"] == 1

        # 12. transient + timeout: exponential backoff then success; no key is permanently disabled
        pool, calls = make([FAKEKEY1], {FAKEKEY1: [Err("503 unavailable", 503), TimeoutError("timed out"), '{"n": 6}']})
        GEMINI = pool
        r = gemini_structured("t_tr", "p11", SCHEMA)
        assert r["ok"] and not pool.entries[0]["auth_failed"]

        # 13. bad request is not retried or rotated
        pool, calls = make([FAKEKEY1, FAKEKEY2], {FAKEKEY1: [Err("invalid_argument: schema", 400)]})
        GEMINI = pool
        r = gemini_structured("t_req", "p12", SCHEMA)
        assert not r["ok"] and r["error_kind"] == "request" and len(calls) == 1

        # 14. per-run request budget is a hard stop (cost guard)
        CFG["gemini_max_requests_per_run"] = 2
        pool, calls = make([FAKEKEY1], {FAKEKEY1: [Err("503", 503)]})
        GEMINI = pool
        r = gemini_structured("t_bud", "p13", SCHEMA)
        assert not r["ok"] and len(calls) <= 2
        CFG["gemini_max_requests_per_run"] = 50

        # 15. persisted state never contains a key, only fingerprints; and reloads auth failures
        sp = os.path.join(tmp, "state", "g.json")
        pool = GeminiPool([("key1", FAKEKEY1)], lambda k: Fake(k, {FAKEKEY1: [Err("API key not valid", 400)]}, []), sp, {"key1": "P1"})
        GEMINI = pool
        gemini_structured("t_state", "p14", SCHEMA)
        raw = open(sp).read()
        assert FAKEKEY1 not in raw and "AIza" not in raw
        pool2 = GeminiPool([("key1", FAKEKEY1)], lambda k: None, sp, {"key1": "P1"})
        assert pool2.entries[0]["auth_failed"]

        # 15b. file upload is pinned to the uploading key; polling until ACTIVE; delete
        pool, calls = make([FAKEKEY1, FAKEKEY2], {FAKEKEY1: ['{"n": 1}'], FAKEKEY2: ['{"n": 2}']})
        c1 = pool._client(pool.entries[0])
        st = {"polls": 0}
        mk = lambda s: types.SimpleNamespace(name="files/abc", uri="https://x/files/abc", mime_type="video/mp4", state=types.SimpleNamespace(name=s))
        def _get(name):
            st["polls"] += 1; return mk("ACTIVE")
        c1.files = types.SimpleNamespace(upload=lambda file: mk("PROCESSING"), get=_get, delete=lambda name: st.update(deleted=name))
        info = pool.upload_file("x.mp4")
        assert info["label"] == "key1" and st["polls"] >= 1
        GEMINI = pool
        r = gemini_structured("t_pin", "pp", SCHEMA, pin=info["label"], media={"type": "video", "uri": info["uri"]}, cache_media={"local": "v"})
        assert r["ok"] and calls[-1][0] == FAKEKEY1
        pool.delete_file(info); assert st["deleted"] == "files/abc"

        # 15c. a hanging Gemini call is abandoned at the deadline; two timeouts open a breaker so later calls fail instantly
        import time as _t
        class Hang:
            def __init__(self, key): self.interactions = types.SimpleNamespace(create=self.create); self.models = types.SimpleNamespace(list=lambda: iter([1]))
            def create(self, **kw): _t.sleep(1.5); return types.SimpleNamespace(output_text='{"n": 1}')
        CFG["gemini_call_budget_sec"] = 0.6
        pool = GeminiPool([("key1", FAKEKEY1)], lambda k: Hang(k), None, {"key1": "P1"})
        GEMINI = pool
        t0 = _t.time(); r = gemini_structured("t_hang", "p_hang", SCHEMA); took = _t.time() - t0
        assert not r["ok"] and r["fallback_required"] and took < 2.9, (r, took)
        t0 = _t.time(); r2 = gemini_structured("t_hang2", "p_hang2", SCHEMA)
        assert not r2["ok"] and _t.time() - t0 < 0.3     # breaker: no second wait
        CFG["gemini_call_budget_sec"] = 120

        # 16. stage tracker + schema validator
        stage_set("unit_test_stage", "completed_using_fallback", decided_by="local")
        assert STAGES["unit_test_stage"]["status"] == "completed_using_fallback"
        STAGES.pop("unit_test_stage", None)
        assert validate_json_schema({"a": [1, "x"]}, {"type": "object", "properties": {"a": {"type": "array", "items": {"type": "integer"}}}})
        assert not validate_json_schema({"a": [1, 2]}, {"type": "object", "properties": {"a": {"type": "array", "items": {"type": "integer"}}}})
        return True
    finally:
        GEM_SLEEP, GEM_NOW, GEMINI, GEMINI_CACHE_DIR = real_sleep, real_now, real_gemini, old_cache
        CFG.update({k: v for k, v in old_cfg.items() if v is not None})


if CFG.get("run_self_tests"):
    GEMINI_CORE_TESTS_PASSED = run_gemini_core_tests()
    print("GEMINI_CORE_TESTS_PASSED =", GEMINI_CORE_TESTS_PASSED)
else:
    GEMINI_CORE_TESTS_PASSED = None
    print("Self-tests skipped (this is normal). Nothing to do in this cell.")
