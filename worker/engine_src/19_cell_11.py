# CELL 11 — Run five independent productions, repair once if needed, then deliver as one verified package

def _render_until_qa(plan, idx, clip_dir, final_path, music_info, broll, sfx_path, tag="a"):
    """Render with the retry ladder; on success the QA-passed file is at final_path. Raises if QA keeps failing."""
    crf = int(CFG["render_crf"])
    audio_ceiling = 0.80
    for attempt in range(CFG["qa_retry_count"] + 1):
        log("render", f"clip {idx}: render attempt {attempt+1}" + ("" if tag == "a" else " (after review fix)"))
        work_dir = os.path.join(clip_dir, f"work_{tag}{attempt+1}")
        render_info = render_plan(plan, SOURCE, TRANSCRIPT.words, music_info["path"], final_path, work_dir, broll, crf=crf, audio_ceiling=audio_ceiling, sfx_path=sfx_path)
        print("  framing:", [f.get("mode") + (" (uncertain)" if f.get("uncertain") else "") for f in render_info["framing"]])
        technical_checks = run_technical_qa(render_info["temporary_path"], plan, render_info["caption_events"])
        passed = all(c["passed"] for c in technical_checks)
        for check in technical_checks:
            if CFG.get("verbose_qa") or not check["passed"]:
                print(f"  {'PASS' if check['passed'] else 'FAIL'} {check['check']}: {check['detail']}")
        if passed and not CFG.get("verbose_qa"):
            print(f"  technical QA: all {len(technical_checks)} checks passed")
        if passed:
            os.replace(render_info["temporary_path"], final_path)
            return render_info, technical_checks
        try: os.remove(render_info["temporary_path"])
        except OSError: pass
        if attempt >= CFG["qa_retry_count"]:
            raise RuntimeError(f"Clip {idx} failed technical QA after retries; package upload is blocked")
        crf = min(23, crf + 2)
        audio_ceiling = max(0.50, audio_ceiling - 0.10)


