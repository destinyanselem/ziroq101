import os, threading, time, pytest
import store as S, main as M

def test_claim_is_exclusive_under_race():
    st = S.MemoryStore()
    for i in range(3): st.add_job(f"j{i}", "u"); st.jobs[f"j{i}"]["createdAt"] = i
    got = []
    ts = [threading.Thread(target=lambda: got.append(st.claim_next_job("w"))) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    ids = sorted(g["id"] for g in got if g)
    assert ids == ["j0", "j1", "j2"]                # each job claimed once; oldest first; 5 claimers got nothing

def test_fail_stale_jobs():
    t = [1000.0]; st = S.MemoryStore(clock=lambda: t[0]); st.add_job("a", "u"); st.claim_next_job("w")
    t[0] += 100; assert st.fail_stale_jobs(stale_seconds=600) == []
    t[0] += 1000; assert st.fail_stale_jobs(stale_seconds=600) == ["a"]
    assert st.jobs["a"]["status"] == "failed" and "Tap" in st.jobs["a"]["error"]

def test_has_queued():
    st = S.MemoryStore(); assert not st.has_queued(); st.add_job("a", "u"); assert st.has_queued()

def test_plain_failure_messages():
    assert "cookies" in M.plain_failure(RuntimeError("Sign in to confirm you're not a bot")).lower()
    assert "Tap" in M.plain_failure(RuntimeError("weird"))
    assert "refresh token" in M.plain_failure(RuntimeError("Google rejected the Drive refresh token")).lower()

# ---- process_job end to end with a fake engine and a fake Drive
class FakeDrive:
    def __init__(self, fail_upload=False): self.fail_upload = fail_upload
    def find_or_create_folder(self, n, p=None): return "ROOT"
    def create_folder(self, n, p=None): return "RUN"
    def upload_tree(self, d, parent):
        if self.fail_upload: raise RuntimeError("boom")
        m = {}
        for r, _, fs in os.walk(d):
            for f in fs: m[os.path.relpath(os.path.join(r, f), d)] = {"id": "i", "url": "https://drive/" + f, "size": 1}
        return m, {"": parent, "PUBLISHING_PACK": "PACKF"}
    def folder_url(self, i): return "https://drive/folder/" + i

@pytest.fixture
def fake_engine(tmp_path, monkeypatch):
    import types, sys
    deliver = tmp_path / "deliver"; (deliver / "FINAL_VIDEOS").mkdir(parents=True); (deliver / "COVERS").mkdir(); (deliver / "PUBLISHING_PACK").mkdir()
    clips = []
    for i in (1, 2, 3):
        v, c = deliver / "FINAL_VIDEOS" / f"0{i}_t.mp4", deliver / "COVERS" / f"0{i}_COVER.jpg"; v.write_bytes(b"v"); c.write_bytes(b"c")
        clips.append({"index": i, "title": f"Clip {i}", "duration": 42.0 + i, "final_path": str(v), "cover_path": str(c), "technical_pass": True, "editorial_pass": True,
                      "review_flags": ["x"], "review": {"verdict": "approve", "score": 7.5, "summary": "good"}, "editorial_score": 60})
    def run_job(url, work_dir, consent, progress, **kw):
        progress.state("rendering", completed_clips=1, total_clips=3); progress.line("hello AIzaSyA1234567890abcdefghijklmnop")
        return {"delivery_dir": str(deliver), "clips": clips, "source_title": "My Talk", "source_video_id": "dQw4w9WgXcQ", "gemini_enabled": consent,
                "packs": {"youtube": {"1": {"title": "T"}}}, "length_notes": {"1": ["YouTube Shorts: OK"]}, "asr_usage": {"groq": 3, "local": 0}}
    mod = types.ModuleType("run_engine"); mod.run_job = run_job; monkeypatch.setitem(sys.modules, "run_engine", mod)
    monkeypatch.setattr(M, "precheck_source", lambda *a, **k: {"title": "My Talk", "channel": "Ch", "minutes": 20})
    return clips

def test_process_job_success(fake_engine, tmp_path):
    st = S.MemoryStore(); st.settings["geminiConsent"] = True; st.add_job("job1", "https://youtu.be/dQw4w9WgXcQ"); job = st.claim_next_job("w")
    assert M.process_job(st, job, FakeDrive(), {}, str(tmp_path / "w")) is True
    j = st.jobs["job1"]
    assert j["status"] == "done" and j["progress"] == 100 and j["folderUrl"].endswith("RUN") and "AIza" not in str(j)
    assert len(st.clips) == 3 and st.clips["job1_01"]["driveUrl"].endswith("01_t.mp4") and st.clips["job1_01"]["pack"]["youtube"]["title"] == "T"
    assert st.clips["job1_01"]["status"] == "candidate" and st.clips["job1_02"]["packUrl"].endswith("PACKF")

def test_process_job_marks_local_mode_when_no_consent(fake_engine, tmp_path):
    st = S.MemoryStore(); st.add_job("j", "https://youtu.be/dQw4w9WgXcQ"); job = st.claim_next_job("w")
    M.process_job(st, job, FakeDrive(), {}, str(tmp_path / "w")); assert "local mode" in st.jobs["j"]["note"]

def test_process_job_drive_failure_gives_plain_error(fake_engine, tmp_path):
    st = S.MemoryStore(); st.add_job("j", "https://youtu.be/dQw4w9WgXcQ"); job = st.claim_next_job("w")
    assert M.process_job(st, job, FakeDrive(fail_upload=True), {}, str(tmp_path / "w")) is False
    j = st.jobs["j"]; assert j["status"] == "failed" and j["error"] and not st.clips

def test_process_job_rejects_local_path(fake_engine, tmp_path):
    st = S.MemoryStore(); st.add_job("j", "/etc/passwd"); job = st.claim_next_job("w")
    assert M.process_job(st, job, FakeDrive(), {}, str(tmp_path / "w")) is False
    assert "YouTube" in st.jobs["j"]["error"]

def test_process_job_missing_delivery_file_blocks_done(fake_engine, tmp_path):
    class Partial(FakeDrive):
        def upload_tree(self, d, p):
            m, ids = super().upload_tree(d, p); m.pop(os.path.join("COVERS", "02_COVER.jpg")); return m, ids
    st = S.MemoryStore(); st.add_job("j", "https://youtu.be/dQw4w9WgXcQ"); job = st.claim_next_job("w")
    assert M.process_job(st, job, Partial(), {}, str(tmp_path / "w")) is False
    assert "02_COVER.jpg" in st.jobs["j"]["error"]
