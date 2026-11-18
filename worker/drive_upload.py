"""Google Drive delivery with the `drive.file` scope (the app creates and owns its own folders).

  * access token from a refresh token (refreshed again on any 401)
  * resumable uploads in 8 MiB chunks, resumed after interruption, retried on 429 / 5xx / network errors
  * the stored size is compared with the local size after every upload (delivery is verified, not assumed)
  * files are never shared publicly; only the owner's own Drive holds them
All HTTP goes through an injectable `session` so the tests can run with a fake Drive.
"""
import os
import time

TOKEN_URL = "https://oauth2.googleapis.com/token"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
CHUNK = 8 * 1024 * 1024           # must be a multiple of 256 KiB
FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveError(Exception):
    pass


class AuthError(DriveError):
    pass


class DriveClient:
    def __init__(self, client_id, client_secret, refresh_token, session=None, sleep=time.sleep, max_attempts=6):
        import requests
        self.client_id, self.client_secret, self.refresh_token = client_id, client_secret, refresh_token
        self.session = session or requests.Session()
        self.sleep, self.max_attempts = sleep, max_attempts
        self.token = None
        self.refreshes = 0

    # ------------------------------------------------------------------ auth
    def refresh(self):
        r = self.session.post(TOKEN_URL, data={"client_id": self.client_id, "client_secret": self.client_secret,
                                               "refresh_token": self.refresh_token, "grant_type": "refresh_token"}, timeout=30)
        if r.status_code != 200:
            body = ""
            try:
                body = str(r.json().get("error", ""))
            except Exception:
                pass
            if body == "invalid_grant":
                raise AuthError("Google rejected the Drive refresh token (invalid_grant). If the Google Cloud consent screen is still in "
                                "'Testing', tokens expire after 7 days: set it to 'In production' and create a new token (SETUP GUIDE, Drive).")
            raise AuthError(f"Could not refresh the Drive token (HTTP {r.status_code}).")
        self.token = r.json()["access_token"]
        self.refreshes += 1

    def _headers(self, extra=None):
        if not self.token:
            self.refresh()
        h = {"Authorization": f"Bearer {self.token}"}
        h.update(extra or {})
        return h

    def _request(self, method, url, ok=(200, 201), **kw):
        """Retries 401 (one refresh), 429/5xx and network errors. Returns the response."""
        last = ""
        refreshed = False
        extra = kw.pop("headers_extra", None)
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = self.session.request(method, url, headers=self._headers(extra), timeout=120, **kw)
            except Exception as exc:
                last = f"network error {type(exc).__name__}"
                self.sleep(min(2 ** attempt, 30))
                continue
            code = resp.status_code
            if code in ok:
                return resp
            if code == 401 and not refreshed:
                refreshed = True
                self.refresh()
                continue
            if code == 429 or code >= 500:
                last = f"HTTP {code}"
                self.sleep(min(2 ** attempt, 30))
                continue
            raise DriveError(f"Drive request failed: HTTP {code} {str(getattr(resp, 'text', ''))[:200]}")
        raise DriveError(f"Drive request failed after {self.max_attempts} attempts ({last})")

    # ------------------------------------------------------------------ folders
    def find_or_create_folder(self, name, parent_id=None):
        safe = name.replace("\\", "\\\\").replace("'", "\\'")
        q = f"name = '{safe}' and mimeType = '{FOLDER_MIME}' and trashed = false"
        q += f" and '{parent_id}' in parents" if parent_id else " and 'root' in parents"
        r = self._request("GET", FILES_URL, params={"q": q, "fields": "files(id,name)", "pageSize": 1})
        files = r.json().get("files", [])
        if files:
            return files[0]["id"]
        meta = {"name": name, "mimeType": FOLDER_MIME}
        if parent_id:
            meta["parents"] = [parent_id]
        r = self._request("POST", FILES_URL, params={"fields": "id"}, json=meta)
        return r.json()["id"]

    def create_folder(self, name, parent_id=None):
        meta = {"name": name, "mimeType": FOLDER_MIME}
        if parent_id:
            meta["parents"] = [parent_id]
        return self._request("POST", FILES_URL, params={"fields": "id"}, json=meta).json()["id"]

    # ------------------------------------------------------------------ files
    def upload_file(self, local_path, parent_id, name=None, mime="application/octet-stream"):
        size = os.path.getsize(local_path)
        name = name or os.path.basename(local_path)
        init = self._request("POST", UPLOAD_URL, params={"uploadType": "resumable", "fields": "id,size,webViewLink"},
                             json={"name": name, "parents": [parent_id]},
                             headers_extra={"X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(size)})
        session_url = init.headers.get("Location")
        if not session_url:
            raise DriveError("Drive did not return a resumable upload address")
        if size == 0:
            r = self.session.put(session_url, data=b"", headers=self._headers({"Content-Length": "0"}), timeout=120)
            return self._finish(r, local_path, size)
        offset, failures = 0, 0
        with open(local_path, "rb") as f:
            while offset < size:
                f.seek(offset)
                data = f.read(CHUNK)
                end = offset + len(data) - 1
                headers = self._headers({"Content-Length": str(len(data)), "Content-Range": f"bytes {offset}-{end}/{size}"})
                try:
                    r = self.session.put(session_url, data=data, headers=headers, timeout=300)
                except Exception:
                    r = None
                if r is not None and r.status_code in (200, 201):
                    return self._finish(r, local_path, size)
                if r is not None and r.status_code == 308:
                    rng = r.headers.get("Range", "")
                    offset = int(rng.split("-")[-1]) + 1 if rng else 0
                    failures = 0
                    continue
                if r is not None and r.status_code == 401:
                    self.refresh()
                    continue
                failures += 1
                if failures > self.max_attempts:
                    raise DriveError(f"Upload of {name} kept failing (last HTTP {getattr(r, 'status_code', 'network error')})")
                self.sleep(min(2 ** failures, 30))
                offset = self._query_offset(session_url, size, offset)
        raise DriveError(f"Upload of {name} ended without a completion response")

    def _query_offset(self, session_url, size, fallback):
        try:
            r = self.session.put(session_url, data=b"", headers=self._headers({"Content-Length": "0", "Content-Range": f"bytes */{size}"}), timeout=60)
            if r.status_code == 308:
                rng = r.headers.get("Range", "")
                return int(rng.split("-")[-1]) + 1 if rng else 0
        except Exception:
            pass
        return fallback

    @staticmethod
    def _finish(resp, local_path, size):
        info = resp.json()
        remote = int(info.get("size", -1)) if str(info.get("size", "")).isdigit() else None
        if remote is not None and remote != size:
            raise DriveError(f"Drive size check failed for {os.path.basename(local_path)}: local {size} bytes, Drive {remote} bytes")
        return {"id": info.get("id"), "url": info.get("webViewLink") or (f"https://drive.google.com/file/d/{info.get('id')}/view"), "size": size}

    # ------------------------------------------------------------------ whole tree
    def upload_tree(self, local_dir, parent_id, skip_ext=(".tmp", ".part")):
        """Mirrors local_dir under parent_id. Returns {relative_path: {id,url,size}} for every file, plus folder ids."""
        mapping, folder_ids = {}, {"": parent_id}
        for root, dirs, files in os.walk(local_dir):
            dirs.sort()
            rel_root = os.path.relpath(root, local_dir)
            rel_root = "" if rel_root == "." else rel_root
            if rel_root not in folder_ids:
                parent_rel = os.path.dirname(rel_root)
                folder_ids[rel_root] = self.create_folder(os.path.basename(rel_root), folder_ids[parent_rel])
            for fname in sorted(files):
                if fname.endswith(skip_ext):
                    continue
                path = os.path.join(root, fname)
                rel = os.path.join(rel_root, fname) if rel_root else fname
                mapping[rel] = self.upload_file(path, folder_ids[rel_root], fname, guess_mime(fname))
        return mapping, folder_ids

    def folder_url(self, folder_id):
        return f"https://drive.google.com/drive/folders/{folder_id}"


def guess_mime(name):
    ext = os.path.splitext(name)[1].lower()
    return {".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".json": "application/json",
            ".md": "text/markdown", ".txt": "text/plain", ".csv": "text/csv"}.get(ext, "application/octet-stream")
