# CELL 3b — Gemini core: consent, key manager, quota-aware scheduler, structured client, stage tracker
# Purpose : make Gemini usable as ZIROQ's editorial director WITHOUT ever being a single point of failure.
# Contracts: gemini_structured(...) -> {"ok": bool, "data": ..., "fallback_required": bool, ...}; it never raises for
#            availability problems, so every caller keeps its local fallback. API keys are read from Colab Secrets,
#            are never printed/logged/persisted, and every message passes through gem_scrub().
# API facts used (Google docs, checked 2026-11-13): google-genai >= 2.3.0, client.interactions.create(model, input,
#            response_format={"type":"text","mime_type":"application/json","schema":...}, store=False);
#            rate limits are applied PER PROJECT (not per key); unpaid-tier data may be human-reviewed.
import base64, importlib.metadata
from datetime import timedelta
from zoneinfo import ZoneInfo

GEM_SLEEP = time.sleep          # indirection so tests can run instantly
GEM_NOW = time.time
GEMINI_CACHE_DIR = os.path.join(CACHE_ROOT, "gemini_cache")
GEMINI_STATE_PATH = os.path.join(DRIVE_ROOT if DRIVE_AVAILABLE else CACHE_ROOT, "_GEMINI_STATE", "gemini_state.json")

GEMINI_DOC_FACTS = {
    "as_of": "2026-11-13",
    "agentic_video_models_documented": ["gemini-4-argon", "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"],
    "file_api_retention_hours": 48,
    "unpaid_terms": "unpaid-tier input/output may be used to improve Google products and read by human reviewers",
    "rate_limit_scope": "per project, not per API key; RPD resets at midnight Pacific",
}

CONSENT_TEXT = """
================ EXTERNAL AI PROCESSING — READ BEFORE CONTINUING ================
ZIROQ can send your TRANSCRIPT (and, in later steps, video frames/clips) to Google's Gemini API.
* On Google's UNPAID tier, Google's terms say submitted content may be used to improve its products and may be read
  by human reviewers (disconnected from your account). Do not send sensitive, confidential or personal material.
* ZERO_COST_MODE cannot detect whether a project has billing enabled. To stay at $0, use keys created in Google AI Studio
  projects that have NO billing account linked. Hard per-run request caps below limit worst-case usage either way.
* Answer anything other than the exact phrase to run LOCAL-ONLY (all fallbacks still produce your five clips).
==================================================================================="""


class GeminiUnavailable(Exception):
    """No key/model can serve the request right now. Callers must use their local fallback."""
    def __init__(self, message, waiting_for_quota=False, attempts=0):
        super().__init__(message)
        self.waiting_for_quota, self.attempts = waiting_for_quota, attempts


class GeminiRequestError(Exception):
    """The request itself is invalid (bad schema/arguments); retrying or rotating keys cannot help."""


# ----------------------------------------------------------------------------- secrets & scrubbing
_GEM_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")
_GEM_KNOWN_SECRETS = []


def gem_scrub(text):
    text = str(text)
    for secret in _GEM_KNOWN_SECRETS:
        if secret:
            text = text.replace(secret, "[redacted-key]")
    return _GEM_KEY_RE.sub("[redacted-key]", text)


def gem_log(message):
    log("gemini", gem_scrub(message))


def _gemini_secret(name):
    value = os.environ.get(name, "")
    if value:
        return str(value).strip()
    try:
        from google.colab import userdata
        return str(userdata.get(name) or "").strip()
    except Exception:
        return ""


def detect_gemini_keys():
    found, seen = [], set()
    def add(value):
        value = str(value or "").strip().strip("\"'")
        if value and value not in seen:
            seen.add(value); found.append(value)
    for name in [f"GEMINI_API_KEY_{i}" for i in range(1, 13)] + ["GEMINI_API_KEY", "GOOGLE_API_KEY"]:
        add(_gemini_secret(name))
    for part in re.split(r"[,\s;]+", _gemini_secret("GEMINI_API_KEYS") or ""):
        add(part)
    return [(f"key{i}", k) for i, k in enumerate(found, 1)]


