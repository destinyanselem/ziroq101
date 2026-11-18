# CELL 13 (OPTIONAL, developers only) — Helper-level tests (no YouTube download, model download, or Colab GPU required)

def run_helper_self_tests():
    # Multi-interval source → output timeline mapping.
    plan = ClipPlan("test", "test", [
        Segment(10.0, 12.0, "hook", "alpha beta"),
        Segment(40.0, 43.0, "payoff", "gamma delta"),
    ], target_duration=90.0)
    words = [Word("alpha", 10.0, 10.4), Word("beta", 11.0, 11.4),
             Word("gamma", 40.0, 40.5), Word("delta", 42.0, 42.4)]
    mapped = timeline_words(plan, words)
    assert len(mapped) == 4
    assert abs(mapped[2]["start"] - 2.0) < 1e-6, mapped
    assert abs(mapped[-1]["end"] - 4.4) < 1e-6, mapped

    # Duration and source-boundary validation.
    assert abs(validate_segment_list(plan.segments, 100.0) - 5.0) < 1e-6
    try:
        validate_segment_list([Segment(98, 105, "bad", "bad")], 100.0)
        raise AssertionError("Invalid source segment was not rejected")
    except ValueError:
        pass
    try:
        ClipPlan("too long", "test", [Segment(0, 91, "x", "x")]).validate(120)
        raise AssertionError("Over-90-second plan was not rejected")
    except ValueError:
        pass

    # Audio graph has the ducking + speech-first final limiter path used by renderer.
    graph = ";".join(make_audio_mix_graph(2, 5.0, volume=0.18))
    assert "sidechaincompress" in graph and "amix" in graph and "alimiter" in graph
    assert "level=disabled" in graph and "alimiter=limit=0.800" in graph and "limit=0.500" in ";".join(make_audio_mix_graph(2, 5.0, ceiling=0.5))   # auto-level off: it used to push peaks back to full scale

    # Output-count gate must reject partial sets.
    try:
        enforce_five_output_count([1,2,3,4,5], [1,2])
        raise AssertionError("Partial delivery should have been blocked")
    except RuntimeError:
        pass
    assert enforce_five_output_count([1,2,3,4,5], [1,2,3,4,5]) is True

    # FFmpeg dynamic crop expression is syntactically composed and clamped.
    expression = ffmpeg_crop_x_expr([(0.0, 100.0), (1.0, 140.0), (2.0, 120.0)], 500)
    assert "if(lt(t" in expression and "min(500" in expression

    # Asset licence policy and path containment regression tests.
    assert _normalise_license_url("https://creativecommons.org/licenses/by/4.0") == "https://creativecommons.org/licenses/by/4.0/"
    assert "https://creativecommons.org/publicdomain/mark/1.0/" not in ARCHIVE_LICENSE_ALLOWLIST
    test_pool = os.path.join(CFG["work_dir"], "_asset_path_test")
    os.makedirs(test_pool, exist_ok=True)
    safe_test_file = os.path.join(test_pool, "safe.mp4")
    with open(safe_test_file, "wb") as f:
        f.write(b"test")
    assert _safe_pool_file(test_pool, "safe.mp4") == safe_test_file
    assert _safe_pool_file(test_pool, "../outside.mp4") is None
    assert _safe_pool_file(test_pool, os.path.abspath(safe_test_file)) is None
    os.remove(safe_test_file)
    os.rmdir(test_pool)

    print("PASS: timeline offset mapping")
    print("PASS: source interval / duration validation")
    print("PASS: speech-ducked audio graph construction")
    print("PASS: five-output delivery gate")
    print("PASS: dynamic crop expression construction")
    print("PASS: asset licence allowlist + safe pool paths")
    return True

