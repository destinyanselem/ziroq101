# CELL 7 — Editorial window scoring, cross-timestamp story reconstruction, and five-clip diversity
from sklearn.metrics.pairwise import cosine_similarity


def editorial_features(text, confidence, duration, words_per_second):
    low = (text or "").lower()
    tok = set(tokens(low))
    hook = min(10.0, 2.0 * len(tok & HOOK_CUES)) + (3.0 if "?" in text else 0.0)
    specificity = min(10.0, 3.5 * bool(re.search(r"\b\d+(?:\.\d+)?\b", text)) + 2.5 * bool(re.search(r"\b(first|second|third|percent|million|billion|years|months|days)\b", low)))
    story = min(10.0, 2.0 * len(tok & {x.lower() for x in STORY_CUES}))
    payoff = min(10.0, 2.5 * sum(phrase in low for phrase in ("i learned", "the lesson", "that's why", "in the end", "the result", "which means")))
    action = min(10.0, 2.0 * len(tok & ACTION_CUES))
    density = max(0.0, 8.0 - abs(words_per_second - 2.6) * 3.0)
    confidence_score = max(0.0, min(10.0, confidence * 10.0))
    clarity = 6.0 if len(text.split()) >= 15 and len(text.split()) <= 90 else 3.0
    return {
        "hook": hook, "specificity": specificity, "story": story, "payoff": payoff,
        "actionability": action, "speech_density": density,
        "transcription_confidence": confidence_score, "standalone_clarity": clarity,
    }


def make_editorial_windows(transcript, content_map):
    windows = []
    n = len(transcript.sentences)
    starts = [w.start for w in transcript.words]
    for i in range(0, n, 2):
        best = None
        for j in range(i + 1, min(n, i + 8) + 1):
            start = transcript.sentences[i].start
            end = transcript.sentences[j - 1].end
            duration = end - start
            if duration > 27.0:
                break
            if duration < 8.0:
                continue
            text = " ".join(s.text for s in transcript.sentences[i:j])
            lo = bisect.bisect_left(starts, start - 0.01)
            hi = bisect.bisect_right(starts, end + 0.01)
            ws = transcript.words[lo:hi]
            if len(ws) < 12:
                continue
            avg_conf = float(np.mean([w.prob for w in ws]))
            wps = len(ws) / max(duration, 0.1)
            feats = editorial_features(text, avg_conf, duration, wps)
            topics, emotions, role = classify_text(text)
            # Explainable editorial heuristic; intentionally not labelled a virality prediction.
            score = (2.2 * feats["hook"] + 1.2 * feats["specificity"] + 1.1 * feats["story"] +
                     1.25 * feats["payoff"] + 1.1 * feats["actionability"] +
                     0.7 * feats["speech_density"] + 0.8 * feats["transcription_confidence"] +
                     0.8 * feats["standalone_clarity"])
            row = {
                "window_id": len(windows), "sentence_start_index": i,
                "sentence_end_index": j - 1, "source_start": start,
                "source_end": end, "duration": duration, "text": text,
                "topics": topics, "emotions": emotions, "role": role,
                "confidence": avg_conf, "word_count": len(ws),
                "words_per_second": wps, "features": feats, "score": score,
            }
            if best is None or abs(duration - 17.0) < abs(best["duration"] - 17.0):
                best = row
        if best is not None:
            windows.append(best)
    # Keep the strongest editorial windows while retaining whole-source time coverage.
    if len(windows) > 900:
        windows = sorted(windows, key=lambda x: x["score"], reverse=True)[:900]
        windows.sort(key=lambda x: x["source_start"])
    for idx, row in enumerate(windows):
        row["window_id"] = idx
    return windows


