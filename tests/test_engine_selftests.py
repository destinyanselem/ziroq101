"""Runs the engine's OWN self-tests (the notebook's fake-client tests) against the ported engine.
They make no network calls and use no keys. Also proves the worker's patches still match the engine source exactly once."""
import os, sys, pytest
import engine_loader as L, run_engine as R

@pytest.fixture(scope="module")
def ns(tmp_path_factory):
    work = str(tmp_path_factory.mktemp("engine"))
    ns = L.load_definitions({"__name__": "ziroq_engine", "DRIVE_AVAILABLE": False, "DRIVE_ROOT": os.path.join(work, "staging")}, work_dir=work, cells=L.DEFINITION_CELLS + L.TEST_CELLS + ["21_cell_13_optional_developers_only.py"])
    ns["CFG"]["work_dir"] = work
    for d in ("RUN_DIR", "CACHE_ROOT", "ASSET_DIR", "RENDER_DIR", "REPORT_DIR"):
        if d in ns:
            os.makedirs(ns[d], exist_ok=True)
    for k in list(os.environ):
        if k.startswith(("GEMINI_API_KEY", "GOOGLE_API_KEY")):
            del os.environ[k]
    ns["GEMINI"] = ns["setup_gemini"]()           # keyless => disabled pool, exactly like a real run without keys
    assert ns["GEMINI"].enabled is False
    return ns

def test_patches_match_engine_source_exactly_once():
    for cell, p in R.build_patches().items():
        src = L.strip_magics(L.read_cell(cell))
        out = p(src) if callable(p) else L.apply_replacements(src, p, cell)
        compile(out, cell, "exec")

def test_a_changed_engine_is_detected():
    with pytest.raises(RuntimeError):
        L.apply_replacements("print(1)", [("print(2)", "x")], "demo")

def test_engine_has_the_v6_capabilities_the_addendum_requires(ns):
    cfg = ns["CFG"]
    assert cfg["max_clip_duration"] == 180.0 and cfg["local_clip_max_sec"] == 90.0 and cfg["director_long_max_clips"] == 2 and cfg["review_min_score"] == 6.0
    for fn in ("long_clip_gate", "review_final_clip", "run_final_review", "length_notes", "build_publishing_packs", "finish_plans"):
        assert callable(ns.get(fn)), fn

def test_gemini_core_selftests(ns):
    ns["run_gemini_core_tests"]()

def test_director_selftests(ns):
    ns["run_director_tests"]()

@pytest.mark.parametrize("name", ["run_helper_self_tests", "run_batch1_regression_tests", "run_batch3_music_tests", "run_batch5_sfx_tests",
                                  "run_batch6_tests", "run_batch7_tests", "run_batch8_tests"])
def test_notebook_helper_tests(ns, name):
    ns[name]()