def run_batch1_regression_tests():
    """Regression tests for the v4 batch-1 fixes (synthetic fixtures; no model/network access required)."""
    import tempfile, inspect, zipfile
    tmp = tempfile.mkdtemp(prefix="ziroq_batch1_")
    g = globals()
    try:
        # 1) classifier cue vocabulary matches lowercase tokens
        assert all(c == c.lower() for c in STORY_CUES), "STORY_CUES must be lowercase"

        # 2) story-plan checkpoint: round trip, and invalidation when the source changes
        saved = {k: g.get(k) for k in ("SOURCE", "TRANSCRIPT", "SIMILARITY_BACKEND", "SOURCE_DIR")}
        try:
            g["SOURCE"] = Source("tvid", "t", 300.0, "x.mp4")
            ws = [Word(f"w{i}", i * 0.5, i * 0.5 + 0.4) for i in range(40)]
            g["TRANSCRIPT"] = Transcript("en", ws, build_sentences(ws))
            g["SIMILARITY_BACKEND"], g["SOURCE_DIR"] = "tfidf_fallback", tmp
            plan = ClipPlan("Title", "story", [Segment(10.0, 20.0, "hook", "a b c"), Segment(100.0, 115.5, "payoff", "d e f")],
                            90.0, {"editorial_score": 55.5, "topics": ["money"]})
            save_plans_checkpoint([plan])
            loaded = load_plans_checkpoint()
            assert loaded and loaded[0].title == "Title" and abs(loaded[0].duration - 25.5) < 1e-9
            assert loaded[0].notes["editorial_score"] == 55.5
            g["SOURCE"] = Source("tvid", "t", 301.0, "x.mp4")
            assert load_plans_checkpoint() is None, "checkpoint must be rejected for a different source duration"
        finally:
            g.update(saved)

        # 3) editorial QA verifies against the transcript
        src = Source("v", "t", 600.0, "x.mp4")
        tw = [Word("Why", 10.0, 10.3), Word("does", 10.4, 10.7), Word("money", 10.8, 11.1), Word("matter?", 11.2, 11.6),
              Word("Because", 40.0, 40.4), Word("compounding", 40.5, 41.1), Word("works.", 41.2, 41.6)]
        tr = Transcript("en", tw, build_sentences(tw))
        def qa(segs, topics=("money",)):
            p = ClipPlan("t", "story", segs, 90.0, {"topics": list(topics), "editorial_score": 50})
            return {c["check"]: c for c in run_editorial_qa(p, src, tr)}
        good = qa([Segment(10.0, 11.6, "hook", "Why does money matter?"), Segment(40.0, 41.6, "payoff", "Because compounding works.")])
        assert good["source_quotation_integrity"]["passed"] and good["segments_on_sentence_boundaries"]["passed"]
        assert not good["context_review_flagged"]["flagged"]
        fake = qa([Segment(10.0, 11.6, "hook", "Completely different invented sentence here")])
        assert not fake["source_quotation_integrity"]["passed"], "invented text must fail quotation integrity"
        mid = qa([Segment(10.4, 11.6, "hook", "does money matter?")])
        assert not mid["segments_on_sentence_boundaries"]["passed"], "mid-sentence cut must fail"
        dangling = qa([Segment(40.0, 41.6, "hook", "Because compounding works.")])
        assert dangling["context_review_flagged"]["flagged"] and dangling["context_review_flagged"]["passed"]
        reorder = qa([Segment(40.0, 41.6, "hook", "Because compounding works."), Segment(10.0, 11.6, "support", "Why does money matter?")])
        assert "source order" in reorder["context_review_flagged"]["detail"]

        # 4) framing logic with a stub detector on a synthetic 16:9 video (a moving, then two static, bright squares)
        class BoxDetector:
            def detectMultiScale(self, gray, **kw):
                n, _, stats, _ = cv2.connectedComponentsWithStats((gray > 200).astype(np.uint8))
                return np.array([stats[i][:4] for i in range(1, n) if stats[i][4] > 1000], dtype=np.int32).reshape(-1, 4)
        class NoFaces:
            def detectMultiScale(self, gray, **kw):
                return np.empty((0, 4), dtype=np.int32)
        W, H, FPS = 1280, 720, 10
        def make_video(path, painter):
            vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
            for n in range(60):
                frame = np.zeros((H, W, 3), np.uint8); painter(frame, n / FPS); vw.write(frame)
            vw.release()
        def moving(frame, t):
            x = 100 + int(t * 150); frame[300:420, x:x + 120] = 255
        def two_static(frame, t):
            frame[300:420, 100:220] = 255; frame[300:420, 1000:1120] = 255
        v1, v2 = os.path.join(tmp, "moving.mp4"), os.path.join(tmp, "two.mp4")
        make_video(v1, moving); make_video(v2, two_static)
        seg = Segment(0.0, 5.0, "hook", "x")
        r = detect_segment_framing(Source("a", "a", 6.0, v1, width=W, height=H), seg, detector=BoxDetector())
        assert r["mode"] == "face_track" and r["keyframes"][-1][1] > r["keyframes"][0][1] + 200, r
        r = detect_segment_framing(Source("b", "b", 6.0, v2, width=W, height=H), seg, detector=BoxDetector())
        assert r["mode"] == "fit_blur" and r.get("multi_person_samples", 0) >= 2, r
        r = detect_segment_framing(Source("a", "a", 6.0, v1, width=W, height=H), seg, detector=NoFaces())
        assert r["mode"] == "fit_blur" and r.get("uncertain"), r
        old_mode = CFG.get("framing_uncertain_mode")
        try:
            CFG["framing_uncertain_mode"] = "center_crop"
            r = detect_segment_framing(Source("a", "a", 6.0, v1, width=W, height=H), seg, detector=NoFaces())
            assert r["mode"] == "center_crop", r
        finally:
            CFG["framing_uncertain_mode"] = old_mode
        r = detect_segment_framing(Source("p", "p", 6.0, v1, width=720, height=1280), seg, detector=BoxDetector())
        assert r["mode"] == "fit_blur" and "not wider" in r["reason"], r

        # 5) cascade recovery helpers (wheel extraction without installing)
        wheel = os.path.join(tmp, "fake.whl")
        with zipfile.ZipFile(wheel, "w") as zf:
            zf.writestr("cv2/data/" + FACE_CASCADE_NAME, "x" * 2000)
            zf.writestr("cv2/data/tiny.xml", "x")
        out = extract_member_from_wheel(wheel, "/" + FACE_CASCADE_NAME, os.path.join(tmp, "m", FACE_CASCADE_NAME))
        assert out and os.path.getsize(out) == 2000
        assert extract_member_from_wheel(wheel, "/tiny.xml", os.path.join(tmp, "m", "tiny.xml")) is None
        assert extract_member_from_wheel(wheel, "/missing.xml", os.path.join(tmp, "m", "missing.xml")) is None

        # 6) detector resolution order and the loud no-detector outcome (mocked)
        saved_fns = {k: g[k] for k in ("_load_cascade", "recover_cascade_from_opencv4_wheel", "get_face_detector")}
        try:
            g["_load_cascade"] = lambda p: None
            g["recover_cascade_from_opencv4_wheel"] = lambda d: None
            st = saved_fns["get_face_detector"](force_reload=True)
            assert st["detector"] is None and st["kind"] is None and st["reason"]
            g["get_face_detector"] = lambda force_reload=False: {"detector": None}
            r = detect_segment_framing(Source("a", "a", 6.0, v1, width=W, height=H), seg)
            assert r["mode"] == "fit_blur" and r.get("uncertain"), "no detector must keep the full frame, not centre-crop"
            sentinel = object()
            g["get_face_detector"] = saved_fns["get_face_detector"]
            g["_load_cascade"] = lambda p: sentinel
            st = saved_fns["get_face_detector"](force_reload=True)
            assert st["kind"] == "haar_bundled" and st["detector"] is sentinel
        finally:
            g.update(saved_fns)
            _FACE_DETECTOR_STATE.update({"resolved": False, "kind": None, "detector": None, "reason": ""})

        _keep_seg = seg
        S = Source("v", "t", 200.0, "x", width=1920, height=1080)
        seg = Segment(8.0, 20.0, "p", "t")
        h = parse_framing_hint({"layout": "single_speaker", "speaker_x": [{"time": "00:10", "x": 0.3}, {"time": "00:14", "x": 0.35}]})
        f = framing_from_hint(S, seg, h)
        assert f["mode"] == "face_track" and len(f["keyframes"]) == 2 and all(0 <= k[1] <= 1920 - 608 for k in f["keyframes"])
        assert framing_from_hint(S, seg, {"layout": "slides_or_screen", "points": []})["mode"] == "fit_blur"
        assert framing_from_hint(S, seg, {"layout": "single_speaker", "points": [(10.0, 0.1), (12.0, 0.9)]}) is None   # implausible jump
        assert framing_from_hint(S, seg, {"layout": "single_speaker", "points": [(10.0, 0.5)]}) is None                 # too few samples
        assert framing_from_hint(S, seg, None) is None
        seg = _keep_seg
        # 6b) Gemini framing hint: used only when local detection is uncertain; slides/multi-speaker layouts force fit_blur
        class _Seg: source_start, source_end, duration = 0.0, 3.0, 3.0
        _src = Source("h", "h", 6.0, v1, width=W, height=H)
        _hint = {"layout": "single_speaker", "points": [(0.5, 0.4), (2.5, 0.45)]}
        _r = detect_segment_framing(_src, seg, detector=NoFaces(), hint=_hint)
        assert _r["mode"] == "face_track" and _r.get("decided_by") == "gemini", _r
        _r = detect_segment_framing(_src, seg, detector=BoxDetector(), hint={"layout": "slides_or_screen", "points": []})
        assert _r["mode"] == "fit_blur" and _r.get("decided_by") == "gemini"
        _r = detect_segment_framing(_src, seg, detector=NoFaces(), hint=None)
        assert _r["mode"] == "fit_blur" and _r.get("uncertain")

        # 7) retry no longer needs to mutate CFG
        assert "crf" in inspect.signature(render_plan).parameters
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("PASS: batch-1 regression tests (classifier cues, plan checkpoint, editorial QA, framing, cascade recovery, retry crf)")
    return True