def window_vector_matrix(windows, sentence_vectors):
    rows = []
    for win in windows:
        s0, s1 = win["sentence_start_index"], win["sentence_end_index"] + 1
        if hasattr(sentence_vectors, "tocsr"):
            v = sentence_vectors[s0:s1].mean(axis=0)
            rows.append(np.asarray(v).ravel())
        else:
            v = np.asarray(sentence_vectors[s0:s1]).mean(axis=0)
            norm = np.linalg.norm(v)
            rows.append(v / norm if norm else v)
    if hasattr(sentence_vectors, "tocsr"):
        from scipy.sparse import csr_matrix
        return csr_matrix(np.vstack(rows))
    return np.vstack(rows).astype(np.float32)


def similarity_matrix(windows, vectors):
    win_vectors = window_vector_matrix(windows, vectors)
    return cosine_similarity(win_vectors).astype(np.float32)


def headline_from_source_text(text, max_words=8):
    # Extractive title: it is assembled from the speaker's transcript, not invented dialogue.
    first_sentence = re.split(r"(?<=[.!?])\s+", text.strip())[0]
    words = first_sentence.split()
    headline = " ".join(words[:max_words]).strip(" ,:;—-")
    return headline or "A useful idea worth remembering"


def overlap_ratio(a, b):
    intersection = max(0.0, min(a["source_end"], b["source_end"]) - max(a["source_start"], b["source_start"]))
    union = max(a["source_end"], b["source_end"]) - min(a["source_start"], b["source_start"])
    return intersection / union if union > 0 else 0.0


def _local_cap():
    return min(float(CFG["max_clip_duration"]), float(CFG.get("local_clip_max_sec", 90.0)))


