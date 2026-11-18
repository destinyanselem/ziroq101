# CELL 6b — Gemini DIRECTOR (brain) -> validated ClipPlans (body executes)
# Gemini watches the whole video from its public YouTube link and returns timestamped clip proposals + music direction.
# The notebook NEVER trusts timestamps blindly: each one is snapped to local Whisper sentence boundaries, checked against the
# transcript, de-duplicated and length-capped. Anything Gemini cannot supply is filled by the local selector (CELL 7).
# Free-tier note (Google docs, 2026-11-13): YouTube-URL input is a free preview, max 8 hours of video/day/project, public videos only.
DIRECTOR_SCHEMA = {
    "type": "object",
    "properties": {
        "video_summary": {"type": "string"},
        "clips": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "narrative_type": {"type": "string", "enum": ["lesson", "story", "myth_vs_truth", "framework", "warning", "case_study", "surprising_insight"]},
                "start": {"type": "string"},          # "MM:SS" or "H:MM:SS"
                "end": {"type": "string"},
                "hook": {"type": "string"},
                "why_it_works": {"type": "string"},
                "long_form_reason": {"type": "string"},   # required only for clips over 90 seconds
                "opening_words": {"type": "string"},  # first ~8 words spoken, used to verify the timestamp
                "closing_words": {"type": "string"},
                "score": {"type": "integer", "minimum": 1, "maximum": 100},
                "topics": {"type": "array", "items": {"type": "string"}},
                "music": {"type": "object", "properties": {
                    "mood": {"type": "string", "enum": ["reflective", "uplifting", "playful", "curious", "documentary"]},
                    "energy": {"type": "string", "enum": ["low", "medium", "high"]},
                    "search_terms": {"type": "string"},   # 2-5 words for a royalty-free music library
                    "avoid": {"type": "string"}},
                    "required": ["mood", "energy", "search_terms"]},
                "broll_query": {"type": "string"},
                "sfx_cues": {"type": "array", "items": {"type": "object", "properties": {
                    "time": {"type": "string"}, "kind": {"type": "string", "enum": ["whoosh", "impact", "pop", "riser"]}},
                    "required": ["time", "kind"]}},
                "framing": {"type": "object", "properties": {
                    "layout": {"type": "string", "enum": ["single_speaker", "multi_speaker", "slides_or_screen", "other"]},
                    "speakers_x": {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1}},
                    "speaker_x": {"type": "array", "items": {"type": "object", "properties": {
                        "time": {"type": "string"}, "x": {"type": "number", "minimum": 0, "maximum": 1}},
                        "required": ["time", "x"]}}},
                    "required": ["layout"]},
                "visual_notes": {"type": "string"}},
            "required": ["title", "start", "end", "hook", "opening_words", "closing_words", "score", "music"]}},
    },
    "required": ["clips"],
}


def parse_timecode(value):
    """'MM:SS', 'H:MM:SS' or seconds -> float seconds; None if unparseable."""
    if isinstance(value, (int, float)):
        return float(value)
    parts = str(value).strip().split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if not 1 <= len(nums) <= 3:
        return None
    sec = 0.0
    for n in nums:
        sec = sec * 60 + n
    return sec


def _norm_tokens(text):
    return re.findall(r"[a-z0-9']+", str(text).lower())


def _token_overlap(a, b):
    ta, tb = _norm_tokens(a), set(_norm_tokens(b))
    return sum(1 for t in ta if t in tb) / len(ta) if ta else 0.0