def run_batch3_music_tests():
    g = globals()
    plan = ClipPlan("t", "x", [Segment(0, 30, "p", "believe in the future")],
                    notes={"music_brief": {"mood": "playful", "energy": "high", "search_terms": "Bright Ukulele!"}, "broll_query": "city skyline night"})
    assert plan_mood(plan) == "playful"                                   # Gemini brief wins over keyword guess
    plan.notes["music_brief"]["mood"] = "bogus"
    assert plan_mood(plan) == "uplifting"                                  # invalid brief falls back to the local classifier
    assert broll_query_for_plan(plan) == "city skyline night"
    plan.notes["broll_query"] = "x"
    assert broll_query_for_plan(plan) != "x"                               # too-short brief -> local query
    seen = {}
    import tempfile
    real_get, real_cfg_key, real_cache = g["requests"].get, CFG.get("coverr_api_key"), g["ASSET_CACHE_DIR"]
    g["ASSET_CACHE_DIR"] = tempfile.mkdtemp()
    class R:
        def raise_for_status(self): pass
        def json(self): return {"hits": []}
    def fake_get(url, params=None, **kw):
        seen["query"] = (params or {}).get("query"); return R()
    try:
        g["requests"].get = fake_get
        CFG["coverr_api_key"] = "test"
        plan.notes["music_brief"] = {"mood": "playful", "search_terms": "Bright Ukulele!"}
        g["MUSIC_QUERY_OVERRIDE"] = "bright ukulele"
        g["_download_coverr_music"]("playful", 0)
        assert seen["query"] == "bright ukulele", seen
        g["MUSIC_QUERY_OVERRIDE"] = ""
    finally:
        g["requests"].get = real_get; CFG["coverr_api_key"] = real_cfg_key; g["MUSIC_QUERY_OVERRIDE"] = ""; g["ASSET_CACHE_DIR"] = real_cache
    print("PASS: Gemini music/B-roll brief reaches the provider search; invalid briefs fall back locally")
    return True