def gemini_project_map(labels):
    """Key label -> project label. Default follows CFG['gemini_keys_are_separate_projects']; override with a secret
    GEMINI_KEY_PROJECTS such as 'key1=A,key2=A,key3=B' when some keys share a project (they then share one quota)."""
    explicit = {}
    for part in re.split(r"[,\n;]+", _gemini_secret("GEMINI_KEY_PROJECTS") or ""):
        if "=" in part:
            a, b = part.split("=", 1)
            explicit[a.strip()] = b.strip()
    separate = CFG.get("gemini_keys_are_separate_projects", True)
    return {lab: explicit.get(lab, f"project-{lab}" if separate else "project-shared") for lab in labels}


# ----------------------------------------------------------------------------- error classification
def classify_gemini_error(exc):
    text = gem_scrub(f"{type(exc).__name__}: {exc}")
    low = text.lower()
    code = None
    for attr in ("code", "status_code", "http_status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            code = v; break
        if isinstance(v, str) and v.isdigit():
            code = int(v); break
    if code is None:
        m = re.search(r"\b(400|401|403|404|408|429|500|502|503|504)\b", low)
        code = int(m.group(1)) if m else None
    retry_after = None
    m = re.search(r"retry(?:\s*in|delay['\"]?\s*[:=]\s*['\"]?)\s*(\d+(?:\.\d+)?)\s*s", low)
    if m:
        retry_after = float(m.group(1))
    auth_markers = ("api key not valid", "api_key_invalid", "api key expired", "unauthenticated", "permission_denied",
                    "reported as leaked", "key has been revoked", "invalid api key", "consumer_suspended",
                    "api_key_service_blocked", "api key not found")
    if code == 401 or code == 403 or any(x in low for x in auth_markers):
        kind = "auth"
    elif code == 429 or "resource_exhausted" in low or "quota" in low:
        if "limit: 0" in low:
            kind = "quota_zero"
        elif any(x in low for x in ("perday", "per day", "daily", "requestsperday")):
            kind = "quota_daily"
        else:
            kind = "rate_limit"
    elif code == 404 or "not_found" in low or "no longer available" in low or "is not supported" in low:
        kind = "model_unavailable"
    elif code == 400 or "invalid_argument" in low:
        kind = "bad_request"
    elif isinstance(exc, TimeoutError) or code in (408, 504) or any(x in low for x in ("timeout", "timed out", "deadline")):
        kind = "timeout"
    elif code in (500, 502, 503) or any(x in low for x in ("unavailable", "internal", "overloaded", "connection", "reset by peer")):
        kind = "transient"
    else:
        kind = "unknown"
    return {"kind": kind, "code": code, "retry_after": retry_after, "message": text[:400]}


def _next_pacific_midnight(now):
    tz = ZoneInfo("America/Los_Angeles")
    dt = datetime.fromtimestamp(now, tz)
    return (dt + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0).timestamp()


def _default_client_factory(key):
    from google import genai
    timeout_ms = int(float(CFG.get("gemini_request_timeout_sec", 180)) * 1000)
    try:
        return genai.Client(api_key=key, http_options={"timeout": timeout_ms})
    except TypeError:
        return genai.Client(api_key=key)


class GeminiResult:
    def __init__(self, text, model, project, key_label, attempts):
        self.text, self.model, self.project, self.key_label, self.attempts = text, model, project, key_label, attempts


# ----------------------------------------------------------------------------- key + quota manager
class GeminiPool:
    """Credential failover + PER-PROJECT quota tracking. Keys of the same project share one cooldown, so rotating
    among them never bypasses a quota; keys of different projects are tracked independently."""

    def __init__(self, keys, client_factory=None, state_path=None, project_map=None):
        project_map = project_map or {}
        self.client_factory = client_factory or _default_client_factory
        self.state_path = state_path
        self.entries = []
        for label, key in keys:
            _GEM_KNOWN_SECRETS.append(key)
            self.entries.append({"label": label, "project": project_map.get(label, f"project-{label}"), "key": key,
                                 "fp": hashlib.sha256(key.encode()).hexdigest()[:8], "client": None,
                                 "auth_failed": False, "auth_ts": 0.0, "fail_streak": 0, "cool_until": 0.0,
                                 "requests": 0, "errors": 0, "last_call": 0.0, "validated": 0.0})
        self.cooldowns, self.blocked = {}, {}
        self.requests_made = 0
        self.timeouts_in_row, self.breaker_until = 0, 0.0
        self.enabled = bool(self.entries)
        self.disabled_reason = "" if self.entries else "no Gemini API keys configured"
        self.capabilities, self.call_log = {}, []
        self._load_state()

    # -- persistence of NON-SENSITIVE operational state (fingerprints only, never keys)
    def _load_state(self):
        self.state = {"keys": {}, "cooldowns": {}, "blocked": {}}
        try:
            if self.state_path and os.path.isfile(self.state_path):
                with open(self.state_path, encoding="utf-8") as f:
                    self.state = json.load(f)
        except Exception:
            self.state = {"keys": {}, "cooldowns": {}, "blocked": {}}
        now = GEM_NOW()
        for e in self.entries:
            ks = self.state.get("keys", {}).get(e["fp"], {})
            if ks.get("auth_failed") and now - ks.get("auth_ts", 0) < 12 * 3600:
                e["auth_failed"], e["auth_ts"] = True, ks.get("auth_ts", 0)
            e["validated"] = ks.get("validated", 0.0)
            for model, until in self.state.get("cooldowns", {}).get(e["fp"], {}).items():
                if until > now:
                    self.cooldowns[(e["project"], model)] = max(self.cooldowns.get((e["project"], model), 0), until)
            for model, reason in self.state.get("blocked", {}).get(e["fp"], {}).items():
                self.blocked[(e["project"], model)] = reason

    def save_state(self):
        if not self.state_path:
            return
        keys, cds, blk = {}, {}, {}
        for e in self.entries:
            keys[e["fp"]] = {"auth_failed": e["auth_failed"], "auth_ts": e["auth_ts"], "validated": e["validated"],
                             "requests": e["requests"], "errors": e["errors"], "label": e["label"], "project": e["project"]}
            cds[e["fp"]] = {m: u for (p, m), u in self.cooldowns.items() if p == e["project"] and u > GEM_NOW()}
            blk[e["fp"]] = {m: r for (p, m), r in self.blocked.items() if p == e["project"]}
        try:
            atomic_write_json(self.state_path, {"saved_utc": datetime.now(timezone.utc).isoformat(),
                                                "keys": keys, "cooldowns": cds, "blocked": blk})
        except Exception:
            pass

    # -- selection
    def _client(self, entry):
        if entry["client"] is None:
            entry["client"] = self.client_factory(entry["key"])
        return entry["client"]

    def _select(self, model, pin=None):
        """Return (entry, 0) for a usable key, or (None, seconds_until_one_frees) or (None, None) if none ever will.
        `pin` restricts selection to one key label (uploaded files can only be read by the key/project that uploaded them)."""
        now = GEM_NOW()
        projects = {}
        for e in self.entries:
            if pin is not None and e["label"] != pin:
                continue
            projects.setdefault(e["project"], []).append(e)
        best, min_wait = None, None
        for proj, es in projects.items():
            if (proj, model) in self.blocked:
                continue
            healthy = [e for e in es if not e["auth_failed"]]
            if not healthy:
                continue
            cd = self.cooldowns.get((proj, model), 0.0)
            if cd > now:
                min_wait = (cd - now) if min_wait is None else min(min_wait, cd - now)
                continue
            avail = [e for e in healthy if e["cool_until"] <= now]
            if not avail:
                wait = min(e["cool_until"] for e in healthy) - now
                min_wait = wait if min_wait is None else min(min_wait, wait)
                continue
            proj_last = max(e["last_call"] for e in es)
            cand = min(avail, key=lambda e: e["last_call"])
            if best is None or proj_last < best[0]:
                best = (proj_last, cand)
        if best:
            return best[1], 0.0
        return None, min_wait

    def usable_projects(self, model):
        return sorted({e["project"] for e in self.entries if not e["auth_failed"] and (e["project"], model) not in self.blocked})

    def _pace(self, entry):
        interval = float(CFG.get("gemini_min_interval_sec", 4.0))
        proj_last = max(e["last_call"] for e in self.entries if e["project"] == entry["project"])
        gap = interval - (GEM_NOW() - proj_last)
        if gap > 0:
            GEM_SLEEP(gap)

    def _invoke(self, entry, model, input, response_format, system_instruction, generation_config):
        client = self._client(entry)
        kwargs = {"model": model, "input": input, "store": False}   # store=False: do not keep prompts server-side
        if response_format:
            kwargs["response_format"] = response_format
        if system_instruction:
            kwargs["system_instruction"] = system_instruction
        if generation_config:
            kwargs["generation_config"] = generation_config
        return client.interactions.create(**kwargs)

    def _handle_error(self, entry, model, info):
        kind, now, proj = info["kind"], GEM_NOW(), entry["project"]
        if kind == "auth":
            entry["auth_failed"], entry["auth_ts"] = True, now
            gem_log(f"{entry['label']} rejected (authentication/permission); excluded for this session")
        elif kind == "rate_limit":
            wait = max(5.0, info["retry_after"] or 30.0) + random.uniform(0, 3)
            self.cooldowns[(proj, model)] = now + wait
            gem_log(f"{proj}: rate limit on {model}; cooling down {wait:.0f}s (project-level, other keys of this project share it)")
        elif kind == "quota_daily":
            self.cooldowns[(proj, model)] = _next_pacific_midnight(now)
            gem_log(f"{proj}: daily quota exhausted for {model}; paused until midnight Pacific")
        elif kind in ("quota_zero", "model_unavailable"):
            self.blocked[(proj, model)] = kind
            gem_log(f"{proj}: {model} not available to this project ({kind})")
        else:  # transient / timeout / unknown / empty -> exponential backoff with jitter, then fail over to another key
            entry["fail_streak"] += 1
            if kind != "timeout":      # a timeout already cost the full wait; sleeping on top of it only makes the user wait more
                GEM_SLEEP(min(8.0, 2 ** min(entry["fail_streak"], 3) + random.uniform(0, 1.0)))
            if entry["fail_streak"] >= 2:
                entry["cool_until"] = GEM_NOW() + 45.0
            gem_log(f"{entry['label']}: {kind} on {model}; backing off (streak {entry['fail_streak']}) — {gem_scrub(str(info.get('message', '')))[:110]}")
        self.save_state()

    def _invoke_limited(self, remaining, *args):
        """Run one request with a hard wall-clock deadline, whatever the SDK does; an abandoned call is simply ignored."""
        import concurrent.futures as cf
        ex = cf.ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(self._invoke, *args)
        try:
            return fut.result(timeout=max(0.2, min(remaining, 600.0)))
        except cf.TimeoutError:
            raise TimeoutError(f"no answer within {max(0.2, remaining):.0f}s; call abandoned") from None
        finally:
            ex.shutdown(wait=False)

    def generate(self, models, input, response_format=None, system_instruction=None, generation_config=None, task_id="", pin=None):
        t_start = GEM_NOW()
        budget_sec = float(CFG.get("gemini_call_budget_sec", 120))
        if GEM_NOW() < self.breaker_until:
            raise GeminiUnavailable("Gemini timed out repeatedly a moment ago; skipping it for now (local fallback runs)", False, 0)
        max_attempts = int(CFG.get("gemini_max_attempts_per_call", 6))
        max_wait = float(CFG.get("gemini_max_wait_sec", 120))
        budget = int(CFG.get("gemini_max_requests_per_run", 60))
        attempts, waited, last_kind, saw_quota = 0, 0.0, None, False
        for model in models:
            while True:
                if self.requests_made >= budget:
                    raise GeminiUnavailable(f"per-run request budget ({budget}) reached", attempts=attempts)
                entry, wait = self._select(model, pin)
                if entry is None:
                    if wait is None:
                        break
                    if waited + wait > max_wait:
                        saw_quota = True
                        break
                    GEM_SLEEP(wait + random.uniform(0, 1)); waited += wait
                    continue
                if attempts >= max_attempts:
                    raise GeminiUnavailable(f"gave up after {attempts} attempts (last: {last_kind})", saw_quota, attempts)
                remaining = budget_sec - (GEM_NOW() - t_start)
                if remaining <= 0.5:
                    raise GeminiUnavailable(f"time budget of {budget_sec:.0f}s reached (last: {last_kind})", saw_quota, attempts)
                attempts += 1
                self._pace(entry)
                self.requests_made += 1
                entry["requests"] += 1
                entry["last_call"] = GEM_NOW()
                try:
                    interaction = self._invoke_limited(remaining, entry, model, input, response_format, system_instruction, generation_config)
                    text = getattr(interaction, "output_text", None)
                    if not text or not str(text).strip():
                        raise ValueError("empty output (possibly blocked by safety filters)")
                    entry["fail_streak"] = 0
                    self.timeouts_in_row = 0
                    self.call_log.append({"task": task_id, "model": model, "project": entry["project"], "key": entry["label"],
                                          "utc": datetime.now(timezone.utc).isoformat()})
                    self.save_state()
                    return GeminiResult(str(text), model, entry["project"], entry["label"], attempts)
                except Exception as exc:
                    info = classify_gemini_error(exc)
                    last_kind = info["kind"]
                    entry["errors"] += 1
                    saw_quota = saw_quota or info["kind"] in ("rate_limit", "quota_daily", "quota_zero")
                    if info["kind"] == "bad_request":
                        raise GeminiRequestError(info["message"]) from None
                    self._handle_error(entry, model, info)
                    if info["kind"] == "timeout":
                        self.timeouts_in_row += 1
                        if self.timeouts_in_row >= 2:
                            self.breaker_until = GEM_NOW() + float(CFG.get("gemini_breaker_sec", 600))
                            raise GeminiUnavailable("Gemini timed out twice in a row; pausing it for a few minutes (local fallback runs)", saw_quota, attempts)
        raise GeminiUnavailable(f"no Gemini key/model could serve the request (last: {last_kind})", saw_quota, attempts)

    def upload_file(self, path, poll_timeout=120):
        """Upload media with the first healthy key; returns {label, name, uri, mime_type}. Use generate(..., pin=label) afterwards."""
        last = "no usable key"
        for e in self.entries:
            if e["auth_failed"]:
                continue
            try:
                client = self._client(e)
                f = client.files.upload(file=path)
                def state(x):
                    s = getattr(x, "state", None)
                    return "ACTIVE" if s is None else str(getattr(s, "name", s) or "").upper().replace("FILESTATE.", "")
                waited = 0
                while state(f) in ("PROCESSING", "STATE_UNSPECIFIED", "") and waited < poll_timeout:
                    GEM_SLEEP(5); waited += 5
                    f = client.files.get(name=f.name)
                if state(f) != "ACTIVE":
                    raise RuntimeError(f"uploaded file state is {state(f)}")
                return {"label": e["label"], "name": f.name, "uri": f.uri, "mime_type": getattr(f, "mime_type", None) or "video/mp4"}
            except Exception as exc:
                info = classify_gemini_error(exc)
                last = info["kind"]
                if info["kind"] == "auth":
                    e["auth_failed"], e["auth_ts"] = True, GEM_NOW()
                gem_log(f"{e['label']}: media upload failed ({info['kind']})")
        raise GeminiUnavailable(f"could not upload media to any Gemini key (last: {last})")

    def delete_file(self, info):
        try:
            entry = next(e for e in self.entries if e["label"] == info["label"])
            self._client(entry).files.delete(name=info["name"])
        except Exception:
            pass

    def validate_keys(self):
        """Cheap key check via models.list (not a generation request); cached 12h; inconclusive results never disable a key."""
        now = GEM_NOW()
        for e in self.entries:
            if e["auth_failed"] or (now - e["validated"] < 12 * 3600):
                continue
            try:
                listing = self._client(e).models.list()
                next(iter(listing), None)
                e["validated"] = now
            except Exception as exc:
                info = classify_gemini_error(exc)
                if info["kind"] == "auth":
                    e["auth_failed"], e["auth_ts"] = True, now
                gem_log(f"{e['label']}: validation {'FAILED (auth)' if info['kind'] == 'auth' else 'inconclusive (' + info['kind'] + ')'}")
        self.save_state()

    def health_report(self):
        rows = []
        for e in self.entries:
            rows.append({"key": e["label"], "fingerprint": e["fp"][:4], "project": e["project"],
                         "health": "auth_failed" if e["auth_failed"] else "ok", "requests": e["requests"], "errors": e["errors"]})
        return rows


# ----------------------------------------------------------------------------- structured output client
def validate_json_schema(value, schema, path="$"):
    """Minimal JSON-Schema validator (type/required/properties/items/enum/min/max/minItems/maxItems/additionalProperties)."""
    errors = []
    types = schema.get("type")
    if types is not None:
        tl = types if isinstance(types, list) else [types]
        def ok_type(t):
            return ((t == "string" and isinstance(value, str)) or (t == "boolean" and isinstance(value, bool)) or
                    (t == "integer" and isinstance(value, int) and not isinstance(value, bool)) or
                    (t == "number" and isinstance(value, (int, float)) and not isinstance(value, bool)) or
                    (t == "object" and isinstance(value, dict)) or (t == "array" and isinstance(value, list)) or
                    (t == "null" and value is None))
        if not any(ok_type(t) for t in tl):
            return [f"{path}: expected {'|'.join(tl)}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} > maximum {schema['maximum']}")
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}: missing required '{req}'")
        props = schema.get("properties", {})
        for k, v in value.items():
            if k in props:
                errors += validate_json_schema(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected property '{k}'")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(value[:200]):
                errors += validate_json_schema(item, schema["items"], f"{path}[{i}]")
    return errors[:10]


def _extract_json(text):
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S).strip()
    return json.loads(t)