def candidate_story(anchor_idx, windows, sim, source):
    anchor = windows[anchor_idx]
    support_rows = []
    for j, other in enumerate(windows):
        if j == anchor_idx or overlap_ratio(anchor, other) > 0.05:
            continue
        similarity = float(sim[anchor_idx, j])
        if similarity < (0.10 if SIMILARITY_BACKEND == "tfidf_fallback" else 0.20):
            continue
        # Complementary beats improve narrative progression; this remains an explainable heuristic.
        role_bonus = 0.0
        if other["role"] != anchor["role"]:
            role_bonus += 0.12
        if other["role"] in ("explanation", "story", "lesson", "payoff"):
            role_bonus += 0.10
        topic_bonus = 0.12 if set(anchor["topics"]) & set(other["topics"]) else 0.0
        quality = min(1.0, other["score"] / 95.0)
        # Prefer passages related to the anchor and not simply adjacent repeats.
        rank_score = 0.58 * similarity + role_bonus + topic_bonus + 0.12 * quality
        support_rows.append((rank_score, j, similarity))
    support_rows.sort(reverse=True)

    chosen = [anchor]
    total = anchor["duration"]
    for rank_score, j, similarity in support_rows:
        other = windows[j]
        if any(overlap_ratio(other, x) > 0.05 for x in chosen):
            continue
        # A coherent short can be under 90 seconds; no filler is added to hit a target.
        if total + other["duration"] > _local_cap():
            continue
        chosen.append(other)
        total += other["duration"]
        if total >= CFG["preferred_min_duration"] or len(chosen) >= 4:
            break

    # If semantic support is sparse, extend the anchor with its immediately adjacent context
    # without crossing the source boundary. This remains one real continuous source interval.
    if len(chosen) == 1 and total < 42.0:
        idx0, idx1 = anchor["sentence_start_index"], anchor["sentence_end_index"]
        sentences = TRANSCRIPT.sentences
        start, end = anchor["source_start"], anchor["source_end"]
        left, right = idx0, idx1
        while end - start < min(65.0, _local_cap()):
            left_dur = sentences[left - 1].end - start if left > 0 else -1
            right_dur = sentences[right + 1].end - start if right + 1 < len(sentences) else 999
            if right_dur <= _local_cap() and (left_dur < 0 or abs(62 - right_dur) <= abs(62 - left_dur)):
                right += 1; end = sentences[right].end
            elif left_dur >= 0 and left_dur <= _local_cap():
                left -= 1; start = sentences[left].start
            else:
                break
        if end > start and end - start <= _local_cap():
            text = " ".join(s.text for s in sentences[left:right+1])
            chosen = [{**anchor, "source_start": start, "source_end": end,
                       "duration": end-start, "text": text, "role": classify_text(text)[2]}]
            total = end - start

    # Hook is first; supporting material is then arranged in source-time order.
    support = sorted(chosen[1:], key=lambda x: x["source_start"])
    sequence = [chosen[0]] + support
    segments = []
    for idx, item in enumerate(sequence):
        role = "hook" if idx == 0 else item["role"]
        segments.append(Segment(
            source_start=round(item["source_start"], 3),
            source_end=round(item["source_end"], 3),
            purpose=role,
            transcript=item["text"],
            role=role,
            confidence=float(item["confidence"]),
        ))
    if sum(s.duration for s in segments) > _local_cap() + 0.02:
        return None
    sem_sim = [x[2] for x in support_rows if any(x[1] == windows.index(c) for c in support)]
    mean_sim = float(np.mean(sem_sim)) if sem_sim else 0.0
    diversity = len({topic for item in sequence for topic in item["topics"]})
    avg_conf = float(np.mean([s.confidence for s in segments]))
    hook_score = anchor["features"]["hook"]
    payoff_score = max([x["features"]["payoff"] for x in sequence] or [0.0])
    story_score = min(100.0, 0.42 * anchor["score"] + 22.0 * mean_sim + 2.5 * diversity + 0.8 * payoff_score + 5.0 * avg_conf)
    title = headline_from_source_text(anchor["text"])
    plan = ClipPlan(
        title=title,
        narrative_type=anchor["role"],
        segments=segments,
        target_duration=_local_cap(),
        notes={
            "editorial_score": round(story_score, 2),
            "score_type": "explainable editorial heuristic; not a virality or retention prediction",
            "hook_score": round(hook_score, 2),
            "semantic_similarity_mean": round(mean_sim, 3),
            "similarity_backend": SIMILARITY_BACKEND,
            "topics": sorted({t for x in sequence for t in x["topics"]}),
            "emotions": sorted({e for x in sequence for e in x["emotions"]}),
            "source_intervals": [[s.source_start, s.source_end] for s in segments],
            "non_chronological_reorder": any(segments[i].source_start < segments[i-1].source_start for i in range(1, len(segments))),
            "context_review_required": True,
            "source_dialogue_only": True,
            "retention_quality_map": [
                {"output_start": round(sum(x.duration for x in segments[:k]), 2),
                 "output_end": round(sum(x.duration for x in segments[:k+1]), 2),
                 "editorial_role": x.purpose,
                 "attention_risk": "review for slow explanation" if x.purpose == "explanation" else "review for missing setup" if x.purpose == "hook" else "review for payoff strength" if x.purpose == "payoff" else "standard review",
                 "score_type": "editorial review heuristic, not measured retention"}
                for k, x in enumerate(segments)
            ],
        },
    )
    plan.validate(source.duration)
    return plan


