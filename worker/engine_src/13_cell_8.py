# CELL 8 — Multi-provider licensed music + B-roll acquisition (surgical V3 patch)
# Providers: Coverr API (free tier), Internet Archive CC search, Pixabay API, and verified manual pools.
# Final fallbacks: original procedural music and the source footage. No licence gate is bypassed.

import requests
from urllib.parse import quote
from scipy.io import wavfile

# Do NOT include Public Domain Mark here: it is a rights-status label, not a copyright licence.
ARCHIVE_LICENSE_ALLOWLIST = {
    "https://creativecommons.org/publicdomain/zero/1.0/": "CC0 1.0",
    "https://creativecommons.org/licenses/by/4.0/": "CC BY 4.0",
    "https://creativecommons.org/licenses/by/3.0/": "CC BY 3.0",
}
LICENSE_ALLOWLIST = dict(ARCHIVE_LICENSE_ALLOWLIST)
COVERR_LICENSE_URL = "https://coverr.co/license/"
PIXABAY_LICENSE_URL = "https://pixabay.com/service/license-summary/"
MIXKIT_MUSIC_LICENSE_URL = "https://mixkit.co/license/modal/musicFree/"
MIXKIT_VIDEO_LICENSE_URL = "https://mixkit.co/license/modal/videoFree/"

# The persistent cache prevents repeated API calls and preserves downloaded media between runs.
# Pixabay explicitly requires API responses to be cached for at least 24 hours.
ASSET_CACHE_DIR = os.path.join(DRIVE_ROOT, "_ASSET_CACHE") if DRIVE_AVAILABLE else os.path.join(CACHE_ROOT, "_ASSET_CACHE")
os.makedirs(ASSET_CACHE_DIR, exist_ok=True)
MUSIC_POOL_DIR = os.path.join(DRIVE_ROOT if DRIVE_AVAILABLE else CFG["work_dir"], "music_pool")
BROLL_POOL_DIR = os.path.join(DRIVE_ROOT if DRIVE_AVAILABLE else CFG["work_dir"], "broll_pool")
os.makedirs(MUSIC_POOL_DIR, exist_ok=True)
os.makedirs(BROLL_POOL_DIR, exist_ok=True)

ASSET_MANIFEST = {"generated_utc": datetime.now(timezone.utc).isoformat(), "music": [], "broll": []}
USED_MUSIC_IDS = set()
USED_BROLL_IDS = set()
BROLL_DOWNLOADS_THIS_RUN = 0


def _secret_value(name):
    """Read a Colab Secret first, then an environment variable; never print API keys."""
    value = os.environ.get(name, "")
    if value:
        return str(value).strip()
    try:
        from google.colab import userdata
        return str(userdata.get(name) or "").strip()
    except Exception:
        return ""


def _normalise_license_url(value):
    value = str(value or "").strip().split("#", 1)[0].split("?", 1)[0].rstrip("/")
    if value.startswith("http://creativecommons.org/"):
        value = "https://" + value[len("http://"):]
    return (value + "/") if value else ""


def _safe_pool_file(pool_dir, filename):
    """Resolve a manifest filename and reject absolute paths or traversal outside the pool."""
    try:
        root = Path(pool_dir).resolve()
        raw = Path(str(filename or ""))
        if raw.is_absolute():
            return None
        candidate = (root / raw).resolve()
        if candidate == root or root not in candidate.parents:
            return None
        return str(candidate) if candidate.is_file() else None
    except Exception:
        return None


def _stream_download(url, local_path, max_bytes=180 * 1024 * 1024, headers=None, timeout=(15, 90)):
    """Download atomically; never leave a partial file that can be mistaken for an asset."""
    if not url or not str(url).startswith("https://"):
        raise ValueError("Asset URL must be HTTPS")
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    if os.path.isfile(local_path) and os.path.getsize(local_path) > 20_000:
        return local_path
    part_path = local_path + ".part"
    try:
        with requests.get(url, stream=True, timeout=timeout, headers=headers or {}) as response:
            response.raise_for_status()
            declared = int(response.headers.get("Content-Length") or 0)
            if declared and declared > max_bytes:
                raise ValueError(f"Asset exceeds download cap ({declared} bytes)")
            total = 0
            with open(part_path, "wb") as out:
                for block in response.iter_content(1024 * 256):
                    if not block:
                        continue
                    total += len(block)
                    if total > max_bytes:
                        raise ValueError("Asset exceeded download cap while streaming")
                    out.write(block)
        if total < 20_000:
            raise ValueError(f"Downloaded asset is suspiciously small ({total} bytes)")
        os.replace(part_path, local_path)
        return local_path
    except Exception:
        try:
            if os.path.exists(part_path):
                os.remove(part_path)
        except OSError:
            pass
        raise


def _probe_audio_asset(path, min_duration=8.0):
    try:
        info = ffprobe_json(path)
        streams = info.get("streams", [])
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        duration = float(info.get("format", {}).get("duration", 0) or 0)
        return bool(has_audio and duration >= min_duration), duration
    except Exception:
        return False, 0.0