def _gemini_cache_key(task_id, prompt, schema, system, media):
    blob = json.dumps({"t": task_id, "p": prompt, "s": schema, "y": system, "m": media}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def gemini_models_for(tier):
    names = CFG.get("gemini_model_reasoning") if tier == "reasoning" else CFG.get("gemini_model_fast")
    return list(names or [])


def gemini_structured(task_id, prompt, schema, tier="reasoning", system=None, media=None, validator=None,
                      max_repairs=1, temperature=0.2, use_cache=True, pin=None, cache_media=None):
    """Run one structured Gemini task. NEVER raises for availability problems:
    returns {"ok": False, "fallback_required": True, "error_kind": ...} so the caller uses its local fallback."""
    out = {"ok": False, "task_id": task_id, "data": None, "fallback_required": True, "source": None,
           "model": None, "project": None, "error_kind": None, "error": None, "attempts": 0}
    pool = globals().get("GEMINI")
    if pool is None or not getattr(pool, "enabled", False):
        out.update(error_kind="disabled", error=getattr(pool, "disabled_reason", "Gemini disabled") if pool else "Gemini not initialised")
        return out
    if len(prompt) > int(CFG.get("gemini_prompt_char_cap", 400000)):
        out.update(error_kind="request", error=f"prompt too large ({len(prompt)} chars)")
        return out
    cache_path = os.path.join(GEMINI_CACHE_DIR, _gemini_cache_key(task_id, prompt, schema, system, cache_media if cache_media is not None else media) + ".json")
    if use_cache and os.path.isfile(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            out.update(ok=True, data=cached["data"], fallback_required=False, source="cache",
                       model=cached.get("model"), project=cached.get("project"))
            return out
        except Exception:
            pass
    response_format = {"type": "text", "mime_type": "application/json", "schema": schema}
    request_input = [media, {"type": "text", "text": prompt}] if media else prompt
    gen_cfg = {"temperature": temperature}
    current_prompt, last_errors = prompt, []
    for repair in range(max_repairs + 1):
        if repair:
            current_prompt = (prompt + "\n\nYour previous answer was rejected:\n- " + "\n- ".join(last_errors[:8]) +
                              "\nReturn corrected JSON only, obeying the schema and the sentence-ID rules.")
            request_input = [media, {"type": "text", "text": current_prompt}] if media else current_prompt
        try:
            res = (pool.generate(gemini_models_for(tier), request_input, response_format, system, gen_cfg, task_id, pin=pin) if pin is not None
                   else pool.generate(gemini_models_for(tier), request_input, response_format, system, gen_cfg, task_id))
        except GeminiRequestError as exc:
            out.update(error_kind="request", error=gem_scrub(exc)); return out
        except GeminiUnavailable as exc:
            out.update(error_kind="waiting_for_quota" if exc.waiting_for_quota else "unavailable",
                       error=gem_scrub(exc), attempts=exc.attempts); return out
        out.update(model=res.model, project=res.project, attempts=out["attempts"] + res.attempts)
        try:
            data = _extract_json(res.text)
        except Exception as exc:
            last_errors = [f"output was not valid JSON ({type(exc).__name__})"]; continue
        last_errors = validate_json_schema(data, schema)
        if not last_errors and validator is not None:
            try:
                last_errors = list(validator(data) or [])
            except Exception as exc:
                last_errors = [f"validator error: {type(exc).__name__}"]
        if not last_errors:
            out.update(ok=True, data=data, fallback_required=False, source="gemini")
            try:
                atomic_write_json(cache_path, {"data": data, "model": res.model, "project": res.project,
                                               "task_id": task_id, "cached_utc": datetime.now(timezone.utc).isoformat()})
            except Exception:
                pass
            return out
    out.update(error_kind="invalid_output", error="; ".join(last_errors[:5]))
    return out


# ----------------------------------------------------------------------------- stage tracker
STAGE_STATUSES = ("pending", "running", "completed", "failed", "waiting_for_quota", "completed_using_fallback", "requires_review")
STAGES = {}


def stage_set(name, status, decided_by=None, detail=""):
    assert status in STAGE_STATUSES, status
    STAGES[name] = {"status": status, "decided_by": decided_by, "detail": detail, "utc": datetime.now(timezone.utc).isoformat()}
    STATE["stages"] = STAGES
    save_state(f"stage:{name}", status=status, decided_by=decided_by)


def print_stage_report():
    print("\nSTAGE REPORT")
    for name, row in STAGES.items():
        print(f"  {name:<34} {row['status']:<26} {row['decided_by'] or '-':<22} {row['detail'][:70]}")


# ----------------------------------------------------------------------------- live capability probe (explicit, small)
def gemini_live_probe(pool, include_video=True, include_agentic=True):
    """A handful of tiny requests that REPORT what the account can actually do; nothing is assumed from model names."""
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    rf = {"type": "text", "mime_type": "application/json", "schema": schema}
    report = {"checked_utc": datetime.now(timezone.utc).isoformat(), "structured": {}, "video": {}, "agentic": {}}
    top = gemini_models_for("reasoning")[:1]
    models = list(dict.fromkeys(top + gemini_models_for("fast")[:1]))
    def attempt(models_, inp):
        try:
            _old_attempts = CFG.get("gemini_max_attempts_per_call", 6)
            CFG["gemini_max_attempts_per_call"] = 2        # a probe must never spend minutes retrying
            try:
                res = pool.generate(models_, inp, rf, None, {"temperature": 0.0}, "probe")
            finally:
                CFG["gemini_max_attempts_per_call"] = _old_attempts
            return {"supported": bool(_extract_json(res.text).get("ok") in (True, False)), "model": res.model, "project": res.project}
        except GeminiUnavailable as exc:
            return {"supported": None, "error": gem_scrub(exc)}
        except GeminiRequestError as exc:
            return {"supported": False, "error": gem_scrub(exc)}
        except Exception as exc:
            return {"supported": None, "error": gem_scrub(f"{type(exc).__name__}: {exc}")}
    for m in models:
        report["structured"][m] = attempt([m], 'Reply with JSON {"ok": true}.')
    if include_video and top and any(r.get("supported") for r in report["structured"].values()):
        path = os.path.join(RUN_DIR, "gemini_probe.mp4")
        if not os.path.isfile(path):
            run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=5:duration=2",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", path], quiet=True)
        b64 = base64.b64encode(open(path, "rb").read()).decode()
        part = {"type": "video", "data": b64, "mime_type": "video/mp4"}
        report["video"][top[0]] = attempt(top, [part, {"type": "text", "text": 'Is there video content? Reply JSON {"ok": true}.'}])
        if include_agentic:
            apart = dict(part, processing="agentic")
            report["agentic"][top[0]] = attempt(top, [apart, {"type": "text", "text": 'Is there video content? Reply JSON {"ok": true}.'}])
    pool.capabilities = report
    return report