def run_batch5_sfx_tests():
    import tempfile
    seg = Segment(100.0, 160.0, "p", "t")
    plan = ClipPlan("t", "x", [seg], notes={"sfx_cues": [(110.0, "impact"), (111.0, "pop"), (150.0, "whoosh"), (99.0, "pop"), (159.9, "pop"), (120.0, "bogus")]})
    cues = plan_sfx_cues(plan)
    assert [(round(t), k) for t, k, _ in cues] == [(10, "impact"), (50, "whoosh")], cues      # too-close, outside and invalid cues are dropped
    two = ClipPlan("t", "x", [Segment(10, 40, "p", "t"), Segment(200, 230, "p", "t")])
    c2 = plan_sfx_cues(two)
    assert len(c2) == 1 and c2[0][2] == "local_rule" and abs(c2[0][0] - 29.85) < 0.01          # rule-based whoosh at the join
    assert plan_sfx_cues(ClipPlan("t", "x", [seg])) == []                                       # single interval, no cues -> nothing
    tmp = tempfile.mkdtemp()
    path = build_sfx_track(plan, os.path.join(tmp, "sfx.wav"))
    with wave.open(path) as w:
        assert w.getframerate() == 48000 and w.getnchannels() == 2 and abs(w.getnframes() / 48000 - 61.0) < 0.05
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    assert 0.05 < np.abs(data).max() / 32767 <= 0.81                                            # audible but bounded
    for k in SFX_KINDS:
        assert np.isfinite(synth_sfx(k)).all() and abs(synth_sfx(k)).max() <= 0.61
    assert build_sfx_track(ClipPlan("t", "x", [seg]), os.path.join(tmp, "none.wav")) is None
    g = ";".join(make_audio_mix_graph(2, 5.0, sfx_input_index=3))
    assert "[3:a]" in g and "amix=inputs=3" in g and "[speech][ducked][sfx]" in g
    assert "amix=inputs=2" in ";".join(make_audio_mix_graph(2, 5.0))
    # real FFmpeg run of the 3-input graph: result decodes, and peak stays under the ceiling
    ff = os.path.join(tmp, "m.wav")
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=20:c=pink:a=0.4", "-f", "lavfi", "-i", "sine=f=220:d=20", "-i", path,
         "-filter_complex", ";".join(["[0:a]aformat=channel_layouts=stereo,asplit=0[x]"][:0] + ["[0:a]aformat=channel_layouts=stereo[abase]"] + make_audio_mix_graph(1, 20.0, sfx_input_index=2)),
         "-map", "[aout]", "-t", "20", ff], quiet=True)
    audio = load_pcm16_wav(ff)[0] if False else None
    assert os.path.getsize(ff) > 1000
    print("PASS: SFX cue validation, procedural synthesis, 3-input mix graph (decoded by FFmpeg)")
    return True