def _probe_video_asset(path, min_duration=2.0):
    try:
        info = ffprobe_json(path)
        streams = info.get("streams", [])
        has_video = any(s.get("codec_type") == "video" for s in streams)
        duration = float(info.get("format", {}).get("duration", 0) or 0)
        return bool(has_video and duration >= min_duration), duration
    except Exception:
        return False, 0.0


def _read_fresh_json(path, ttl_seconds):
    try:
        with open(path, encoding="utf-8") as f:
            row = json.load(f)
        if time.time() - float(row.get("cached_at", 0)) <= ttl_seconds:
            return row
    except Exception:
        pass
    return None


def _write_json_cache(path, payload):
    atomic_write_json(path, {"cached_at": time.time(), **payload})


MUSIC_QUERY_OVERRIDE = ""      # set per clip from Gemini's music brief; empty = built-in mood terms
_BRIEF_MOODS = ("reflective", "uplifting", "playful", "curious", "documentary")


def _music_query_tag():
    return hashlib.sha1(MUSIC_QUERY_OVERRIDE.encode()).hexdigest()[:6] if MUSIC_QUERY_OVERRIDE else "std"


def plan_mood(plan):
    _brief_mood = (plan.notes.get("music_brief") or {}).get("mood")
    if _brief_mood in _BRIEF_MOODS:
        return _brief_mood
    emotions = set(plan.notes.get("emotions", []))
    text = " ".join(s.transcript for s in plan.segments).lower()
    if "tension" in emotions or any(x in text for x in ("fear", "risk", "danger", "crisis")):
        return "reflective"
    if "hope" in emotions or any(x in text for x in ("believe", "future", "opportunity", "dream")):
        return "uplifting"
    if "amusement" in emotions or any(x in text for x in ("funny", "joke", "laugh")):
        return "playful"
    if "surprise" in emotions:
        return "curious"
    return "documentary"


def _coverr_headers():
    key = str(CFG.get("coverr_api_key") or _secret_value("COVERR_API_KEY")).strip()
    return {"x-api-key": key} if key else {}


def _download_coverr_music(mood, clip_index):
    """Search Coverr's official API. Free-tier music must be credited in the publishing pack."""
    api_key = str(CFG.get("coverr_api_key") or _secret_value("COVERR_API_KEY")).strip()
    if not api_key:
        return None
    terms = {
        "reflective": "ambient cinematic piano",
        "uplifting": "inspirational uplifting cinematic",
        "playful": "light upbeat acoustic",
        "curious": "minimal suspense ambient",
        "documentary": "documentary ambient instrumental",
    }
    cache_path = os.path.join(ASSET_CACHE_DIR, f"coverr_audio_{mood}_{_music_query_tag()}.json")
    cached = _read_fresh_json(cache_path, 24 * 3600)
    if cached is None:
        try:
            resp = requests.get(
                "https://api.coverr.co/audios",
                params={"query": MUSIC_QUERY_OVERRIDE or terms.get(mood, "ambient instrumental"), "page_size": 30, "sort": "popular"},
                headers=_coverr_headers(), timeout=(15, 30),
            )
            resp.raise_for_status()
            hits = resp.json().get("hits", [])
            _write_json_cache(cache_path, {"hits": hits})
            cached = {"hits": hits}
        except Exception as exc:
            log("music", f"Coverr music API unavailable ({type(exc).__name__}); trying other cleared sources")
            return None
    hits = cached.get("hits", [])
    # Prefer stock, non-premium tracks with a direct master download URL.
    hits = sorted(hits, key=lambda h: (bool(h.get("isPremium")), -int(h.get("downloads", 0) or 0)))
    hits = rerank_candidates("music", hits)
    detail_lookups = 0
    for hit in hits:
        track_id = str(hit.get("id") or "")
        if not track_id or track_id in USED_MUSIC_IDS or hit.get("isPremium") is True or hit.get("is_premium") is True:
            continue
        sponsor_fields = any(hit.get(k) for k in ("sponsored_by", "sponsor", "sponsor_name", "partner_content", "is_sponsored", "is_partner"))
        partner_text = " ".join(str(hit.get(k) or "") for k in ("title", "name", "description", "provider", "source")).lower()
        if sponsor_fields or any(partner in partner_text for partner in ("shutterstock", "istock", "premiumbeat")):
            continue
        urls = hit.get("urls") or {}
        download_url = urls.get("masterDownload") or urls.get("master_download")
        if not download_url:
            # Some catalogue responses omit signed URLs; cap detail lookups to protect the free API quota.
            if detail_lookups >= 3:
                break
            detail_lookups += 1
            try:
                detail = requests.get(f"https://api.coverr.co/audios/{quote(track_id)}", headers=_coverr_headers(), timeout=(15, 25))
                detail.raise_for_status()
                hit = detail.json()
                urls = hit.get("urls") or {}
                download_url = urls.get("masterDownload") or urls.get("master_download")
            except Exception:
                continue
        if not download_url:
            continue
        filename = str(hit.get("filename") or f"coverr_{track_id}.wav")
        ext = Path(filename).suffix.lower() or ".wav"
        local_path = os.path.join(ASSET_CACHE_DIR, f"coverr_music_{safe_filename(track_id, 32)}{ext}")
        try:
            _stream_download(download_url, local_path, max_bytes=100 * 1024 * 1024)
            valid, duration = _probe_audio_asset(local_path)
            if not valid:
                try: os.remove(local_path)
                except OSError: pass
                continue
            creator = hit.get("creator") or hit.get("author") or hit.get("user") or hit.get("createdBy")
            if isinstance(creator, dict):
                creator_name = creator.get("name") or creator.get("username") or creator.get("full_name")
                creator_url = creator.get("url") or creator.get("profile_url")
            else:
                creator_name, creator_url = (str(creator) if creator else None), None
            if creator_name and not creator_url:
                continue  # Free API attribution must link the named creator's Coverr profile.
            asset = {
                "asset_type": "music", "provider": "Coverr API", "asset_id": track_id,
                "title": hit.get("title") or hit.get("name") or Path(filename).stem,
                "artist": creator_name or "Creator not listed in API response",
                "creator_url": creator_url, "source_url": hit.get("url") or hit.get("page_url") or "https://coverr.co/",
                "download_url": download_url, "license": "Coverr Stock Library License",
                "license_url": COVERR_LICENSE_URL, "attribution_required": True,
                "attribution_text": (f"Music: {creator_name} on Coverr" if creator_name else f"Music: {hit.get('title') or hit.get('name') or 'Track'} via Coverr"),
                "local_path": local_path, "duration": duration, "mood": mood,
                "clip_index": clip_index, "is_ai_generated": hit.get("is_ai_generated"),
                "sha256": sha256_file(local_path), "accessed_utc": datetime.now(timezone.utc).isoformat(),
            }
            USED_MUSIC_IDS.add(track_id)
            return asset
        except Exception as exc:
            log("music", f"Coverr track rejected after download/probe ({type(exc).__name__})")
            continue
    return None