# ----------------------------------------------------------------------------- capability parity (honest status)
PARITY_MATRIX = [
    ("Topic discovery",            "Gemini topic map",                     "CELL 6 lexicon/embedding content map",              "implemented"),
    ("Story reconstruction",       "Gemini sentence-ID story proposals",   "CELL 7 multi-interval story builder",               "implemented"),
    ("Clip selection / hooks",     "Gemini ranking + hook reasoning",      "CELL 7 editorial scoring",                          "implemented"),
    ("Story validation",           "Gemini critique of each candidate",    "Editorial QA (transcript-verified) + review flags", "implemented"),
    ("Whole-video visual understanding", "Gemini video / agentic video",   "scene detection + OCR + keyframes",                 "partial (director sees video; local scene/OCR not built)"),
    ("Portrait composition",       "Gemini visual direction",              "face detector + fit-blur layout rules",             "partial (batch 1)"),
    ("B-roll research / verification", "Gemini visual search brief",       "provider adapters + metadata ranking",              "implemented (Gemini re-ranks results)"),
    ("Music selection",            "Gemini mood/energy brief",             "mood classifier + licensed pool + procedural bed",  "implemented (Gemini brief + re-rank)"),
    ("Sound-effects cue plan",     "Gemini cue sheet",                     "rule-based cue planner (joins) + procedural SFX",   "implemented"),
    ("Preview review / correction","Gemini reviews contact sheets",        "automated technical QA",                            "partial (existing)"),
]


