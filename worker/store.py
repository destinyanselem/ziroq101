"""Job store. Two implementations behind one small interface:
  FirestoreStore  - real (firebase-admin, Spark plan). NOT run in this build environment: tested only through the shared contract with the fake.
  MemoryStore     - in-memory twin used by the tests (same claim / stale-job rules).
Collections: settings/owner, jobs/{id}, clips/{id}. Timestamps are epoch seconds (floats) to keep rules and code simple.
"""
import threading
import time

JOB_STALE_SECONDS = 150 * 60      # a 'running' job with no update for this long is failed
DEFAULT_SETTINGS = {"geminiConsent": False, "requireApproval": True, "discoveryEnabled": False, "maxSourceMinutes": 90}


class MemoryStore:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.jobs, self.clips, self.settings = {}, {}, dict(DEFAULT_SETTINGS)
        self._lock = threading.Lock()

    def get_settings(self):
        return dict(DEFAULT_SETTINGS, **self.settings)

    def add_job(self, job_id, url, origin="manual", **extra):
        self.jobs[job_id] = {"url": url, "origin": origin, "status": "queued", "progress": 0, "stage": "queued",
                             "createdAt": self.clock(), "updatedAt": self.clock(), "tail": [], **extra}

    def claim_next_job(self, worker_id):
        with self._lock:
            queued = sorted((j["createdAt"], jid) for jid, j in self.jobs.items() if j["status"] == "queued")
            if not queued:
                return None
            jid = queued[0][1]
            self.jobs[jid].update(status="running", startedAt=self.clock(), updatedAt=self.clock(), worker=worker_id)
            return dict(self.jobs[jid], id=jid)

    def update_job(self, job_id, fields):
        self.jobs[job_id].update(fields, updatedAt=self.clock())

    def fail_stale_jobs(self, stale_seconds=JOB_STALE_SECONDS):
        now, failed = self.clock(), []
        for jid, j in self.jobs.items():
            if j["status"] == "running" and now - j.get("updatedAt", now) > stale_seconds:
                j.update(status="failed", error="The worker stopped responding (timed out). Tap 'Make clips' again to retry.", updatedAt=now)
                failed.append(jid)
        return failed

    def has_queued(self):
        return any(j["status"] == "queued" for j in self.jobs.values())

    def write_clip(self, clip_id, fields):
        self.clips[clip_id] = dict(fields)


class FirestoreStore:
    def __init__(self, service_account_info, clock=time.time):
        import firebase_admin
        from firebase_admin import credentials, firestore
        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.Certificate(service_account_info))
        self.db = firestore.client()
        self._firestore = firestore
        self.clock = clock

    def get_settings(self):
        snap = self.db.collection("settings").document("owner").get()
        return dict(DEFAULT_SETTINGS, **(snap.to_dict() or {})) if snap.exists else dict(DEFAULT_SETTINGS)

    def has_queued(self):
        docs = list(self.db.collection("jobs").where("status", "==", "queued").limit(1).stream())
        return bool(docs)

    def claim_next_job(self, worker_id):
        firestore = self._firestore
        # equality filter only (no orderBy): a composite index would have to be created by hand. Oldest is picked here.
        docs = list(self.db.collection("jobs").where("status", "==", "queued").limit(50).stream())
        if not docs:
            return None
        docs.sort(key=lambda d: (d.to_dict() or {}).get("createdAt", 0))
        ref = docs[0].reference

        @firestore.transactional
        def _claim(tx):
            snap = ref.get(transaction=tx)
            data = snap.to_dict() or {}
            if data.get("status") != "queued":
                return None                         # someone else claimed it first
            now = self.clock()
            tx.update(ref, {"status": "running", "startedAt": now, "updatedAt": now, "worker": worker_id})
            return dict(data, id=ref.id, status="running")

        return _claim(self.db.transaction())

    def update_job(self, job_id, fields):
        self.db.collection("jobs").document(job_id).update(dict(fields, updatedAt=self.clock()))

    def fail_stale_jobs(self, stale_seconds=JOB_STALE_SECONDS):
        cutoff, failed = self.clock() - stale_seconds, []
        for d in self.db.collection("jobs").where("status", "==", "running").stream():
            if (d.to_dict() or {}).get("updatedAt", cutoff + 1) < cutoff:
                d.reference.update({"status": "failed", "updatedAt": self.clock(),
                                    "error": "The worker stopped responding (timed out). Tap 'Make clips' again to retry."})
                failed.append(d.id)
        return failed

    def write_clip(self, clip_id, fields):
        self.db.collection("clips").document(clip_id).set(fields)