def select_five_distinct_stories(windows, sim, source):
    """Strict pass first; if fewer than five stories qualify, relax the gates step by step (never below a floor) and
    label every plan that needed relaxing so you can review it. Distinct-source-time protection always stays on."""
    required = int(CFG["required_clip_count"])
    base = float(CFG["min_story_score"])
    ladder = [  # (min story score, min anchor score, max source overlap, enforce same-topic rule)
        (base, 18, 0.30, True),
        (base * 0.85, 16, 0.40, True),
        (base * 0.70, 14, 0.50, False),
        (base * 0.55, 10, 0.60, False),
    ]
    ranked_anchors = sorted(range(len(windows)), key=lambda i: windows[i]["score"], reverse=True)
    plan_cache, selected, all_candidates = {}, [], []

    def overlap_with(plan, prior):
        total = 0.0
        for a in plan.segments:
            for b in prior.segments:
                total += max(0.0, min(a.source_end, b.source_end) - max(a.source_start, b.source_start))
        return total / max(plan.duration, 1.0)

    for level, (min_score, anchor_floor, max_overlap, topic_rule) in enumerate(ladder):
        candidates, seen = [], set()
        for idx in ranked_anchors:
            if windows[idx]["score"] < anchor_floor:
                continue
            if idx not in plan_cache:
                plan_cache[idx] = candidate_story(idx, windows, sim, source)
            plan = plan_cache[idx]
            if plan is None or plan.notes["editorial_score"] < min_score:
                continue
            signature = tuple((round(s.source_start / 5), round(s.source_end / 5)) for s in plan.segments)
            if signature in seen:
                continue
            seen.add(signature)
            candidates.append(plan)
            if len(candidates) >= 120:
                break
        all_candidates = candidates or all_candidates
        for plan in sorted(candidates, key=lambda p: p.notes["editorial_score"], reverse=True):
            if len(selected) >= required:
                break
            if any(plan is p for p in selected):
                continue
            too_close = False
            for prior in selected:
                if overlap_with(plan, prior) > max_overlap:
                    too_close = True
                    break
                pa, pb = set(plan.notes.get("topics", [])), set(prior.notes.get("topics", []))
                if topic_rule and pa and pb and len(pa & pb) / max(1, len(pa | pb)) > 0.80 and plan.narrative_type == prior.narrative_type:
                    too_close = True
                    break
            if not too_close:
                if level > 0:
                    plan.notes["relaxed_selection_level"] = level
                    plan.notes["review_note"] = "chosen after relaxing the selection gate; check it before posting"
                selected.append(plan)
        if len(selected) >= required:
            break
        log("editorial", f"only {len(selected)}/{required} stories at selection level {level + 1}; relaxing the gate")
    # Last resort: stories built ONLY from source time that no chosen story uses (so they are genuinely different).
    while len(selected) < required:
        covered = [(sg.source_start, sg.source_end) for p in selected for sg in p.segments]

        def is_free(w):
            ov = sum(max(0.0, min(w["source_end"], e) - max(w["source_start"], s)) for s, e in covered)
            return ov / max(w["duration"], 1e-6) <= 0.05
        free_idx = [i for i, w in enumerate(windows) if is_free(w)]
        if len(free_idx) < 2:
            log("editorial", f"no unused source material left ({len(free_idx)} free windows of {len(windows)})")
            break
        sub = [windows[i] for i in free_idx]
        sim_sub = sim[np.ix_(free_idx, free_idx)]
        best = None
        for k in sorted(range(len(sub)), key=lambda k: sub[k]["score"], reverse=True)[:80]:
            plan = candidate_story(k, sub, sim_sub, source)
            if plan is None or plan.duration < 25.0:
                continue
            if any(overlap_with(plan, prior) > 0.20 for prior in selected):
                continue
            if best is None or plan.notes["editorial_score"] > best.notes["editorial_score"]:
                best = plan
        if best is None:
            log("editorial", "unused source material is too thin to build another distinct story")
            break
        best.notes["relaxed_selection_level"] = 4
        best.notes["review_note"] = "built from leftover source material; check it before posting"
        selected.append(best)
    if len(selected) < required:
        log("editorial", f"DIAGNOSTIC: source {float(source.duration):.0f}s, {len(windows)} windows, {len(plan_cache)} anchors tried, "
                         f"{sum(1 for v in plan_cache.values() if v is not None)} produced a story, {len(selected)} selected")
    return selected, all_candidates


def plans_checkpoint_path():
    return os.path.join(SOURCE_DIR, "plans_checkpoint.json")


def plans_checkpoint_key():
    """Identity of everything that determines the plans; a mismatch invalidates the checkpoint."""
    return {"schema": 1, "source_id": SOURCE.video_id, "source_duration": round(float(SOURCE.duration), 2),
            "words": len(TRANSCRIPT.words), "sentences": len(TRANSCRIPT.sentences),
            "similarity_backend": SIMILARITY_BACKEND, "min_story_score": CFG["min_story_score"],
            "max_clip_duration": CFG["max_clip_duration"], "required_clip_count": CFG["required_clip_count"],
            "director": bool(GEMINI.enabled and CFG.get("use_gemini_director", True))}