def snap_interval_to_sentences(start, end, transcript, opening_words="", closing_words="", search_pad=12.0):
    """Align a Gemini timestamp pair to real sentence boundaries. The opening/closing words Gemini reports are used to
    re-locate the boundary if its timestamp drifted (Gemini timecodes are ~1s-accurate, sometimes worse in long videos).
    Returns (start, end, quality) or None if the transcript cannot support the interval."""
    sents = transcript.sentences
    if not sents:
        return None
    def best_sentence(t, words, mode):
        near = [i for i, s in enumerate(sents) if s.end >= t - search_pad and s.start <= t + search_pad]
        if not near:
            return None
        def key(i):
            s = sents[i]
            text_score = _token_overlap(words, s.text) if (words and mode == "start") else \
                         (_token_overlap(words, s.text) if words else 0.0)
            dist = abs((s.start if mode == "start" else s.end) - t)
            return (text_score >= 0.6, text_score, -dist)
        return max(near, key=key)
    si, ei = best_sentence(start, opening_words, "start"), best_sentence(end, closing_words, "end")
    if si is None or ei is None or ei < si:
        return None
    s_sent, e_sent = sents[si], sents[ei]
    open_ok = (not opening_words) or _token_overlap(opening_words, " ".join(x.text for x in sents[si:si + 2])) >= 0.5
    close_ok = (not closing_words) or _token_overlap(closing_words, " ".join(x.text for x in sents[max(si, ei - 1):ei + 1])) >= 0.5
    quality = "verified" if (open_ok and close_ok) else "timestamp_only"
    return max(0.0, s_sent.start - 0.05), e_sent.end + 0.10, quality


def trim_to_limit(start, end, transcript, limit):
    """If a proposal exceeds the 90s cap, end it at the last sentence boundary that fits (never mid-sentence)."""
    if end - start <= limit:
        return start, end
    ends = [s.end + 0.10 for s in transcript.sentences if start < s.end + 0.10 <= start + limit]
    return (start, ends[-1]) if ends and ends[-1] - start >= 20.0 else (start, None)


def parse_sfx_cues(raw):
    out = []
    for c in raw or []:
        if isinstance(c, dict) and c.get("kind") in ("whoosh", "impact", "pop", "riser"):
            t = parse_timecode(c.get("time"))
            if t is not None:
                out.append((float(t), c["kind"]))
    return sorted(out)[:6]


def parse_framing_hint(raw):
    """Gemini framing -> {"layout": str, "points": [(source_seconds, x 0..1)]} with invalid samples dropped."""
    if not isinstance(raw, dict):
        return None
    layout = raw.get("layout") if raw.get("layout") in ("single_speaker", "multi_speaker", "slides_or_screen", "other") else "other"
    pts = []
    for p in raw.get("speaker_x") or []:
        t, x = parse_timecode(p.get("time")), p.get("x")
        if t is not None and isinstance(x, (int, float)) and 0.0 <= x <= 1.0:
            pts.append((float(t), float(x)))
    xs = sorted(float(x) for x in (raw.get("speakers_x") or []) if isinstance(x, (int, float)) and not isinstance(x, bool) and 0.0 <= x <= 1.0)
    return {"layout": layout, "points": sorted(pts), "speakers_x": xs if len(xs) == 2 else []}


def long_clip_gate(s0, s1, transcript):
    """Deterministic check that a clip longer than the normal limit is dense enough to hold attention. Returns (ok, reasons)."""
    words = [w for w in transcript.words if w.start >= s0 - 0.05 and w.end <= s1 + 0.05]
    dur, reasons = max(1e-6, s1 - s0), []
    if len(words) / dur < 1.7:
        reasons.append(f"speech density {len(words) / dur:.1f} words/s is below 1.7")
    gap, prev = 0.0, s0
    for w in words:
        gap = max(gap, w.start - prev)
        prev = w.end
    gap = max(gap, s1 - prev)
    if gap > 4.0:
        reasons.append(f"a {gap:.1f}s silence")
    if len([s for s in transcript.sentences if s.end > s0 and s.start < s1]) < 8:
        reasons.append("fewer than 8 sentences")
    return (not reasons), reasons