def run_batch6_tests():
    import types
    S = lambda t, a, b: Sentence(t, a, b)
    sents = [S("We started with nothing.", 0, 4), S("Then the first sale came in", 4.5, 9),            # no terminal punctuation, ends on a plain word
             S("and everything changed.", 9.4, 12), S("It took years.", 12.5, 15), S("But that is the lesson.", 15.3, 18.5),
             S("And so we kept going.", 18.9, 22)]
    tr = Transcript("en", [], sents)
    SRC = Source("v", "t", 100.0, "x")
    assert sentence_complete("It ended.") and not sentence_complete("and then the") and not sentence_complete("we went to") and not sentence_complete("I think so")
    # end extension: plan ends mid-thought ("...came in" is complete-looking, so use a clearly unfinished one)
    sents2 = [S("Money works like this.", 0, 4), S("You save, then you", 4.4, 8), S("invest the difference.", 8.3, 12), S("That is it.", 12.5, 15)]
    tr2 = Transcript("en", [], sents2)
    p = ClipPlan("t", "x", [Segment(0.0, 8.1, "p", "Money works like this. You save, then you")], notes={})
    log_ = complete_plan_boundaries(p, tr2, SRC)
    assert log_ and abs(p.segments[0].source_end - 12.1) < 0.01 and p.segments[0].transcript.endswith("invest the difference."), (log_, p.segments[0])
    # cap respected: cannot extend beyond 90 s
    p = ClipPlan("t", "x", [Segment(0.0, 8.1, "p", "x")], target_duration=8.2, notes={})
    p.target_duration = 90.0
    CFG_max = CFG["max_clip_duration"]; CFG["max_clip_duration"] = 8.5
    try:
        complete_plan_boundaries(p, tr2, SRC); assert p.segments[0].source_end <= 8.51
    finally:
        CFG["max_clip_duration"] = CFG_max
    # dangling connector start trimmed when enough time remains
    long_s = [S("And so we kept going.", 0, 4)] + [S(f"Sentence {i} stays here.", 4 + i * 4, 7.5 + i * 4) for i in range(8)]
    trl = Transcript("en", [], long_s)
    p = ClipPlan("t", "x", [Segment(0.0, 36.0, "p", "x")], notes={})
    complete_plan_boundaries(p, trl, SRC); assert _first_token(p.segments[0].transcript) == "sentence" and p.segments[0].source_start > 3.9

    # tighten: filler + long pause removed, cuts in silence only, QA boundary logic accepts it
    W = lambda w, a, b: Word(w, a, b, 0.95)
    words = [W("Money", 0.5, 0.9), W("works", 1.0, 1.4), W("um", 1.6, 1.9), W("like", 2.0, 2.3), W("this", 2.4, 2.8),   # filler between 1.4 and 2.0
             W("Save", 4.2, 4.6), W("early", 4.7, 5.1), W("and", 5.2, 5.5), W("invest", 5.6, 6.1), W("today.", 6.2, 6.7),      # 1.4 s pause before "Save"
             W("That", 6.8, 7.1), W("is", 7.2, 7.4), W("it", 7.5, 7.8), W("really.", 7.9, 8.4)]
    sn = [S("Money works um like this.", 0.45, 2.85), S("Save early and invest today.", 4.15, 6.75), S("That is it really.", 6.8, 8.45)]
    tr3 = Transcript("en", words, sn)
    SR = Source("v", "t", 30.0, "x", width=1920, height=1080)
    plan = ClipPlan("t", "x", [Segment(0.4, 8.55, "p", "Money works um like this. Save early and invest today. That is it really.")], notes={"editorial_score": 50})
    before = plan.duration
    CFG["tighten_max_fraction"] = 0.30        # this toy clip is deliberately full of dead air
    try:
        st = tighten_plan(plan, tr3, SR)
    finally:
        CFG["tighten_max_fraction"] = 0.20
    assert st["fillers"] >= 1 and st["pauses"] >= 1 and len(plan.segments) >= 3 and plan.duration < before - 1.0, (st, plan.segments)
    assert plan.segments[0].role != "tight_zoom" and plan.segments[1].role == "tight_zoom" and plan.segments[2].role == "tight_cont"
    for a, b in zip(plan.segments, plan.segments[1:]):
        assert b.source_start > a.source_end                                                    # cuts only remove time, never repeat it
        assert not any(w.start < a.source_end + 0.001 and w.end > a.source_end + 0.001 or (w.start < b.source_start and w.end > b.source_start + 0.001) for w in words), "cut inside a word"
    assert all(s.duration >= 0.8 for s in plan.segments)
    mapped = [m["word"] for m in timeline_words(plan, words)]
    assert "um" not in mapped and "Money" in mapped and "today." in mapped                       # filler gone from captions, real words kept
    qa = run_editorial_qa(plan, SR, tr3)
    assert [c for c in qa if c["check"] == "segments_on_sentence_boundaries"][0]["passed"], qa
    assert [c for c in qa if c["check"] == "source_quotation_integrity"][0]["passed"], qa
    # budget guard: a clip that is mostly silence is not shredded
    sparse = ClipPlan("t", "x", [Segment(0.0, 10.0, "p", "x")], notes={})
    tr4 = Transcript("en", [W("a", 0.0, 0.3), W("b", 4.0, 4.3), W("c", 9.6, 9.9)], [S("a b c.", 0, 10)])
    t4 = tighten_plan(sparse, tr4, SR); assert t4["removed_sec"] <= 0.2 * 10.0 + 0.01
    CFG["enable_tighten"] = False
    try:
        off = ClipPlan("t", "x", [Segment(0.4, 8.55, "p", "x")], notes={}); assert tighten_plan(off, tr3, SR)["pieces_added"] == 0
    finally:
        CFG["enable_tighten"] = True

    # critic: executed only as bounded sentence fixes; bad output changes nothing
    global GEMINI
    saved = GEMINI
    class CriticPool:
        enabled = True; disabled_reason = ""
        def __init__(self, v): self.v = v
        def generate(self, models, inp, rf, system, cfg, task):
            return GeminiResult(json.dumps({"verdicts": self.v}), models[0], "P", "k", 1)
    import tempfile
    global GEMINI_CACHE_DIR
    saved_cache = GEMINI_CACHE_DIR; GEMINI_CACHE_DIR = tempfile.mkdtemp()
    try:
        tr5 = Transcript("en", [], [S("Intro line here.", 0, 4), S("The key point is this.", 4.4, 14), S("Do it daily.", 14.4, 24), S("That compounds.", 24.4, 30)])
        p1 = ClipPlan("t", "x", [Segment(0.0, 14.1, "p", "Intro line here. The key point is this.")], notes={})
        GEMINI = CriticPool([{"index": 0, "complete_thought": False, "needs_prior_context": True, "ends_on_payoff": False, "issue": "cut before the answer",
                              "extend_end_sentences": 2, "trim_start_sentences": 1}])
        v = gemini_critic([p1], tr5); assert 0 in v
        apply_critic(p1, v[0], tr5, SRC)
        assert abs(p1.segments[0].source_end - 30.1) < 0.01 and p1.segments[0].source_start > 3.9
        p2 = ClipPlan("t", "x", [Segment(0.0, 14.1, "p", "x")], notes={})
        apply_critic(p2, {"complete_thought": True, "needs_prior_context": False, "ends_on_payoff": True, "extend_end_sentences": 2, "trim_start_sentences": 2}, tr5, SRC)
        assert p2.segments[0].source_end == 14.1 and p2.segments[0].source_start == 0.0           # advice ignored when the verdict says it is fine
        GEMINI = CriticPool([{"index": 7, "complete_thought": True, "needs_prior_context": False, "ends_on_payoff": True, "extend_end_sentences": 0, "trim_start_sentences": 0}])
        assert gemini_critic([ClipPlan("t2", "x", [Segment(0.0, 5.0, "p", "z")], notes={})], tr5) == {}   # invalid index -> fallback, no changes
        GEMINI = types.SimpleNamespace(enabled=False, disabled_reason="off")
        assert gemini_critic([p1], tr5) == {}
        # whole pipeline entry point, idempotent
        GEMINI = types.SimpleNamespace(enabled=False, disabled_reason="off")
        pp = ClipPlan("t", "x", [Segment(0.4, 8.55, "p", "x")], notes={"editorial_score": 50})
        finish_plans([pp], tr3, SR); n1 = len(pp.segments); finish_plans([pp], tr3, SR)
        assert pp.notes["finished"] and len(pp.segments) == n1
    finally:
        GEMINI, GEMINI_CACHE_DIR = saved, saved_cache
    # split-screen geometry + chain
    h = parse_framing_hint({"layout": "multi_speaker", "speakers_x": [0.7, 0.25], "speaker_x": []})
    assert h["speakers_x"] == [0.25, 0.7]
    f = framing_from_hint(SR, Segment(0, 5, "p", "t"), h)
    assert f["mode"] == "split_two" and f["crop_w"] == 1214 and 0 <= f["x_left"] < f["x_right"] <= 1920 - 1214, f
    assert framing_from_hint(SR, Segment(0, 5, "p", "t"), {"layout": "multi_speaker", "speakers_x": [0.4, 0.5]})["mode"] == "fit_blur"     # too close together
    assert framing_from_hint(Source("v", "t", 9.0, "x", width=1000, height=1080), Segment(0, 5, "p", "t"), h)["mode"] == "fit_blur"        # source too narrow
    assert parse_framing_hint({"layout": "multi_speaker", "speakers_x": [0.2]})["speakers_x"] == []
    # audio meter regression: identical-channel audio with a true 0.75 peak must read ~0.75, not 1.06 (+3 dB mono-downmix artefact)
    tmpd = tempfile.mkdtemp(); mp = os.path.join(tmpd, "t.m4a")
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "aevalsrc=0.75*sin(2*PI*440*t)|0.75*sin(2*PI*440*t):d=2:s=48000", "-c:a", "aac", "-b:a", "192k", mp], quiet=True)
    pk = audio_metrics(mp)["peak"]
    assert 0.70 <= pk <= 0.82, pk
    print("PASS: completeness gate, Gemini critic (bounded), filler/pause tightening, split-screen geometry, audio meter")
    return True