def save_plans_checkpoint(plans):
    atomic_write_json(plans_checkpoint_path(), {"key": plans_checkpoint_key(), "plans": [p.to_dict() for p in plans]})


def load_plans_checkpoint():
    """Return validated ClipPlans saved for exactly this source/transcript/config, else None."""
    path = plans_checkpoint_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("key") != plans_checkpoint_key():
            return None
        plans = [ClipPlan(title=p["title"], narrative_type=p["narrative_type"],
                          segments=[Segment(**s) for s in p["segments"]],
                          target_duration=p.get("target_duration", 90.0), notes=p.get("notes", {}))
                 for p in data["plans"]]
        for plan in plans:
            plan.validate(SOURCE.duration)
        return plans
    except Exception as exc:
        log("editorial", f"plans checkpoint ignored: {type(exc).__name__}: {exc}")
        return None


PLANS = load_plans_checkpoint() if CFG.get("resume_plans", True) else None
if PLANS and len(PLANS) == CFG["required_clip_count"]:
    STORY_CANDIDATES = []
    log("editorial", "resumed five story plans from checkpoint (same source, transcript and config)")
else:
    PLANS = None
    # BRAIN: Gemini watches the video link and proposes clips; BODY: this notebook validates and executes them.
    _director_plans, DIRECTOR_INFO = gemini_director(globals().get("USER_INPUT", ""), SOURCE, TRANSCRIPT)
    log("director", f"Gemini director: {len(_director_plans)} validated clip(s)" if _director_plans else f"local selector in charge ({DIRECTOR_INFO['reason']})")
    for _t, _why in DIRECTOR_INFO.get("rejected", []):
        log("director", f"rejected '{_t}': {_why}")
    EDITORIAL_WINDOWS = make_editorial_windows(TRANSCRIPT, CONTENT_MAP)
    if len(EDITORIAL_WINDOWS) < CFG["required_clip_count"] and len(_director_plans) < CFG["required_clip_count"]:
        raise RuntimeError(f"Only {len(EDITORIAL_WINDOWS)} usable editorial windows were found; cannot responsibly produce five distinct clips")
    log("editorial", f"scored {len(EDITORIAL_WINDOWS)} whole-source candidate windows")
    if len(_director_plans) >= CFG["required_clip_count"]:
        PLANS, STORY_CANDIDATES = _director_plans[:CFG["required_clip_count"]], []
    else:
        _local_plans, STORY_CANDIDATES = [], []
        if len(EDITORIAL_WINDOWS) >= 1:
            SIM_MATRIX = similarity_matrix(EDITORIAL_WINDOWS, SENTENCE_VECTORS)
            _local_plans, STORY_CANDIDATES = select_five_distinct_stories(EDITORIAL_WINDOWS, SIM_MATRIX, SOURCE)
        PLANS = merge_director_with_local(_director_plans, _local_plans, CFG["required_clip_count"])
print("\nSELECTED STORY BLUEPRINTS")
for i, plan in enumerate(PLANS, 1):
    print(f"{i}. {plan.title[:70]} | {plan.duration:.1f}s | {len(plan.segments)} source interval(s) | score={plan.notes['editorial_score']} | by {plan.notes.get('decided_by', 'local_selector')}")
if len(PLANS) != CFG["required_clip_count"]:
    if CFG.get("allow_fewer_clips", True) and len(PLANS) >= int(CFG.get("min_clip_count", 3)):
        log("editorial", f"only {len(PLANS)} distinct stories qualified; continuing with {len(PLANS)} clips instead of manufacturing weak ones")
        CFG["required_clip_count"] = len(PLANS)
    else:
        raise RuntimeError(f"Only {len(PLANS)} distinct stories cleared the editorial selection gate. No final Drive upload will occur. This is preferable to manufacturing weak clips.")
atomic_write_json(os.path.join(REPORT_DIR, "editing_blueprints.json"), [p.to_dict() for p in PLANS])
save_plans_checkpoint(PLANS)
save_state("five_story_plans_ready", count=len(PLANS), candidate_count=len(STORY_CANDIDATES))