def director_plans_from_response(data, transcript, source_duration, required, plan_cls=None):
    """Pure function (testable): Gemini JSON -> list of validated ClipPlans + rejection log."""
    plans, rejected, used = [], [], []
    max_len = float(CFG["max_clip_duration"])
    long_thr, long_used = float(CFG.get("long_clip_threshold_sec", 90.0)), 0
    clips = sorted(data.get("clips", []), key=lambda c: -int(c.get("score", 0)))
    for c in clips:
        t0, t1 = parse_timecode(c.get("start")), parse_timecode(c.get("end"))
        title = str(c.get("title", "?"))[:60]
        if t0 is None or t1 is None or t1 <= t0:
            rejected.append((title, "unparseable timestamps")); continue
        if t1 > source_duration + 2.0 or t0 < 0:
            rejected.append((title, "timestamps outside the video")); continue
        snapped = snap_interval_to_sentences(t0, min(t1, source_duration), transcript, c.get("opening_words", ""), c.get("closing_words", ""))
        if snapped is None:
            rejected.append((title, "no transcript support at those times")); continue
        s0, s1, quality = snapped
        long_reason, long_note = "", ""
        if s1 - s0 > long_thr:
            reason = str(c.get("long_form_reason", "")).strip()
            gate_ok, gate_why = long_clip_gate(s0, s1, transcript)
            if CFG.get("allow_long_clips", True) and len(reason) >= 12 and gate_ok and long_used < int(CFG.get("director_long_max_clips", 2)):
                long_reason, long_used = reason[:240], long_used + 1
            else:
                why_not = ("no allowed long-clip reason" if len(reason) < 12 else "; ".join(gate_why) if not gate_ok else "long-clip quota used")
                long_note = f"shortened to the normal limit ({why_not})"
                s0, s1 = trim_to_limit(s0, s1, transcript, long_thr)
                if s1 is None:
                    rejected.append((title, "cannot trim to 90s at a sentence boundary")); continue
        s0, s1 = trim_to_limit(s0, s1, transcript, max_len)
        if s1 is None:
            rejected.append((title, "cannot trim to 90s at a sentence boundary")); continue
        if s1 - s0 < float(CFG.get("director_min_clip_sec", 20.0)):
            rejected.append((title, "too short after snapping")); continue
        if any(min(s1, b) - max(s0, a) > 0.25 * (s1 - s0) for a, b in used):
            rejected.append((title, "overlaps a higher-scored clip")); continue
        text = " ".join(s.text for s in transcript.sentences if s.end > s0 and s.start < s1).strip()
        seg = Segment(source_start=round(s0, 2), source_end=round(s1, 2), purpose="gemini_director", transcript=text,
                      role="hook", confidence=1.0 if quality == "verified" else 0.7)
        music = c.get("music") or {}
        plan = ClipPlan(title=title, narrative_type=c.get("narrative_type", "lesson"), segments=[seg],
                        target_duration=max_len,
                        notes={"decided_by": "gemini_director", "boundary_quality": quality, "editorial_score": float(c.get("score", 50)),
                               "topics": [str(t).lower() for t in (c.get("topics") or [])][:4] or ["business"],
                               "hook": c.get("hook", ""), "why_it_works": c.get("why_it_works", ""),
                               "music_brief": {k: music.get(k) for k in ("mood", "energy", "search_terms", "avoid")},
                               "broll_query": c.get("broll_query", ""), "visual_notes": c.get("visual_notes", ""),
                               "gemini_framing": parse_framing_hint(c.get("framing")),
                               "sfx_cues": parse_sfx_cues(c.get("sfx_cues")),
                               "long_form_reason": long_reason, "long_form_note": long_note})
        try:
            plan.validate(source_duration)
        except ValueError as exc:
            rejected.append((title, str(exc))); continue
        plans.append(plan); used.append((s0, s1))
        if len(plans) >= required:
            break
    return plans, rejected


