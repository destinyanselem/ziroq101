# CELL 9b — Finishing studio: completeness gate, Gemini critic, filler/pause tightening
# Why: the common failures of AI clippers are clips that end mid-thought, clips that need earlier context, and dead air / filler words.
# Vizard describes a dedicated "find the natural ending, then check completeness" step; Descript-style tools remove filler and silence.
# This cell does both at zero cost: deterministic local rules always run; Gemini (one extra request for all five clips) acts as an editor
# whose instructions are executed ONLY as bounded sentence-count fixes that the code validates.
FILLER_WORDS = {"um", "uh", "umm", "uhm", "uhh", "er", "erm", "hmm", "mm", "ah"}
INCOMPLETE_ENDINGS = {"and", "but", "because", "which", "that", "to", "of", "the", "a", "an", "or", "if", "when", "with", "for", "as",
                      "than", "then", "is", "are", "was", "were", "in", "on", "at", "my", "your", "our", "their", "this", "these"}
START_CONNECTORS = {"and", "but", "so", "because", "which", "or", "plus", "also", "then", "anyway", "cause"}


def _plan_cap(plan):
    """A normal clip may grow only up to the normal limit; a justified long clip may use the hard ceiling."""
    hard = float(CFG["max_clip_duration"])
    return hard if plan.notes.get("long_form_reason") else min(hard, float(CFG.get("long_clip_threshold_sec", 90.0)))


def sentence_complete(text):
    """Whisper punctuates finished sentences; a sentence with no terminal punctuation is treated as possibly cut off."""
    t = str(text or "").strip()
    return bool(t) and bool(re.search(r"[.!?][\"')\]]?$", t))


def _first_token(text):
    m = re.findall(r"[A-Za-z']+", str(text or "").lower())
    return m[0] if m else ""


def _sentence_range(transcript, start, end):
    idx = [i for i, s in enumerate(transcript.sentences) if s.end > start + 0.05 and s.start < end - 0.05]
    return (idx[0], idx[-1]) if idx else None


def _set_bounds(seg, transcript, si, ei, source_duration):
    s = transcript.sentences
    seg.source_start = round(max(0.0, s[si].start - 0.05), 2)
    seg.source_end = round(min(source_duration, s[ei].end + 0.10), 2)
    seg.transcript = " ".join(x.text for x in s[si:ei + 1]).strip()


def complete_plan_boundaries(plan, transcript, source, max_len=None):
    """Local completeness gate: never end on an unfinished sentence, never open on a dangling connector. Returns a list of log strings."""
    max_len = float(max_len or _plan_cap(plan))
    log_, sents = [], transcript.sentences
    if not sents:
        return log_
    last = plan.segments[-1]
    r = _sentence_range(transcript, last.source_start, last.source_end)
    if r:
        si, ei = r
        changed = False
        for _ in range(2):
            if sentence_complete(sents[ei].text) or ei + 1 >= len(sents):
                break
            nxt = sents[ei + 1]
            if nxt.start - sents[ei].end > 2.5 or (plan.duration - last.duration + (nxt.end + 0.10 - last.source_start)) > max_len:
                break
            ei += 1; changed = True
        if changed:
            _set_bounds(last, transcript, si, ei, source.duration); log_.append("ended on an unfinished sentence; extended to complete it")
        elif not sentence_complete(sents[ei].text):
            log_.append("ending may be unfinished (could not extend within limits)")
    first = plan.segments[0]
    r = _sentence_range(transcript, first.source_start, first.source_end)
    if r:
        si, ei = r
        trimmed = False
        while si < ei and _first_token(sents[si].text) in START_CONNECTORS and (plan.duration - (sents[si + 1].start - first.source_start)) >= 20.0:
            si += 1; trimmed = True
        if trimmed:
            _set_bounds(first, transcript, si, ei, source.duration); log_.append("opened on a dangling connector; started one sentence later")
        elif _first_token(sents[si].text) in START_CONNECTORS:
            log_.append("opens on a connector word (may need earlier context)")
    return log_


CRITIC_SCHEMA = {"type": "object", "properties": {"verdicts": {"type": "array", "items": {"type": "object", "properties": {
    "index": {"type": "integer", "minimum": 0},
    "complete_thought": {"type": "boolean"}, "needs_prior_context": {"type": "boolean"}, "ends_on_payoff": {"type": "boolean"},
    "issue": {"type": "string"},
    "extend_end_sentences": {"type": "integer", "minimum": 0, "maximum": 2},
    "trim_start_sentences": {"type": "integer", "minimum": 0, "maximum": 2}},
    "required": ["index", "complete_thought", "needs_prior_context", "ends_on_payoff", "extend_end_sentences", "trim_start_sentences"]}}},
    "required": ["verdicts"]}