def produce_five_videos():
    N = int(CFG["required_clip_count"])
    if len(PLANS) != N:
        raise RuntimeError(f"{N} qualified story plans are required before rendering (found {len(PLANS)})")
    results = []
    FINISH_LOG = finish_plans(PLANS, TRANSCRIPT, SOURCE)      # completeness gate + Gemini critic + filler/pause tightening
    for idx, plan in enumerate(PLANS, 1):
        log("production", f"START CLIP {idx}/{N} — {plan.title[:80]}")
        clip_dir = os.path.join(RENDER_DIR, f"clip_{idx:02d}")
        os.makedirs(clip_dir, exist_ok=True)
        final_name = f"{idx:02d}_{safe_filename(plan.title, 56)}.mp4"
        final_path = os.path.join(clip_dir, final_name)
        music_info = MUSIC_ASSETS[idx]
        broll = BROLL_ASSETS.get(idx)
        editorial_checks = run_editorial_qa(plan, SOURCE, TRANSCRIPT)
        if not all(c["passed"] for c in editorial_checks):
            raise RuntimeError(f"Editorial QA failed before render for clip {idx}: {editorial_checks}")

        sfx_path = build_sfx_track(plan, os.path.join(clip_dir, "sfx.wav")) if CFG.get("enable_sfx", True) else None
        if sfx_path:
            print(f"  sound effects: {[(round(t, 1), k, who) for t, k, who in plan_sfx_cues(plan)]}")
        for flag in editorial_checks:
            if flag.get("severity") == "warning" and flag.get("flagged"):
                print(f"  REVIEW clip {idx}: {flag['detail']}")

        def _build(plan_, tag):
            sfx = sfx_path if tag == "a" else (build_sfx_track(plan_, os.path.join(clip_dir, f"sfx_{tag}.wav")) if (CFG.get("enable_sfx", True) and not plan_.notes.get("no_sfx")) else None)
            return _render_until_qa(plan_, idx, clip_dir, final_path, music_info, None if plan_.notes.get("disable_broll") else broll, sfx, tag)

        render_info, technical_checks = _build(plan, "a")
        review = None
        if "run_final_review" in globals() and CFG.get("enable_final_review", True):
            rr = run_final_review(plan, idx, final_path, lambda p_: _build(p_, "fix"))
            review = rr.get("review")
            if rr.get("render_info"):
                render_info, technical_checks, editorial_checks = rr["render_info"], rr["technical_checks"], rr["editorial_checks"]
            if review:
                sc = review.get("score")
                print(f"  AI review: {review['verdict']}" + (f" | score {sc}" if sc is not None else "") + f" | {review['summary'][:110]}")

        if not os.path.isfile(final_path):
            raise RuntimeError(f"Clip {idx} final file is missing")
        cover_path = os.path.join(clip_dir, f"{idx:02d}_COVER.jpg")
        make_cover(final_path, plan.title, cover_path)
        final_info = ffprobe_json(final_path)
        duration = float(final_info["format"]["duration"])
        if duration > float(CFG["max_clip_duration"]) + 0.05:
            raise RuntimeError(f"Clip {idx} exceeds the {CFG['max_clip_duration']:.0f}-second ceiling")
        item = {
            "index": idx, "title": plan.title, "narrative_type": plan.narrative_type,
            "segments": [asdict(s) for s in plan.segments], "duration": duration,
            "editorial_score": plan.notes.get("editorial_score"),
            "score_disclaimer": plan.notes.get("score_type"),
            "music": music_info["asset"], "broll": broll,
            "framing": render_info["framing"], "broll_window": render_info["broll_window"],
            "technical_checks": technical_checks, "editorial_checks": editorial_checks,
            "technical_pass": all(c["passed"] for c in technical_checks),
            "editorial_pass": all(c["passed"] for c in editorial_checks),
            "review_flags": [c["detail"] for c in editorial_checks if c.get("severity") == "warning" and c.get("flagged")]
                            + ([f"AI reviewer: {review['verdict']} (score {review.get('score')}): {review['summary'][:140]}"]
                               if review and (review["verdict"] != "approve" or (review.get("score") is not None and review["score"] < float(CFG.get("review_min_score", 6.0)))) else [])
                            + ([f"Long clip ({duration:.0f}s): {plan.notes.get('long_form_reason', '')[:120]}"] if duration > float(CFG.get("long_clip_threshold_sec", 90.0)) + 0.05 else []),
            "review": review, "long_clip": duration > float(CFG.get("long_clip_threshold_sec", 90.0)) + 0.05,
            "finishing": {"log": plan.notes.get("finish_log", []), "tighten": plan.notes.get("tighten"), "critic": plan.notes.get("critic")},
            "final_path": final_path, "cover_path": cover_path,
            "sha256": sha256_file(final_path),
            "human_review_required": True,
        }
        results.append(item)
        log("production", f"CLIP {idx}/{N} PASS — {duration:.2f}s | {os.path.getsize(final_path)/1e6:.1f} MB")
        save_state("rendering", completed_clips=len(results), total_clips=N)

    if len(results) != N:
        raise RuntimeError(f"{N} completed outputs were not produced; no Drive upload will be attempted")
    if not all(x["technical_pass"] and x["editorial_pass"] for x in results):
        raise RuntimeError("One or more outputs failed the final QA gate")
    delivery_location = build_and_deliver_package(PLANS, results)
    save_state("delivered" if DRIVE_AVAILABLE else "local_outputs_verified", count=len(results), location=delivery_location)
    return results, delivery_location


CLIP_RESULTS, DELIVERY_LOCATION = produce_five_videos()
print("\nFINAL STATUS:", len(CLIP_RESULTS), "of", CFG["required_clip_count"], "video masters verified")
print("Output package:", DELIVERY_LOCATION)