DIRECTOR_PROMPT = """You are the editorial director for a short-form video studio. Watch the ENTIRE video, then choose the {n} best
self-contained moments for vertical Shorts/Reels/TikTok (each ideally {lo}-{hi} seconds).
{long_para}
Prefer moments with a strong first sentence, a complete idea that makes sense without the rest of the video, a payoff or lesson, and
emotional or intellectual surprise. Spread the picks across different parts of the video and different ideas; never overlap.
Each clip must be one CONTINUOUS stretch. Start on the first word of a sentence and end after the last word of a sentence.
Give start/end as MM:SS (or H:MM:SS) exactly as shown in the video player. Quote the first and last ~8 spoken words exactly
(opening_words/closing_words) so a program can verify the timestamps against the transcript.
For music: pick the mood that best supports the clip WITHOUT competing with speech, and 2-5 search words usable on a royalty-free
music library (instrument/genre/feel, no artist or song names). For broll_query give 3-6 concrete visual words, no names or brands.
For sfx_cues: at most 4 sound effects, only where one naturally supports a transition or a key emphasis (whoosh, impact, pop, riser).
Give time as MM:SS in the source video; return an empty list if none is needed; never place one over an important word.
For framing: say whether the main shot is a single_speaker, multi_speaker, slides_or_screen or other. For single_speaker give 2-8
speaker_x samples across the clip (for multi_speaker with exactly two people side by side give speakers_x = their two face centres, left then right): time (MM:SS) and x = horizontal centre of the speaker's face as a fraction of frame width (0=left, 1=right).
Return {n_plus} candidates ranked by score so unusable ones can be dropped. Video title: {title}."""


def gemini_director(source_url, source, transcript):
    """Returns (plans, info). plans may be shorter than required; the caller fills the remainder locally."""
    info = {"used": False, "reason": "", "rejected": []}
    if not (GEMINI.enabled and CFG.get("use_gemini_director", True)):
        info["reason"] = "gemini disabled"; return [], info
    required = int(CFG["required_clip_count"])
    long_para = (f"LONG CLIPS: you may choose a clip of 91-{int(CFG['max_clip_duration'])} seconds ONLY when the idea truly needs that time (a complete story, case study, framework or argument whose payoff would be lost if cut), every part stays gripping with no slow patches, and you explain why in long_form_reason. At most {int(CFG.get('director_long_max_clips', 2))} of the {required} clips may be long. Never pad or stretch; shorter is better when the idea is complete sooner."
                 if CFG.get("allow_long_clips", True) else "Hard maximum 90 seconds.")
    prompt = DIRECTOR_PROMPT.format(n=required, n_plus=required + 4, title=source.title, long_para=long_para, lo=CFG.get('director_clip_sec', [30, 75])[0], hi=CFG.get('director_clip_sec', [30, 75])[1])
    pin, cache_media, uploaded = None, None, None
    if re.search(r"(youtube\.com|youtu\.be)", str(source_url or "")):
        media = {"type": "video", "uri": source_url}
    elif CFG.get("gemini_upload_local", True) and source.video_path and os.path.isfile(source.video_path):
        stage_set("gemini_video_direction", "running", "gemini", "uploading a small proxy of the local file")
        try:
            proxy = build_gemini_proxy(source)
            uploaded = GEMINI.upload_file(proxy)
        except Exception as exc:
            info["reason"] = f"media upload failed: {gem_scrub(exc)[:100]}"
            stage_set("gemini_video_direction", "completed_using_fallback", "local", info["reason"]); return [], info
        media = {"type": "video", "uri": uploaded["uri"], "mime_type": uploaded["mime_type"]}
        pin, cache_media = uploaded["label"], {"local": source.video_id, "duration": round(source.duration)}
    else:
        info["reason"] = "not a YouTube link and no local file to upload"; return [], info
    if CFG.get("gemini_agentic_video", False):
        media["processing"] = "agentic"
    stage_set("gemini_video_direction", "running", "gemini")
    try:
        res = gemini_structured("director_" + source.video_id, prompt, DIRECTOR_SCHEMA, tier="reasoning", media=media, pin=pin, cache_media=cache_media,
                                validator=lambda d: [] if len(d.get("clips", [])) >= 3 else ["fewer than 3 clips returned"])
    finally:
        if uploaded:
            GEMINI.delete_file(uploaded)
    if not res["ok"]:
        info["reason"] = f"{res['error_kind']}: {str(res['error'])[:120]}"
        stage_set("gemini_video_direction", "completed_using_fallback" if res["error_kind"] != "waiting_for_quota" else "waiting_for_quota",
                  "local", info["reason"])
        return [], info
    plans, rejected = director_plans_from_response(res["data"], transcript, source.duration, required)
    info.update(used=bool(plans), rejected=rejected, model=res["model"], project=res["project"], summary=res["data"].get("video_summary", ""))
    stage_set("gemini_video_direction", "completed" if len(plans) >= required else "completed_using_fallback", "gemini+local",
              f"{len(plans)} accepted, {len(rejected)} rejected by validation")
    return plans, info