def _plan_text(plan, limit=2300):
    return " [CUT] ".join(s.transcript.strip() for s in plan.segments)[:limit]


def _following_text(plan, transcript, n=2):
    r = _sentence_range(transcript, plan.segments[-1].source_start, plan.segments[-1].source_end)
    if not r:
        return ""
    return " ".join(x.text for x in transcript.sentences[r[1] + 1:r[1] + 1 + n])


def gemini_critic(plans, transcript):
    """One request reviews all clips like a strict editor. Returns {index: verdict} or {} when Gemini is unavailable."""
    if not (GEMINI.enabled and CFG.get("enable_gemini_critic", True)):
        return {}
    blocks = []
    for i, p in enumerate(plans):
        blocks.append(f"CLIP {i} — title: {p.title}\nTEXT: {_plan_text(p)}\nNEXT SENTENCES AFTER THE CLIP (not included): {_following_text(p, transcript) or '(none)'}")
    prompt = ("You are a strict short-form editor. For each clip decide: (1) complete_thought: does it end after a finished thought or payoff, "
              "not mid-idea? (2) needs_prior_context: would a cold viewer be lost because it refers to something said earlier? "
              "(3) ends_on_payoff: does the last line land? If the clip ends too early, set extend_end_sentences (0-2) using the NEXT SENTENCES. "
              "If the first sentence is a dependent fragment, set trim_start_sentences (0-2). Judge only from the text. Be conservative: "
              "use 0 unless the fix is clearly needed. Return one verdict per clip index.\n\n" + "\n\n".join(blocks))
    stage_set("gemini_critic", "running", "gemini")
    res = gemini_structured("critic_" + hashlib.sha1(prompt.encode()).hexdigest()[:10], prompt, CRITIC_SCHEMA, tier="reasoning",
                            validator=lambda d: [] if all(0 <= v["index"] < len(plans) for v in d["verdicts"]) else ["verdict index out of range"])
    if not res["ok"]:
        stage_set("gemini_critic", "completed_using_fallback", "local", f"{res['error_kind']}")
        return {}
    stage_set("gemini_critic", "completed", "gemini", f"{len(res['data']['verdicts'])} verdicts")
    return {v["index"]: v for v in res["data"]["verdicts"]}


def apply_critic(plan, verdict, transcript, source, max_len=None):
    """Bounded, validated execution of the critic's advice. Returns log strings."""
    max_len = float(max_len or _plan_cap(plan))
    notes, sents, out = [], transcript.sentences, []
    flags = plan.notes.setdefault("critic_flags", [])
    last = plan.segments[-1]
    if not verdict.get("complete_thought", True) and int(verdict.get("extend_end_sentences", 0)) > 0:
        r = _sentence_range(transcript, last.source_start, last.source_end)
        if r:
            si, ei = r
            for _ in range(min(2, int(verdict["extend_end_sentences"]))):
                if ei + 1 >= len(sents) or sents[ei + 1].start - sents[ei].end > 2.5:
                    break
                if plan.duration - last.duration + (sents[ei + 1].end + 0.10 - last.source_start) > max_len:
                    break
                ei += 1
            if ei != r[1]:
                _set_bounds(last, transcript, si, ei, source.duration); out.append("critic: extended ending to finish the thought")
    first = plan.segments[0]
    if verdict.get("needs_prior_context") and int(verdict.get("trim_start_sentences", 0)) > 0:
        r = _sentence_range(transcript, first.source_start, first.source_end)
        if r:
            si, ei = r
            k = min(2, int(verdict["trim_start_sentences"]))
            while k > 0 and si < ei and (plan.duration - (sents[si + 1].start - first.source_start)) >= 20.0:
                si += 1; k -= 1
            if si != r[0]:
                _set_bounds(first, transcript, si, ei, source.duration); out.append("critic: removed a context-dependent opening sentence")
    if verdict.get("needs_prior_context") and not out:
        flags.append("Gemini critic: a cold viewer may need earlier context" + (f" ({verdict['issue'][:100]})" if verdict.get("issue") else ""))
    if not verdict.get("ends_on_payoff", True):
        flags.append("Gemini critic: the ending may not land on a payoff")
    plan.notes["critic"] = {k: verdict.get(k) for k in ("complete_thought", "needs_prior_context", "ends_on_payoff", "issue")}
    return out


