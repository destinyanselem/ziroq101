import os, pytest
import drive_upload as D

class R:
    def __init__(self, code, payload=None, headers=None, text=""): self.status_code, self._p, self.headers, self.text = code, payload or {}, headers or {}, text
    def json(self): return self._p

class FakeDrive:
    """Minimal fake of token + files + resumable upload endpoints, with injectable faults."""
    def __init__(self):
        self.files, self.folders, self.received, self.n = {}, {}, {}, 0
        self.faults = []                   # list of ("put"|"request"|"token", response-or-exception) consumed in order
        self.token_ok, self.tokens_issued, self.size_lie = True, 0, None
    def _fault(self, kind):
        for i, (k, v) in enumerate(self.faults):
            if k == kind:
                self.faults.pop(i)
                if isinstance(v, Exception): raise v
                return v
    def post(self, url, data=None, timeout=None):
        assert url == D.TOKEN_URL
        if not self.token_ok: return R(400, {"error": "invalid_grant"})
        self.tokens_issued += 1
        return R(200, {"access_token": f"tok{self.tokens_issued}"})
    def request(self, method, url, headers=None, timeout=None, params=None, json=None):
        f = self._fault("request")
        if f is not None: return f
        if url == D.FILES_URL and method == "GET":
            name = params["q"].split("'")[1]
            return R(200, {"files": [{"id": self.folders[name]}] if name in self.folders else []})
        if url == D.FILES_URL and method == "POST":
            self.n += 1; fid = f"F{self.n}"; self.folders[json["name"]] = fid; return R(200, {"id": fid})
        if url == D.UPLOAD_URL and method == "POST":
            self.n += 1; sid = f"S{self.n}"; self.received[sid] = [0, json["name"], int(headers["X-Upload-Content-Length"])]
            return R(200, headers={"Location": f"https://upload/{sid}"})
        raise AssertionError((method, url))
    def put(self, url, data=b"", headers=None, timeout=None):
        sid = url.rsplit("/", 1)[1]
        f = self._fault("put")
        if f is not None: return f
        rec = self.received[sid]; cr = headers.get("Content-Range", "")
        if cr.startswith("bytes */"):
            return R(308, headers={"Range": f"bytes=0-{rec[0]-1}"} if rec[0] else {})
        start = int(cr.split()[1].split("-")[0])
        assert start == rec[0], f"gap: got {start}, have {rec[0]}"
        rec[0] += len(data)
        if rec[0] >= rec[2]:
            self.files[rec[1]] = rec[0]
            return R(200, {"id": "FILE_" + rec[1], "size": str(self.size_lie if self.size_lie is not None else rec[0]), "webViewLink": "https://drive/x"})
        return R(308, headers={"Range": f"bytes=0-{rec[0]-1}"})

def mk(fd): return D.DriveClient("id", "sec", "ref", session=fd, sleep=lambda s: None)

def test_refresh_invalid_grant_message():
    fd = FakeDrive(); fd.token_ok = False
    with pytest.raises(D.AuthError) as e: mk(fd).refresh()
    assert "In production" in str(e.value)

def test_multi_chunk_upload_and_size_verified(tmp_path):
    p = tmp_path / "a.mp4"; p.write_bytes(os.urandom(D.CHUNK * 2 + 123))
    fd = FakeDrive(); info = mk(fd).upload_file(str(p), "PARENT")
    assert info["size"] == D.CHUNK * 2 + 123 and fd.files["a.mp4"] == D.CHUNK * 2 + 123

def test_resume_after_network_error_midway(tmp_path):
    p = tmp_path / "a.mp4"; p.write_bytes(os.urandom(D.CHUNK * 2 + 5))
    fd = FakeDrive(); c = mk(fd)
    fd.faults = [("put", R(200)) if False else ("put", ConnectionError("drop"))]
    info = c.upload_file(str(p), "PARENT")
    assert fd.files["a.mp4"] == D.CHUNK * 2 + 5 and info["id"]

def test_5xx_then_ok(tmp_path):
    p = tmp_path / "a.jpg"; p.write_bytes(b"hello")
    fd = FakeDrive(); fd.faults = [("request", R(503)), ("put", R(500))]
    assert mk(fd).upload_file(str(p), "P")["size"] == 5

def test_401_triggers_one_refresh(tmp_path):
    p = tmp_path / "a.jpg"; p.write_bytes(b"hello")
    fd = FakeDrive(); c = mk(fd); c.refresh(); before = fd.tokens_issued
    fd.faults = [("request", R(401))]
    c.upload_file(str(p), "P")
    assert fd.tokens_issued == before + 1

def test_size_mismatch_is_an_error(tmp_path):
    p = tmp_path / "a.mp4"; p.write_bytes(b"12345")
    fd = FakeDrive(); fd.size_lie = 4
    with pytest.raises(D.DriveError) as e: mk(fd).upload_file(str(p), "P")
    assert "size check" in str(e.value)

def test_persistent_failure_raises(tmp_path):
    p = tmp_path / "a.mp4"; p.write_bytes(b"12345")
    fd = FakeDrive(); fd.faults = [("request", R(500))] * 20
    with pytest.raises(D.DriveError): mk(fd).upload_file(str(p), "P")

def test_empty_file(tmp_path):
    p = tmp_path / "e.txt"; p.write_bytes(b"")
    fd = FakeDrive(); fd.put = lambda url, data=b"", headers=None, timeout=None: R(200, {"id": "E", "size": "0", "webViewLink": "u"})
    assert mk(fd).upload_file(str(p), "P")["size"] == 0

def test_upload_tree_mirrors_folders(tmp_path):
    (tmp_path / "FINAL_VIDEOS").mkdir(); (tmp_path / "COVERS").mkdir()
    (tmp_path / "FINAL_VIDEOS" / "01_x.mp4").write_bytes(b"v"); (tmp_path / "COVERS" / "01_COVER.jpg").write_bytes(b"c"); (tmp_path / "REPORT.json").write_bytes(b"{}")
    (tmp_path / "junk.part").write_bytes(b"x")
    fd = FakeDrive(); m, ids = mk(fd).upload_tree(str(tmp_path), "ROOT")
    assert set(m) == {os.path.join("FINAL_VIDEOS", "01_x.mp4"), os.path.join("COVERS", "01_COVER.jpg"), "REPORT.json"}
    assert "FINAL_VIDEOS" in ids and "COVERS" in ids