def merge_director_with_local(director_plans, local_plans, required):
    """Director picks come first; local plans fill gaps without overlapping them."""
    out = list(director_plans)
    for lp in sorted(local_plans, key=lambda p: -p.notes.get("editorial_score", 0)):
        if len(out) >= required:
            break
        ov = sum(max(0.0, min(a.source_end, b.source_end) - max(a.source_start, b.source_start)) for a in lp.segments for p in out for b in p.segments)
        if ov / max(lp.duration, 1.0) <= 0.30:
            lp.notes.setdefault("decided_by", "local_selector")
            out.append(lp)
    return out[:required]


def build_gemini_proxy(source):
    """Small low-fps proxy for upload (keeps the timeline identical, so timestamps map 1:1 to the original)."""
    proxy = os.path.join(os.path.dirname(source.video_path), "gemini_proxy.mp4")
    if not os.path.isfile(proxy):
        run(["ffmpeg", "-y", "-v", "error", "-i", source.video_path, "-vf", "scale=-2:360,fps=4", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "34", "-c:a", "aac", "-b:a", "48k", "-ac", "1", "-ar", "16000", "-movflags", "+faststart", proxy], quiet=True)
    return proxy


# ---- Gemini ranks licensed search results (it only reorders; licence/decode verification still happens afterwards)
RERANK_CONTEXT = {"music": "", "broll": ""}
RERANK_SCHEMA = {"type": "object", "properties": {"ranking": {"type": "array", "items": {"type": "integer"}}}, "required": ["ranking"]}


def _describe_asset(item):
    if not isinstance(item, dict):
        return str(item)
    bits = []
    for k in ("title", "name", "tags", "subject", "description"):
        v = item.get(k)
        if v:
            bits.append(" ".join(map(str, v)) if isinstance(v, list) else str(v))
    return " | ".join(bits) or "untitled"


def rerank_candidates(kind, items, describe=_describe_asset, top_n=15):
    ctx = RERANK_CONTEXT.get(kind) or ""
    pool = globals().get("GEMINI")
    if not ctx or not items or not CFG.get("gemini_rerank", True) or pool is None or not getattr(pool, "enabled", False):
        return items
    head, tail = list(items[:top_n]), list(items[top_n:])
    if len(head) < 2:
        return items
    lines = "\n".join(f"{i}: {describe(x)[:140]}" for i, x in enumerate(head))
    what = "background music track" if kind == "music" else "stock video clip"
    prompt = (f"Rank these {what} candidates from best to worst fit for the brief below. Judge only from the text given. "
              f"Return JSON {{\"ranking\": [ids best-first]}} using each id at most once.\n\nBRIEF: {ctx[:700]}\n\nCANDIDATES:\n{lines}")
    res = gemini_structured(f"rerank_{kind}", prompt, RERANK_SCHEMA, tier="fast",
                            validator=lambda d: [] if all(isinstance(i, int) and 0 <= i < len(head) for i in d["ranking"]) else ["ranking ids out of range"])
    if not res["ok"]:
        return items
    order = []
    for i in res["data"]["ranking"]:
        if i not in order:
            order.append(i)
    order += [i for i in range(len(head)) if i not in order]
    return [head[i] for i in order] + tail


DIRECTOR_INFO = {"used": False, "reason": "not run"}