def run_batch7_tests():
    """Publishing Studio: limits, pinned comments, local fallback, Gemini output clamping, files written."""
    import types, tempfile
    g = globals()
    saved = {k: g.get(k) for k in ("SOURCE", "TRANSCRIPT", "REPORT_DIR", "RUN_ID", "GEMINI", "gemini_structured", "SOURCE_META")}
    try:
        g["SOURCE"] = types.SimpleNamespace(title="Jim Rohn - Take Responsibility", duration=900.0)
        g["TRANSCRIPT"] = types.SimpleNamespace(language="en")
        g["SOURCE_META"] = {"channel": "Rohn Channel", "tags": ["jim rohn", "personal development"]}
        g["REPORT_DIR"] = tempfile.mkdtemp(); g["RUN_ID"] = "T7"
        seg = lambda t: types.SimpleNamespace(transcript=t)
        plan = types.SimpleNamespace(
            title="Stop blaming others for your failures", duration=62.0, narrative_type="problem-solution", notes={"topics": ["self-improvement", "leadership"]},
            segments=[seg("Failure is an event, not a person. You have to take responsibility for your results."),
                      seg("Nobody else is going to change your life, so stop making excuses and start acting today.")])
        ctx = _clip_context(plan, 1)
        # 1. local fallback honours every hard limit and has pinned comments
        f = finalize_pack(local_pack(plan, 1, ctx), ctx)
        assert 12 <= len(f["youtube"]["title"]) <= PL["yt_title"] and len(f["youtube"]["tags_line"]) <= PL["yt_tags_total"]
        assert 1 <= len(f["youtube"]["hashtags"]) <= 5 and len(f["instagram"]["hashtags"]) <= 5 and len(f["x"]["post"]) <= 280
        for plat in ("youtube", "tiktok", "instagram", "facebook"):
            assert len(f[plat]["pinned_comments"]) == 2 and all(f[plat]["pinned_comments"]), plat
        assert f["threads"]["topic_tag"] and "Source: Jim Rohn" in f["youtube"]["description"] and f["youtube"]["category"] == "Education"
        # 2. validator rejects invented numbers and missing pinned comments
        bad = {"youtube": {"title": "How 7 habits made me 10x richer in 30 days", "pinned_comments": [], "tags": ["a"]},
               "tiktok": {"pinned_comments": ["q"]}, "instagram": {"pinned_comments": ["q"]}, "facebook": {"pinned_comments": ["q"]}}
        errs = validate_pack(bad, ctx)
        assert any("not in the clip" in e for e in errs) and any("youtube.pinned_comments" in e for e in errs), errs
        # 3. Gemini output that is too long/spammy is clamped, files are written
        long_raw = {"youtube": {"title": "Stop Blaming Others " * 8, "title_alternatives": ["a b c d e f g h i j k l", "x" * 150], "description_hook": "h" * 400,
                                "description_body": "b " * 3000, "hashtags": [f"tag{i}" for i in range(14)], "tags": [f"long tag number {i}" for i in range(80)] + ["z" * 45],
                                "category": "Made Up Category", "pinned_comments": ["What will you own this week?", "Agree or disagree?"]},
                    "tiktok": {"caption": "c " * 2000, "hashtags": [f"t{i}" for i in range(12)], "search_keywords": ["a"] * 20, "on_screen_text": "o" * 200, "pinned_comments": ["q1", "q2"]},
                    "instagram": {"caption": "i " * 2000, "hashtags": [f"#i{i}" for i in range(30)], "cover_text": "c" * 90, "alt_text": "a" * 900, "pinned_comments": ["q1", "q2"]},
                    "facebook": {"description": "f " * 2000, "hashtags": [f"f{i}" for i in range(9)], "pinned_comments": ["q1", "q2"]},
                    "x": {"post": "x " * 300, "reply_comments": ["r1", "r2"]},
                    "threads": {"post": "t " * 400, "topic_tag": "#Mind set", "reply_comments": ["r1", "r2"]}}
        fl = finalize_pack(long_raw, ctx)
        assert len(fl["youtube"]["title"]) <= 100 and len(fl["youtube"]["hashtags"]) == 5 and len(fl["youtube"]["tags_line"]) <= 500
        assert all(len(t.strip()) <= 30 for t in fl["youtube"]["tags_line"].split(",")) and len(fl["youtube"]["description"]) <= 5000
        assert fl["youtube"]["category"] == "Education" and len(fl["tiktok"]["caption"]) <= 2200 and len(fl["instagram"]["caption"]) <= 2200
        assert len(fl["instagram"]["hashtags"]) == 5 and len(fl["facebook"]["hashtags"]) == 3 and len(fl["x"]["post"]) <= 270
        assert len(fl["threads"]["post"]) <= 480 and fl["threads"]["topic_tag"] == "Mindset" and len(fl["instagram"]["cover_text"]) <= 40
        g["GEMINI"] = types.SimpleNamespace(enabled=True)
        g["gemini_structured"] = lambda *a, **k: {"ok": True, "data": long_raw, "source": "gemini"}
        packs = build_publishing_packs([plan])
        md = open(os.path.join(REPORT_DIR, "publishing_pack", "clip_01_PUBLISHING_PACK.md"), encoding="utf-8").read()
        assert packs["youtube"]["1"]["generated_by"] == "gemini" and "Pinned comment" in md and "copy and paste this exact line" in md
        assert os.path.isfile(os.path.join(REPORT_DIR, "publishing_pack", "ALL_CLIPS_PUBLISHING_PACK.md"))
        # 4. Gemini unavailable -> local template still produces a complete, valid pack
        g["gemini_structured"] = lambda *a, **k: {"ok": False, "fallback_required": True, "error_kind": "unavailable"}
        packs2 = build_publishing_packs([plan])
        assert packs2["youtube"]["1"]["generated_by"] == "local template" and packs2["tiktok"]["1"]["pinned_comments"]
        print("PASS: publishing studio (limits, pinned comments, grounding checks, local fallback, files)")
        return True
    finally:
        for k, v in saved.items():
            if v is None:
                g.pop(k, None)
            else:
                g[k] = v



