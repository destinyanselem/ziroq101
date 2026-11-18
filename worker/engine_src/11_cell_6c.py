# CELL 6c — Director tests (synthetic transcript + fake Gemini; no network, no key)
def run_director_tests():
    import types, tempfile
    global GEMINI, GEMINI_CACHE_DIR
    saved, saved_cache = globals().get("GEMINI"), GEMINI_CACHE_DIR
    GEMINI_CACHE_DIR = tempfile.mkdtemp()
    # 40 sentences x 5s = 200s source. Sentence i says "sentence number i is spoken here now".
    sents = [Sentence(text=f"Sentence number {i} is spoken here now.", start=i * 5.0, end=i * 5.0 + 4.6) for i in range(40)]
    tr = Transcript(language="en", words=[], sentences=sents)
    SRC = Source(video_id="vid", title="T", duration=200.0, video_path="x")
    def clip(t0, t1, o, c, score=80, mood="uplifting"):
        return {"title": f"clip {t0}", "start": t0, "end": t1, "hook": "h", "why_it_works": "w", "opening_words": o,
                "closing_words": c, "score": score, "topics": ["Money"],
                "music": {"mood": mood, "energy": "medium", "search_terms": "warm piano"}, "broll_query": "city skyline"}
    try:
        assert parse_timecode("01:30") == 90.0 and parse_timecode("1:02:03") == 3723.0 and parse_timecode("abc") is None
        # exact timestamps -> sentence-aligned + verified
        data = {"clips": [clip("00:20", "00:55", "Sentence number 4 is spoken", "Sentence number 10 is spoken here now")]}
        plans, rej = director_plans_from_response(data, tr, 200.0, 5)
        assert len(plans) == 1 and plans[0].notes["boundary_quality"] == "verified"
        assert abs(plans[0].segments[0].source_start - 19.95) < 0.2 and plans[0].notes["music_brief"]["mood"] == "uplifting"
        # drifted timestamp (3s early/late) is re-located using the quoted words
        data = {"clips": [clip("00:17", "00:58", "Sentence number 4 is spoken", "Sentence number 10 is spoken here now")]}
        plans, _ = director_plans_from_response(data, tr, 200.0, 5)
        assert plans and abs(plans[0].segments[0].source_start - 19.95) < 0.2
        # wrong quote -> only timestamp trusted, flagged with lower confidence
        data = {"clips": [clip("00:20", "00:55", "totally different words here", "nothing like it at all")]}
        plans, _ = director_plans_from_response(data, tr, 200.0, 5)
        assert plans[0].notes["boundary_quality"] == "timestamp_only" and plans[0].segments[0].confidence < 1
        # rejects: outside video, reversed, too short, overlap, unparseable; over-length trimmed at sentence boundary
        data = {"clips": [clip("03:00", "04:30", "a", "b", 90), clip("00:50", "00:40", "a", "b", 89), clip("00:10", "00:15", "a", "b", 88),
                          clip("x", "y", "a", "b", 87), clip("00:00", "02:20", "Sentence number 0", "Sentence number 28", 86),
                          clip("00:10", "00:50", "Sentence number 2", "Sentence number 9", 85)]}
        plans, rej = director_plans_from_response(data, tr, 200.0, 5)
        assert len(plans) == 1 and plans[0].duration <= 90.0 and len(rej) == 5, (len(plans), rej)
        for p in plans: p.validate(200.0)
        # merge: director first, local fills gaps but never overlaps
        lp1 = ClipPlan("L1", "x", [Segment(100, 140, "p", "t")], notes={"editorial_score": 50})
        lp2 = ClipPlan("L2", "x", [Segment(plans[0].segments[0].source_start + 1, plans[0].segments[0].source_start + 30, "p", "t")], notes={"editorial_score": 99})
        merged = merge_director_with_local(plans, [lp2, lp1], 3)
        assert [m.title for m in merged][1:] == ["L1"] and merged[0].notes["decided_by"] == "gemini_director"

        # end-to-end with a fake pool: YouTube URL is sent as a video part; non-YouTube / disabled -> local fallback
        seen = {}
        class FakePool:
            enabled = True; disabled_reason = ""
            def generate(self, models, inp, rf, system, cfg, task):
                seen["input"] = inp
                body = {"video_summary": "s", "clips": [clip(f"{m:02d}:{s:02d}", f"{m:02d}:{s + 40:02d}", f"Sentence number {(m*60+s)//5} is", f"Sentence number {(m*60+s+35)//5} is spoken here now", 90 - m)
                                                      for m, s in ((0, 10), (0, 70), (2, 10), (2, 20), (0, 130))]}
                return GeminiResult(json.dumps(body), models[0], "P1", "key1", 1)
        GEMINI = FakePool()
        url = "https://www.youtube.com/watch?v=abc123"
        old_sets = CFG.get("use_gemini_director", True)
        CFG["use_gemini_director"] = True
        plans, info = gemini_director(url, SRC, tr)
        assert info["used"] and isinstance(seen["input"], list) and seen["input"][0] == {"type": "video", "uri": url}, seen
        assert STAGES["gemini_video_direction"]["status"] in ("completed", "completed_using_fallback")
        assert parse_sfx_cues([{"time": "00:12", "kind": "whoosh"}, {"time": "x", "kind": "pop"}, {"time": "00:20", "kind": "bogus"}]) == [(12.0, "whoosh")]
        plans2, info2 = gemini_director("/content/local.mp4", SRC, tr)
        assert plans2 == [] and "not a YouTube link" in info2["reason"]
        # local-file path: proxy is uploaded, call is pinned to the uploading key, file deleted afterwards
        events = {}
        class UpPool(FakePool):
            def generate(self, models, inp, rf, system, cfg, task, pin=None):
                events["pin"], events["inp"] = pin, inp; return FakePool.generate(self, models, inp, rf, system, cfg, task)
            def upload_file(self, path): events["uploaded"] = path; return {"label": "key2", "name": "files/z", "uri": "https://x/files/z", "mime_type": "video/mp4"}
            def delete_file(self, info): events["deleted"] = info["name"]
        real_proxy = globals()["build_gemini_proxy"]
        globals()["build_gemini_proxy"] = lambda s: "/tmp/proxy.mp4"
        GEMINI = UpPool()
        LSRC = Source(video_id="localvid", title="L", duration=200.0, video_path=__file__ if "__file__" in globals() else "/etc/hostname")
        if not os.path.isfile(LSRC.video_path): LSRC.video_path = "/etc/hostname"
        plans4, info4 = gemini_director("/content/local.mp4", LSRC, tr)
        globals()["build_gemini_proxy"] = real_proxy
        assert info4["used"] and events["pin"] == "key2" and events["deleted"] == "files/z" and events["inp"][0]["uri"] == "https://x/files/z", events
        # rerank: reorders by Gemini ranking, ignores bad answers, no call without a brief
        calls_n = {"n": 0}
        class RankPool:
            enabled = True; disabled_reason = ""
            def __init__(self, answer): self.answer = answer
            def generate(self, models, inp, rf, system, cfg, task):
                calls_n["n"] += 1; return GeminiResult(json.dumps(self.answer), models[0], "P", "k", 1)
        items = [{"title": f"t{i}"} for i in range(4)]
        RERANK_CONTEXT["music"] = "warm piano for a calm lesson"
        GEMINI = RankPool({"ranking": [2, 0, 1]})
        assert [x["title"] for x in rerank_candidates("music", items)] == ["t2", "t0", "t1", "t3"]
        RERANK_CONTEXT["music"] = "a different brief so the cache cannot answer"
        GEMINI = RankPool({"ranking": [9, 9]})
        assert rerank_candidates("music", items, top_n=4) == items
        RERANK_CONTEXT["music"] = ""
        n0 = calls_n["n"]; assert rerank_candidates("music", items) == items and calls_n["n"] == n0
        GEMINI = types.SimpleNamespace(enabled=False, disabled_reason="off")
        plans3, info3 = gemini_director(url, SRC, tr)
        assert plans3 == [] and info3["reason"] == "gemini disabled"
        CFG["use_gemini_director"] = old_sets
        STAGES.pop("gemini_video_direction", None)
        # framing hints (Gemini) -> framing dicts
        h = parse_framing_hint({"layout": "single_speaker", "speaker_x": [{"time": "00:10", "x": 0.3}, {"time": "00:14", "x": 0.35}, {"time": "00:20", "x": 7}, {"time": "bad", "x": 0.5}]})
        assert h["layout"] == "single_speaker" and h["points"] == [(10.0, 0.3), (14.0, 0.35)]
        return True
    finally:
        GEMINI, GEMINI_CACHE_DIR = saved, saved_cache


if CFG.get("run_self_tests"):
    DIRECTOR_TESTS_PASSED = run_director_tests()
    print("DIRECTOR_TESTS_PASSED =", DIRECTOR_TESTS_PASSED)
else:
    DIRECTOR_TESTS_PASSED = None
    print("Self-tests skipped (this is normal). Nothing to do in this cell.")