def tighten_plan(plan, transcript, source):
    """Remove filler words and long pauses by splitting segments into kept pieces. Cuts are made only in silence/filler
    (never inside a spoken word), keep at least ~0.24 s of natural pause, leave the first/last 0.4 s of each segment alone, and
    remove at most `tighten_max_fraction` of a segment. Adds 12 ms audio fades at every cut (render_plan) and alternates a mild
    punch-in on continuation pieces to hide the jump cut."""
    stats = {"removed_sec": 0.0, "fillers": 0, "pauses": 0, "pieces_added": 0}
    if not CFG.get("enable_tighten", True) or not transcript.words:
        return stats
    thr, max_frac = float(CFG.get("tighten_pause_sec", 0.60)), float(CFG.get("tighten_max_fraction", 0.20))
    starts = [w.start for w in transcript.words]
    new_segments = []
    for seg in plan.segments:
        lo, hi = bisect.bisect_left(starts, seg.source_start - 0.5), bisect.bisect_right(starts, seg.source_end + 0.5)
        ws = [w for w in transcript.words[lo:hi] if seg.source_start <= (w.start + w.end) / 2 < seg.source_end]
        cuts = []
        for k, w in enumerate(ws):
            nxt = ws[k + 1] if k + 1 < len(ws) else None
            if CFG.get("tighten_fillers", True) and re.sub(r"[^a-z]", "", w.word.lower()) in FILLER_WORDS:
                prev_end = ws[k - 1].end if k > 0 else seg.source_start
                a, b = prev_end + 0.06, (nxt.start if nxt else seg.source_end) - 0.06
                if b - a >= 0.18:
                    cuts.append([a, b, "filler"])
                continue
            if nxt is not None and nxt.start - w.end > thr:
                cuts.append([w.end + 0.12, nxt.start - 0.12, "pause"])
        guard_a, guard_b = seg.source_start + 0.4, seg.source_end - 0.4
        cuts = [[max(a, guard_a), min(b, guard_b), kind] for a, b, kind in cuts if min(b, guard_b) - max(a, guard_a) >= 0.18]
        merged = []
        for c in sorted(cuts):
            if merged and c[0] <= merged[-1][1] + 0.01:
                merged[-1][1] = max(merged[-1][1], c[1]); merged[-1][2] = "filler" if "filler" in (merged[-1][2], c[2]) else "pause"
            else:
                merged.append(c)
        # budget: drop the smallest cuts first until within the fraction
        while merged and sum(b - a for a, b, _ in merged) > max_frac * seg.duration:
            merged.remove(min(merged, key=lambda c: c[1] - c[0]))
        # every kept piece must stay >= 0.8 s
        kept, cursor = [], seg.source_start
        for a, b, kind in merged:
            if a - cursor >= 0.8 and (seg.source_end - b) >= 0.8:
                kept.append((a, b, kind)); cursor = b
        if not kept:
            new_segments.append(seg); continue
        pieces, cursor = [], seg.source_start
        for a, b, kind in kept:
            pieces.append((cursor, a)); cursor = b
            stats["fillers" if kind == "filler" else "pauses"] += 1
        pieces.append((cursor, seg.source_end))
        for n, (a, b) in enumerate(pieces):
            text = " ".join(w.word for w in ws if a <= (w.start + w.end) / 2 < b).strip()
            if n == 0:
                role = seg.role
            else:
                role = "tight_zoom" if (n % 2 == 1 and CFG.get("punch_in_zoom", True)) else "tight_cont"
            new_segments.append(Segment(source_start=round(a, 3), source_end=round(b, 3), purpose=seg.purpose, transcript=text,
                                        crop_fx=seg.crop_fx, role=role, confidence=seg.confidence))
        stats["removed_sec"] += sum(b - a for a, b, _ in kept)
        stats["pieces_added"] += len(pieces) - 1
    if stats["pieces_added"]:
        plan.segments = new_segments
    stats["removed_sec"] = round(stats["removed_sec"], 2)
    return stats


def finish_plans(plans, transcript, source):
    """Run once before rendering. Idempotent (plans carry notes['finished'])."""
    todo = [p for p in plans if not p.notes.get("finished")]
    if not todo:
        return []
    for p in todo:
        p.notes["finish_log"] = []
        if CFG.get("enable_completeness_gate", True):
            p.notes["finish_log"] += complete_plan_boundaries(p, transcript, source)
    verdicts = gemini_critic(todo, transcript)
    for i, p in enumerate(todo):
        if i in verdicts:
            p.notes["finish_log"] += apply_critic(p, verdicts[i], transcript, source)
        p.notes["tighten"] = tighten_plan(p, transcript, source)
        t = p.notes["tighten"]
        if t["pieces_added"]:
            p.notes["finish_log"].append(f"tightened: removed {t['removed_sec']}s ({t['fillers']} filler, {t['pauses']} pause cuts)")
        p.validate(source.duration)
        p.notes["finished"] = True
    for i, p in enumerate(todo, 1):
        for line in p.notes["finish_log"]:
            log("finish", f"clip {i}: {line}")
    return [p.notes["finish_log"] for p in todo]