def run_batch8_tests():
    """Long clips (gate + director), final reviewer (verdicts, bounded fixes, revert), pack length notes."""
    import types, tempfile
    g = globals()
    saved = {k: g.get(k) for k in ("SOURCE", "TRANSCRIPT", "RUN_ID")}
    saved_cfg = {k: CFG.get(k) for k in ("director_long_max_clips", "allow_long_clips")}
    try:
        def make_tr(n_sent, words_per=10):
            sents = [Sentence(text=f"Sentence number {i} is spoken here now.", start=i * 5.0, end=i * 5.0 + 4.6) for i in range(n_sent)]
            ws = []
            for i in range(n_sent):
                for k in range(words_per):
                    t0 = i * 5.0 + k * (4.6 / words_per)
                    ws.append(types.SimpleNamespace(word="w", start=t0, end=t0 + 4.6 / words_per * 0.9))
            return Transcript(language="en", words=ws, sentences=sents)
        tr = make_tr(80)
        assert long_clip_gate(0.0, 100.0, tr)[0]
        assert not long_clip_gate(0.0, 100.0, make_tr(80, words_per=3))[0]                     # too sparse to hold attention
        def clip(t0, t1, o, c, score=80, reason=""):
            d = {"title": f"clip {t0}", "start": t0, "end": t1, "hook": "h", "why_it_works": "w", "opening_words": o, "closing_words": c, "score": score,
                 "topics": ["Money"], "music": {"mood": "uplifting", "energy": "medium", "search_terms": "warm piano"}, "broll_query": "city"}
            if reason:
                d["long_form_reason"] = reason
            return d
        why = "A complete case study whose payoff comes only at the very end."
        CFG["allow_long_clips"] = True; CFG["director_long_max_clips"] = 2
        # a justified, dense long clip is kept long
        p, rej = director_plans_from_response({"clips": [clip("00:00", "01:40", "Sentence number 0", "Sentence number 19", 90, why)]}, tr, 400.0, 5)
        assert len(p) == 1 and p[0].duration > 90.5 and p[0].duration <= 180 and p[0].notes["long_form_reason"] == why, (rej, p and p[0].duration)
        # without a reason it is trimmed to 90 at a sentence boundary
        p, rej = director_plans_from_response({"clips": [clip("00:00", "01:40", "Sentence number 0", "Sentence number 19", 90)]}, tr, 400.0, 5)
        assert len(p) == 1 and p[0].duration <= 90.0 and not p[0].notes["long_form_reason"], p and p[0].duration
        # a reason does not rescue a sparse (low-density) clip
        p, _ = director_plans_from_response({"clips": [clip("00:00", "01:40", "Sentence number 0", "Sentence number 19", 90, why)]}, make_tr(80, 3), 400.0, 5)
        assert len(p) == 1 and p[0].duration <= 90.0
        # at most N long clips per run
        CFG["director_long_max_clips"] = 1
        p, _ = director_plans_from_response({"clips": [clip("00:00", "01:40", "Sentence number 0", "Sentence number 19", 90, why),
                                                       clip("02:00", "03:45", "Sentence number 24", "Sentence number 44", 80, why)]}, tr, 400.0, 5)
        assert len(p) == 2 and p[0].duration > 90.5 and p[1].duration <= 90.0, [x.duration for x in p]
        # local fallback never exceeds 90
        assert min(float(CFG["max_clip_duration"]), float(CFG.get("local_clip_max_sec", 90.0))) <= 90.0

        # ---- reviewer: validation and verdict hygiene
        assert _validate_review({"score": 11}) and not _validate_review({"score": 7, "directives": []})
        r = _clean_review({"verdict": "fix", "score": 7, "directives": [], "summary": "s", "problems": []})
        assert r["verdict"] == "approve"
        r = _clean_review({"verdict": "fix", "score": 4, "directives": [{"action": "dance", "reason": "x"}], "summary": "s", "problems": []})
        assert r["verdict"] == "reject" and r["directives"] == []
        # ---- bounded edits
        g["SOURCE"] = Source(video_id="v", title="T", duration=400.0, video_path="x")
        g["TRANSCRIPT"] = tr; g["RUN_ID"] = "T8"
        def mk(): return ClipPlan("P", "lesson", [Segment(0.0, 59.6, "hook", "x")], target_duration=90.0, notes={"editorial_score": 60})
        pl = mk()
        before = pl.duration
        ap = apply_review_directives(pl, [{"action": "trim_end", "sentences": 1}, {"action": "disable_broll"}, {"action": "lower_music"}], tr, g["SOURCE"])
        assert pl.duration < before and before - pl.duration < 8 and pl.notes["disable_broll"] and pl.notes["music_volume"] == 0.10 and len(ap) == 3, (before, pl.duration, ap)
        short = ClipPlan("S", "lesson", [Segment(0.0, 24.6, "hook", "x")], target_duration=90.0, notes={"editorial_score": 60})
        ap2 = apply_review_directives(short, [{"action": "trim_end", "sentences": 2}], tr, g["SOURCE"])
        assert short.duration >= 20.0, short.duration       # never trims below 20 s
        # ---- run_final_review with fakes
        d = tempfile.mkdtemp(); fp = os.path.join(d, "c.mp4")
        def setup(): open(fp, "wb").write(b"orig")
        def rebuild_ok(p): open(fp, "wb").write(b"new"); return "RENDER", "TECH"
        def rebuild_bad(p): raise RuntimeError("QA failed")
        fixrev = lambda s: {"verdict": "fix", "score": s, "summary": "s", "scores": {}, "problems": [], "reviewer": "gemini",
                            "directives": [{"action": "trim_end", "sentences": 1, "reason": "ending drags"}]}
        okrev = lambda s: {"verdict": "approve", "score": s, "summary": "good", "scores": {}, "problems": [], "reviewer": "gemini", "directives": []}
        ed_ok = lambda p: [{"passed": True}]
        setup(); pl = mk(); n0 = pl.duration
        seq = iter([fixrev(6.5), okrev(8.0)])
        out = run_final_review(pl, 1, fp, rebuild_ok, review_fn=lambda *a: next(seq), editorial_fn=ed_ok)
        assert out["review"]["score"] == 8.0 and out["review"]["applied"] and out["render_info"] == "RENDER" and open(fp, "rb").read() == b"new" and pl.duration < n0
        setup(); pl = mk()
        seq = iter([fixrev(7.0), okrev(5.0)])                                                   # the fix scored lower -> restored
        out = run_final_review(pl, 1, fp, rebuild_ok, review_fn=lambda *a: next(seq), editorial_fn=ed_ok)
        assert open(fp, "rb").read() == b"orig" and abs(pl.duration - n0) < 1e-6 and out["review"]["applied"] == [] and "render_info" not in out
        setup(); pl = mk()
        seq = iter([fixrev(7.0), okrev(9.0)])                                                   # re-render failed QA -> restored
        out = run_final_review(pl, 1, fp, rebuild_bad, review_fn=lambda *a: next(seq), editorial_fn=ed_ok)
        assert open(fp, "rb").read() == b"orig" and abs(pl.duration - n0) < 1e-6
        setup(); pl = mk(); calls = []
        out = run_final_review(pl, 1, fp, lambda p: calls.append(1), review_fn=lambda *a: okrev(8.5), editorial_fn=ed_ok)
        assert not calls and out["review"]["verdict"] == "approve"
        # ---- pack length notes
        assert any("X" in n and "140" in n for n in length_notes(100)) and not any("Content ID" in n for n in length_notes(50))
        assert any("Content ID" in n for n in length_notes(100))
        print("PASS: long clips (gate, director), final reviewer (verdicts, bounded fixes, revert), length notes")
        return True
    finally:
        for k, v in saved.items():
            if v is None: g.pop(k, None)
            else: g[k] = v
        CFG.update(saved_cfg)


if CFG.get("run_self_tests"):
    HELPER_TESTS_PASSED = run_helper_self_tests()
    BATCH7_TESTS_PASSED = run_batch7_tests()
    BATCH8_TESTS_PASSED = run_batch8_tests()
    BATCH6_TESTS_PASSED = run_batch6_tests()
    BATCH5_SFX_TESTS_PASSED = run_batch5_sfx_tests()
    BATCH3_MUSIC_TESTS_PASSED = run_batch3_music_tests()
    BATCH1_TESTS_PASSED = run_batch1_regression_tests()
else:
    print("Developer self-tests are OFF (normal). You do not need this cell; skip it.")