def print_parity_matrix():
    print("\nCAPABILITY PARITY (Gemini task -> independent local fallback)")
    for cap, g, local, status in PARITY_MATRIX:
        print(f"  {cap:<34} | {local:<52} | {status}")


# ----------------------------------------------------------------------------- consent + setup
def ensure_external_ai_consent():
    choice = CFG.get("external_ai_enabled")
    if choice is True:
        return True
    if choice is False:
        return False
    pre = (os.environ.get("ZIROQ_GEMINI_CONSENT", "") or _gemini_secret("ZIROQ_GEMINI_CONSENT") or "").strip()
    if pre:                       # typed into the Start-here form (or a Colab Secret): no console input box needed (phone-friendly)
        return pre == "I AGREE"
    print(CONSENT_TEXT)
    try:
        answer = input('Type "I AGREE" to enable Gemini (anything else = local-only): ')
    except (EOFError, OSError):
        answer = ""
    return answer.strip() == "I AGREE"


def setup_gemini(factory=None, state_path=None):
    keys = detect_gemini_keys()
    if not keys:
        pool = GeminiPool([], factory, state_path)
        gem_log("NO KEYS FOUND, so there is no consent prompt. In Colab: left sidebar > key icon (Secrets) > add GEMINI_API_KEY_1 ... GEMINI_API_KEY_5, "
                "switch 'Notebook access' ON for each, then re-run this cell. Running with local fallbacks only.")
        return pool
    try:
        version = importlib.metadata.version("google-genai")
        major_minor = tuple(int(x) for x in re.findall(r"\d+", version)[:2])
        sdk_ok = major_minor >= (2, 3)
    except Exception:
        version, sdk_ok = None, False
    pool = GeminiPool(keys, factory, state_path if state_path else GEMINI_STATE_PATH,
                      gemini_project_map([k for k, _ in keys]))
    if factory is None and not sdk_ok:
        pool.enabled, pool.disabled_reason = False, f"google-genai>=2.3.0 required (found {version}); run Cell 0 again"
        gem_log(pool.disabled_reason)
        return pool
    if not ensure_external_ai_consent():
        pool.enabled, pool.disabled_reason = False, "external AI processing not authorised by the user"
        gem_log("external AI processing NOT enabled; ZIROQ runs entirely on its local components")
        return pool
    pool.validate_keys()
    if not any(not e["auth_failed"] for e in pool.entries):
        pool.enabled, pool.disabled_reason = False, "no valid Gemini key"
    return pool


if "GEMINI" not in globals() or not getattr(globals().get("GEMINI"), "enabled", False) or globals().get("GEMINI_FORCE_SETUP", False):
    GEMINI = setup_gemini()
print("\nGEMINI STATUS:", "ENABLED" if GEMINI.enabled else f"local-only ({GEMINI.disabled_reason})")
for row in GEMINI.health_report():
    print(f"  {row['key']} (fp {row['fingerprint']}…) project={row['project']} health={row['health']}")
if GEMINI.enabled:
    print("  zero_cost_mode:", CFG.get("zero_cost_mode", True), "| per-run request cap:", CFG.get("gemini_max_requests_per_run"),
          "| reasoning models:", gemini_models_for("reasoning"))
    if CFG.get("gemini_live_probe", True):
        PROBE = gemini_live_probe(GEMINI, include_video=True, include_agentic=CFG.get("gemini_probe_agentic", True))
        for what in ("structured", "video", "agentic"):
            for m, r in PROBE[what].items():
                print(f"  probe {what:<10} {m:<22} supported={r.get('supported')} {('— ' + r['error'][:90]) if r.get('error') else ''}")
print_parity_matrix()
save_state("gemini_ready", enabled=GEMINI.enabled)