def search_internet_archive_music(mood, cache_path, clip_index=0):
    """Broad metadata discovery followed by exact item-level licence and decoded-file verification."""
    cached = _read_fresh_json(cache_path, 6 * 3600)
    if cached:
        if cached.get("status") == "no_verified_asset":
            return None
        cached_id = str(cached.get("identifier") or cached.get("asset_id") or "")
        path = cached.get("local_path")
        if cached_id not in USED_MUSIC_IDS and path and os.path.isfile(path):
            valid, _ = _probe_audio_asset(path)
            if valid:
                USED_MUSIC_IDS.add(cached_id)
                return cached

    # Search the licensed corpus first; rank by mood after retrieval instead of AND-ing every mood word.
    licence_urls = []
    for u in ARCHIVE_LICENSE_ALLOWLIST:
        licence_urls.extend([u, u.replace("https://", "http://", 1)])
    licence_terms = " OR ".join(f'licenseurl:"{u}"' for u in licence_urls)
    query = f"mediatype:audio AND ({licence_terms})"
    try:
        response = requests.get("https://archive.org/advancedsearch.php", params={
            "q": query,
            "fl[]": ["identifier", "title", "creator", "licenseurl", "subject", "description", "downloads", "mediatype"],
            "rows": 100, "page": 1, "output": "json", "sort[]": "downloads desc",
        }, timeout=(15, 35))
        response.raise_for_status()
        docs = response.json().get("response", {}).get("docs", [])
    except Exception as exc:
        log("music", f"Internet Archive unavailable ({type(exc).__name__}); continuing to other music sources")
        return None

    mood_terms = set(re.findall(r"[a-z0-9]+", (mood + " " + MUSIC_QUERY_OVERRIDE).replace("_", " ").lower()))
    # Pull mood-related text from titles/subjects, but do not make it a hard search filter.
    docs.sort(key=lambda d: (
        len(mood_terms & set(re.findall(r"[a-z0-9]+", " ".join(map(str, [d.get("title", ""), d.get("subject", ""), d.get("description", "")])).lower()))),
        int(d.get("downloads", 0) or 0),
    ), reverse=True)

    docs = rerank_candidates("music", docs)
    for doc in docs[:80]:
        identifier = str(doc.get("identifier") or "")
        if not identifier or identifier in USED_MUSIC_IDS:
            continue
        try:
            meta_resp = requests.get(f"https://archive.org/metadata/{quote(identifier)}", timeout=(15, 30))
            meta_resp.raise_for_status()
            item = meta_resp.json()
            md = item.get("metadata", {})
            license_url = _normalise_license_url(md.get("licenseurl") or doc.get("licenseurl"))
            if license_url not in ARCHIVE_LICENSE_ALLOWLIST:
                continue
            creator = md.get("creator") or md.get("artist") or doc.get("creator")
            # CC BY needs a real attribution name; skip metadata that cannot be credited honestly.
            if license_url != "https://creativecommons.org/publicdomain/zero/1.0/" and not creator:
                continue
            metadata_text = " ".join(str(x or "") for x in (md.get("title"), md.get("subject"), md.get("description"), doc.get("title"), doc.get("subject"))).lower()
            music_terms = ("music", "instrumental", "ambient", "piano", "soundtrack", "composition", "cinematic", "orchestra", "synth", "guitar", "jazz", "classical", "folk", "score")
            spoken_terms = ("podcast", "audiobook", "lecture", "sermon", "interview", "speech", "talk show", "radio news", "spoken word")
            if not any(term in metadata_text for term in music_terms) or any(term in metadata_text for term in spoken_terms):
                continue
            files = item.get("files", [])
            audio_files = [f for f in files if str(f.get("name", "")).lower().endswith((".mp3", ".wav", ".ogg", ".flac", ".m4a"))
                           and not f.get("private") and 100_000 <= int(f.get("size", 0) or 0) <= 80 * 1024 * 1024
                           and not any(x in str(f.get("name", "")).lower() for x in ("sample", "preview", "spectrogram", "thumbnail"))]
            if not audio_files:
                continue
            audio_files.sort(key=lambda f: (not str(f.get("name", "")).lower().endswith(".mp3"), -int(f.get("size", 0) or 0)))
            for file_row in audio_files[:3]:
                filename = str(file_row["name"])
                download_url = f"https://archive.org/download/{quote(identifier)}/{quote(filename)}"
                ext = Path(filename).suffix.lower()
                local_path = os.path.join(ASSET_CACHE_DIR, f"ia_{safe_filename(identifier, 35)}_{hashlib.sha256(filename.encode()).hexdigest()[:10]}{ext}")
                try:
                    _stream_download(download_url, local_path, max_bytes=90 * 1024 * 1024)
                    valid, duration = _probe_audio_asset(local_path)
                    if not valid:
                        try: os.remove(local_path)
                        except OSError: pass
                        continue
                    attribution_required = license_url != "https://creativecommons.org/publicdomain/zero/1.0/"
                    asset = {
                        "asset_type": "music", "provider": "Internet Archive", "identifier": identifier,
                        "title": md.get("title") or doc.get("title") or identifier,
                        "artist": creator or "Public domain (creator not listed)",
                        "source_url": f"https://archive.org/details/{identifier}", "download_url": download_url,
                        "license": ARCHIVE_LICENSE_ALLOWLIST[license_url], "license_url": license_url,
                        "attribution_required": attribution_required,
                        "attribution_text": (f"Music: {md.get('title') or doc.get('title') or identifier} by {creator} ({ARCHIVE_LICENSE_ALLOWLIST[license_url]})" if attribution_required else "Public-domain music via Internet Archive"),
                        "license_verified_from_item_metadata": True, "local_path": local_path,
                        "duration": duration, "mood": mood, "clip_index": clip_index,
                        "sha256": sha256_file(local_path), "accessed_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    USED_MUSIC_IDS.add(identifier)
                    _write_json_cache(cache_path, asset)
                    return asset
                except Exception:
                    continue
        except Exception:
            continue

    _write_json_cache(cache_path, {"status": "no_verified_asset", "mood": mood})
    log("music", f"Internet Archive: no matching item with verifiable CC0/CC BY metadata for '{mood}'")
    return None


def generate_original_music(path, duration, mood, seed):
    """Generate an original, low-level instrumental bed; no third-party recording or sample is used."""
    sr = 24000
    count = int(max(1.0, duration) * sr)
    t = np.arange(count, dtype=np.float32) / sr
    rng = np.random.default_rng(seed)
    progressions = {
        "reflective": [(110.00, 164.81, 220.00), (98.00, 146.83, 196.00), (130.81, 196.00, 261.63), (110.00, 164.81, 220.00)],
        "uplifting": [(130.81, 164.81, 196.00), (174.61, 220.00, 261.63), (146.83, 185.00, 220.00), (196.00, 246.94, 293.66)],
        "playful": [(146.83, 185.00, 220.00), (164.81, 207.65, 246.94), (196.00, 246.94, 293.66), (164.81, 207.65, 246.94)],
        "curious": [(110.00, 138.59, 164.81), (116.54, 146.83, 174.61), (98.00, 123.47, 146.83), (110.00, 138.59, 164.81)],
        "documentary": [(123.47, 155.56, 185.00), (110.00, 138.59, 164.81), (130.81, 164.81, 196.00), (123.47, 155.56, 185.00)],
    }
    progression = progressions.get(mood, progressions["documentary"])
    chord_sec = 4.0
    chord_idx = np.minimum((t / chord_sec).astype(int) % len(progression), len(progression)-1)
    pad = np.zeros(count, dtype=np.float32)
    for chord_i, notes in enumerate(progression):
        mask = chord_idx == chord_i
        local_t = (t[mask] % chord_sec)
        env = np.minimum(1.0, local_t / 1.2) * np.minimum(1.0, (chord_sec-local_t) / 1.0)
        tone = np.zeros(mask.sum(), dtype=np.float32)
        for note in notes:
            tone += np.sin(2*np.pi*note*t[mask]) + 0.22*np.sin(4*np.pi*note*t[mask])
        pad[mask] = tone / len(notes) * env
    pulse = np.zeros(count, dtype=np.float32)
    beat = 0.75 if mood in ("uplifting", "playful") else 1.0
    for beat_start in np.arange(0, duration, beat):
        start = int(beat_start * sr)
        end = min(count, start + int(0.20 * sr))
        if end > start:
            local = np.arange(end-start, dtype=np.float32) / sr
            freq = rng.choice([55.0, 65.4, 73.4, 82.4])
            pulse[start:end] += 0.10 * np.sin(2*np.pi*freq*local) * np.exp(-local*18.0)
    lfo = 0.82 + 0.10*np.sin(2*np.pi*0.08*t)
    mono = np.clip((0.11 * pad + pulse) * lfo, -0.30, 0.30)
    fade = min(2.0, duration/4)
    if fade > 0:
        fade_n = max(1, int(fade*sr))
        mono[:fade_n] *= np.linspace(0, 1, fade_n, dtype=np.float32)
        mono[-fade_n:] *= np.linspace(1, 0, fade_n, dtype=np.float32)
    stereo = np.stack([mono, np.roll(mono, int(0.012*sr))*0.96], axis=1)
    wavfile.write(path, sr, (stereo * 32767).astype(np.int16))
    return path


def choose_verified_manual_music(mood, index):
    """Manual pool items require a path-contained file and a licence-name/URL match."""
    manifest_path = os.path.join(MUSIC_POOL_DIR, "music_pool_manifest.json")
    if not os.path.isfile(manifest_path):
        return None
    try:
        with open(manifest_path, encoding="utf-8") as f:
            rows = json.load(f)
    except Exception:
        return None
    strict_names = {v: k for k, v in ARCHIVE_LICENSE_ALLOWLIST.items()}
    for row in rows:
        track_path = _safe_pool_file(MUSIC_POOL_DIR, row.get("filename"))
        if not track_path:
            continue
        manual_id = str(row.get("asset_id") or row.get("filename") or Path(track_path).name)
        if manual_id in USED_MUSIC_IDS:
            continue
        license_name = str(row.get("license") or "").strip()
        license_url = _normalise_license_url(row.get("license_url"))
        is_mixkit = license_name == "Mixkit Stock Music Free License" and license_url == _normalise_license_url(MIXKIT_MUSIC_LICENSE_URL)
        if is_mixkit and row.get("manual_verified") is not True:
            continue
        if not is_mixkit and strict_names.get(license_name) != license_url:
            continue
        if not row.get("source_url"):
            continue
        if license_name.startswith("CC BY") and not (row.get("artist") or row.get("creator") or row.get("attribution")):
            continue
        valid, duration = _probe_audio_asset(track_path)
        if not valid:
            continue
        creator = row.get("artist") or row.get("creator") or "Unknown"
        USED_MUSIC_IDS.add(manual_id)
        return {
            "asset_type": "music", "provider": row.get("provider", "User-imported verified pool"),
            "title": row.get("title", Path(track_path).stem), "artist": creator,
            "source_url": row.get("source_url"), "download_url": row.get("download_url"),
            "license": license_name, "license_url": license_url,
            "attribution_required": bool(row.get("attribution_required", license_name.startswith("CC BY"))),
            "attribution_text": row.get("attribution") or f"Music: {row.get('title', Path(track_path).stem)} by {creator}",
            "local_path": track_path, "duration": duration, "mood": mood, "clip_index": index,
            "sha256": sha256_file(track_path), "selection": "verified_manual_import",
            "accessed_utc": datetime.now(timezone.utc).isoformat(),
        }
    return None


def choose_music_for_plan(plan, index):
    global MUSIC_QUERY_OVERRIDE
    mood = plan_mood(plan)
    _brief = plan.notes.get("music_brief") or {}
    MUSIC_QUERY_OVERRIDE = re.sub(r"[^a-z0-9 ]", " ", str(_brief.get("search_terms") or "").lower()).strip()[:60]
    RERANK_CONTEXT["music"] = (f"Background music for a short vertical video titled '{plan.title}'. Mood: {mood}. Energy: {_brief.get('energy', 'medium')}. "
                               f"Wanted: {_brief.get('search_terms') or mood}. Avoid: {_brief.get('avoid') or 'vocals, loud drums'}. It must not compete with speech.") if _brief else ""
    try:
        return _choose_music_for_plan_inner(plan, index, mood)
    finally:
        MUSIC_QUERY_OVERRIDE = ""
        RERANK_CONTEXT["music"] = ""


def _choose_music_for_plan_inner(plan, index, mood):
    asset = _download_coverr_music(mood, index) if CFG.get("enable_coverr_assets", True) else None
    if not asset and CFG.get("enable_internet_archive_music", True):
        ia_cache = os.path.join(ASSET_CACHE_DIR, f"ia_music_{mood}_{_music_query_tag()}.json")
        asset = search_internet_archive_music(mood, ia_cache, index)
    if not asset:
        asset = choose_verified_manual_music(mood, index)
    if asset and asset.get("local_path") and os.path.isfile(asset["local_path"]):
        asset["clip_index"] = index
        ASSET_MANIFEST["music"].append(asset)
        log("music", f"clip {index}: licensed track selected — {asset.get('provider')} / {asset.get('title')}")
        return asset["local_path"], asset

    music_path = os.path.join(ASSET_DIR, f"original_music_{index:02d}.wav")
    seed = int(hashlib.sha256((SOURCE.video_id + str(index) + plan.title).encode()).hexdigest()[:8], 16)
    generate_original_music(music_path, plan.duration, mood, seed)
    asset = {
        "asset_type": "music", "provider": "Project Ziroq procedural generator",
        "title": f"Original {mood} instrumental bed", "artist": "Generated locally",
        "source_url": None, "download_url": None,
        "license": "Original procedural audio generated by this notebook", "license_url": None,
        "attribution_required": False, "attribution_text": "Original music generated locally by Project Ziroq",
        "local_path": music_path, "mood": mood, "clip_index": index,
        "sha256": sha256_file(music_path), "selection": "original_fallback",
        "accessed_utc": datetime.now(timezone.utc).isoformat(),
    }
    ASSET_MANIFEST["music"].append(asset)
    log("music", f"clip {index}: original instrumental bed generated (no unverified third-party track used)")
    return music_path, asset


def _coverr_search_video(query, cache_path):
    """Coverr free API search; reject premium, sponsored, and AI-generated records."""
    api_key = str(CFG.get("coverr_api_key") or _secret_value("COVERR_API_KEY")).strip()
    if not api_key:
        return None
    cached = _read_fresh_json(cache_path, 24 * 3600)
    if cached is None:
        try:
            resp = requests.get("https://api.coverr.co/videos", params={
                "query": query, "page_size": 20, "sort": "popular", "urls": "true",
                "filter": "is_ai_generated:false",
            }, headers=_coverr_headers(), timeout=(15, 30))
            resp.raise_for_status()
            hits = resp.json().get("hits", [])
            _write_json_cache(cache_path, {"hits": hits})
            cached = {"hits": hits}
        except Exception as exc:
            log("broll", f"Coverr API unavailable ({type(exc).__name__})")
            return None
    hits = cached.get("hits", [])
    query_terms = set(re.findall(r"[a-z0-9]+", query.lower()))
    hits = sorted(hits, key=lambda h: (
        len(query_terms & set(re.findall(r"[a-z0-9]+", " ".join([str(h.get("title", "")), str(h.get("description", "")), " ".join(h.get("tags") or [])]).lower()))),
        bool(h.get("is_vertical")), int(h.get("max_width", 0) or 0),
    ), reverse=True)
    global BROLL_DOWNLOADS_THIS_RUN
    hits = rerank_candidates("broll", hits)
    for hit in hits:
        asset_id = str(hit.get("id") or "")
        if not asset_id or asset_id in USED_BROLL_IDS:
            continue
        if hit.get("is_ai_generated") is not False:
            continue
        if hit.get("is_premium") is True or hit.get("isPremium") is True:
            continue
        # Sponsored partner items are not covered by the Coverr stock-library licence.
        sponsor_fields = any(hit.get(k) for k in ("sponsored_by", "sponsor", "sponsor_name", "partner_content", "is_sponsored", "is_partner"))
        partner_text = " ".join(str(hit.get(k) or "") for k in ("title", "description", "provider", "source", "sponsor_name")).lower()
        if sponsor_fields or any(partner in partner_text for partner in ("shutterstock", "istock", "premiumbeat")):
            continue
        duration = float(hit.get("duration", 0) or 0)
        if duration < 3.0:
            continue
        urls = hit.get("urls") or {}
        download_url = urls.get("mp4_download")
        if not download_url:
            continue
        local_path = os.path.join(ASSET_CACHE_DIR, f"coverr_video_{safe_filename(asset_id, 32)}.mp4")
        try:
            if not os.path.isfile(local_path):
                if BROLL_DOWNLOADS_THIS_RUN >= int(CFG.get("broll_max_downloads_per_run", 5)):
                    log("broll", "per-run stock-download cap reached; using already cached/manual assets only")
                    return None
                _stream_download(download_url, local_path, max_bytes=180 * 1024 * 1024)
                BROLL_DOWNLOADS_THIS_RUN += 1
            valid, probed_duration = _probe_video_asset(local_path)
            if not valid:
                try: os.remove(local_path)
                except OSError: pass
                continue
            creator = hit.get("creator") or hit.get("author") or hit.get("user") or hit.get("created_by")
            if isinstance(creator, dict):
                creator_name = creator.get("name") or creator.get("username")
                creator_url = creator.get("url") or creator.get("profile_url")
            else:
                creator_name, creator_url = (str(creator) if creator else None), None
            if creator_name and not creator_url:
                continue  # Free API attribution must link the named creator's Coverr profile.
            # Download tracking is a condition of Coverr API access under its 4 Nov 2026 terms.
            tracked = requests.patch(f"https://api.coverr.co/videos/{quote(asset_id)}/stats/downloads", headers=_coverr_headers(), timeout=(10, 20))
            tracked.raise_for_status()
            asset = {
                "asset_type": "broll_video", "provider": "Coverr API", "asset_id": asset_id,
                "title": hit.get("title") or "Coverr stock video", "author": creator_name or "Creator not listed in API response",
                "creator_url": creator_url, "source_url": hit.get("url") or hit.get("page_url") or "https://coverr.co/", "download_url": download_url,
                "license": "Coverr Stock Library License (free API tier)", "license_url": COVERR_LICENSE_URL,
                "attribution_required": True,
                "attribution_text": (f"Video by {creator_name} on Coverr" if creator_name else "Video footage via Coverr"),
                "local_path": local_path, "duration": probed_duration, "query": query,
                "is_ai_generated": False, "sha256": sha256_file(local_path),
                "clip_index": None, "accessed_utc": datetime.now(timezone.utc).isoformat(),
            }
            USED_BROLL_IDS.add(asset_id)
            return asset
        except Exception as exc:
            log("broll", f"Coverr result {asset_id} rejected ({type(exc).__name__})")
            try:
                if os.path.exists(local_path): os.remove(local_path)
            except OSError:
                pass
    return None


def pixabay_search_video(query, cache_path):
    """Pixabay API with durable 24-hour response caching, including empty results."""
    if not CFG.get("enable_pixabay_broll", True):
        return None
    api_key = str(CFG.get("pixabay_api_key") or _secret_value("PIXABAY_API_KEY")).strip()
    if not api_key:
        return None
    cached = _read_fresh_json(cache_path, max(24 * 3600, int(CFG.get("broll_cache_hours", 24)) * 3600))
    if cached is None:
        try:
            response = requests.get("https://pixabay.com/api/videos/", params={
                "key": api_key, "q": query[:100], "safesearch": "true", "per_page": 20,
                "order": "popular", "min_width": 640,
            }, timeout=(15, 30))
            response.raise_for_status()
            hits = response.json().get("hits", [])
            # Cache the full search response, including an empty result, for 24 hours as required by Pixabay.
            _write_json_cache(cache_path, {"hits": hits, "query": query})
            cached = {"hits": hits}
        except Exception as exc:
            log("broll", f"Pixabay search failed ({type(exc).__name__})")
            return None
    hits = cached.get("hits", [])
    global BROLL_DOWNLOADS_THIS_RUN
    hits.sort(key=lambda h: (int(h.get("duration", 0)) < 4, abs(int(h.get("duration", 0)) - 8), -int(h.get("views", 0) or 0)))
    hits = rerank_candidates("broll", hits)
    for hit in hits:
        asset_id = str(hit.get("id") or "")
        if not asset_id or asset_id in USED_BROLL_IDS:
            continue
        variants = hit.get("videos") or {}
        choices = sorted(variants.values(), key=lambda v: (abs(int(v.get("width", 0) or 0) - 1080), -int(v.get("width", 0) or 0)))
        if not choices or not choices[0].get("url"):
            continue
        chosen = choices[0]
        local_path = os.path.join(ASSET_CACHE_DIR, f"pixabay_video_{safe_filename(asset_id, 32)}.mp4")
        try:
            if not os.path.isfile(local_path):
                if BROLL_DOWNLOADS_THIS_RUN >= int(CFG.get("broll_max_downloads_per_run", 5)):
                    log("broll", "per-run stock-download cap reached; using cached/manual assets only")
                    return None
                _stream_download(chosen["url"], local_path, max_bytes=180 * 1024 * 1024)
                BROLL_DOWNLOADS_THIS_RUN += 1
            valid, duration = _probe_video_asset(local_path)
            if not valid:
                try: os.remove(local_path)
                except OSError: pass
                continue
            asset = {
                "asset_type": "broll_video", "provider": "Pixabay API", "asset_id": asset_id,
                "title": hit.get("tags") or "Pixabay stock video", "author": hit.get("user", "Unknown"),
                "source_url": hit.get("pageURL"), "download_url": chosen["url"],
                "license": "Pixabay Content License", "license_url": PIXABAY_LICENSE_URL,
                "attribution_required": False, "attribution_text": f"Video by {hit.get('user', 'creator')} via Pixabay",
                "local_path": local_path, "duration": duration, "query": query,
                "sha256": sha256_file(local_path), "clip_index": None,
                "accessed_utc": datetime.now(timezone.utc).isoformat(),
            }
            USED_BROLL_IDS.add(asset_id)
            return asset
        except Exception as exc:
            log("broll", f"Pixabay result {asset_id} rejected ({type(exc).__name__})")
            try:
                if os.path.exists(local_path): os.remove(local_path)
            except OSError:
                pass
    return None


def choose_verified_manual_broll(query, index):
    """Manual B-roll pool for no-key runs; reject unverified paths/licences and invalid media."""
    manifest_path = os.path.join(BROLL_POOL_DIR, "broll_pool_manifest.json")
    if not os.path.isfile(manifest_path):
        return None
    try:
        with open(manifest_path, encoding="utf-8") as f:
            rows = json.load(f)
    except Exception:
        return None
    allowed_manual = {
        "Mixkit Stock Video Free License": _normalise_license_url(MIXKIT_VIDEO_LICENSE_URL),
        "Pixabay Content License": _normalise_license_url(PIXABAY_LICENSE_URL),
        "Coverr Stock Library License": _normalise_license_url(COVERR_LICENSE_URL),
        **{name: _normalise_license_url(url) for url, name in ARCHIVE_LICENSE_ALLOWLIST.items()},
    }
    for row in rows:
        local_path = _safe_pool_file(BROLL_POOL_DIR, row.get("filename"))
        if not local_path or row.get("manual_verified") is not True or not row.get("source_url"):
            continue
        manual_id = str(row.get("asset_id") or row.get("filename") or Path(local_path).name)
        if manual_id in USED_BROLL_IDS:
            continue
        license_name = str(row.get("license") or "")
        license_url = _normalise_license_url(row.get("license_url"))
        if allowed_manual.get(license_name) != license_url:
            continue
        if license_name.startswith("CC BY") and not (row.get("author") or row.get("creator") or row.get("attribution")):
            continue
        valid, duration = _probe_video_asset(local_path)
        if not valid:
            continue
        USED_BROLL_IDS.add(manual_id)
        return {
            "asset_type": "broll_video", "provider": row.get("provider", "User-imported verified pool"),
            "asset_id": row.get("asset_id", Path(local_path).stem), "title": row.get("title", Path(local_path).stem),
            "author": row.get("author", "Creator not listed"), "source_url": row.get("source_url"),
            "download_url": row.get("download_url"), "license": license_name, "license_url": license_url,
            "attribution_required": bool(row.get("attribution_required", license_name.startswith("Coverr") or license_name.startswith("CC BY"))),
            "attribution_text": row.get("attribution") or f"Footage: {row.get('title', Path(local_path).stem)}",
            "local_path": local_path, "duration": duration, "query": query,
            "sha256": sha256_file(local_path), "clip_index": index,
            "accessed_utc": datetime.now(timezone.utc).isoformat(),
        }
    return None


def broll_query_for_plan(plan):
    _gq = re.sub(r"[^a-zA-Z0-9 ]", " ", str(plan.notes.get("broll_query") or "")).strip()
    if len(_gq.split()) >= 2:
        return _gq[:80]
    topic = (plan.notes.get("topics") or ["business"])[0]
    phrase_map = {
        "money": "financial planning calculator business office",
        "business": "entrepreneur working business office",
        "entrepreneurship": "small business founder working",
        "self-improvement": "person training sunrise focused work",
        "psychology": "human behavior thoughtful conversation",
        "leadership": "team collaboration meeting",
        "technology": "technology computer data close up",
        "relationships": "people conversation human connection",
    }
    return phrase_map.get(topic, f"{topic} documentary cinematic")


def choose_broll_for_plan(plan, index):
    query = broll_query_for_plan(plan)
    RERANK_CONTEXT["broll"] = (f"A 3-4 second cutaway for a short video about: {plan.title}. Wanted visuals: {query}. "
                               f"Must illustrate the topic, show no identifiable people claiming to be the speaker, no text or logos.") if plan.notes.get("decided_by") == "gemini_director" else ""
    try:
        return _choose_broll_inner(plan, index, query)
    finally:
        RERANK_CONTEXT["broll"] = ""


def _choose_broll_inner(plan, index, query):
    # Coverr first (free key, commercially usable stock); Pixabay second (free key).
    if CFG.get("enable_coverr_assets", True) and (CFG.get("coverr_api_key") or _secret_value("COVERR_API_KEY")):
        for candidate_query in (query, "cinematic " + query.split()[0]):
            cache = os.path.join(ASSET_CACHE_DIR, f"coverr_broll_{hashlib.sha256(candidate_query.encode()).hexdigest()[:12]}.json")
            asset = _coverr_search_video(candidate_query, cache)
            if asset:
                asset["clip_index"] = index
                return asset
    if CFG.get("pixabay_api_key") or _secret_value("PIXABAY_API_KEY"):
        for candidate_query in (query, query.split()[0] + " documentary"):
            cache = os.path.join(ASSET_CACHE_DIR, f"pixabay_broll_{hashlib.sha256(candidate_query.encode()).hexdigest()[:12]}.json")
            asset = pixabay_search_video(candidate_query, cache)
            if asset:
                asset["clip_index"] = index
                return asset
    return choose_verified_manual_broll(query, index)


MUSIC_ASSETS, BROLL_ASSETS = {}, {}
for clip_index, plan in enumerate(PLANS, 1):
    music_path, music_asset = choose_music_for_plan(plan, clip_index)
    MUSIC_ASSETS[clip_index] = {"path": music_path, "asset": music_asset}
    broll_asset = choose_broll_for_plan(plan, clip_index)
    BROLL_ASSETS[clip_index] = broll_asset
    if broll_asset:
        ASSET_MANIFEST["broll"].append(broll_asset)
        log("broll", f"clip {clip_index}: contextual {broll_asset.get('provider')} visual selected ({broll_asset.get('asset_id')})")
    else:
        keyless = not (CFG.get("coverr_api_key") or _secret_value("COVERR_API_KEY") or CFG.get("pixabay_api_key") or _secret_value("PIXABAY_API_KEY"))
        why = "no free stock API key configured and no verified manual asset pool found" if keyless else "no relevant, downloadable, verified asset passed provider checks"
        log("broll", f"clip {clip_index}: {why}; source footage remains primary")

atomic_write_json(os.path.join(REPORT_DIR, "asset_license_manifest.json"), ASSET_MANIFEST)
