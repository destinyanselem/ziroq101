# CELL 9 — Dynamic framing, premium word-level ASS captions, mixed audio, rendering, and QA
import cv2
from PIL import Image, ImageDraw, ImageFont, ImageEnhance


def find_font_path():
    candidates = glob.glob("/usr/share/fonts/**/DejaVuSans-Bold.ttf", recursive=True)
    if not candidates:
        raise RuntimeError("DejaVu Sans Bold font is missing; run Cell 0")
    return candidates[0]


def escape_ass(text):
    return (text or "").replace("{", "(").replace("}", ")").replace("\\", "")


def ass_time(seconds):
    cs = int(round(max(0.0, seconds) * 100))
    return f"{cs//360000}:{(cs//6000)%60:02d}:{(cs//100)%60:02d}.{cs%100:02d}"


def build_ass(plan, words, ass_path):
    mapped = timeline_words(plan, words)
    if not mapped:
        raise RuntimeError("No source-timestamped words map to this story plan")
    groups, current = [], []
    def flush():
        nonlocal current
        if current:
            groups.append(current)
            current = []
    for item in mapped:
        if current:
            gap = item["start"] - current[-1]["end"]
            char_count = sum(len(x["word"]) + 1 for x in current) + len(item["word"])
            if item["segment_index"] != current[-1]["segment_index"] or gap > 0.55 or len(current) >= 4 or char_count > 26 or re.search(r"[.!?,;:]$", current[-1]["word"]):
                flush()
        current.append(item)
    flush()
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Cap,DejaVu Sans,68,&H0000D7FF,&H00FFFFFF,&H00101010,&HAA000000,-1,0,0,0,100,100,0,0,1,6,2,2,80,80,500,1\n"
        "Style: Hook,DejaVu Sans,56,&H00FFFFFF,&H00FFFFFF,&H00101010,&HAA000000,-1,0,0,0,100,100,0,0,1,5,2,8,90,90,160,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines, events = [], []
    # Title is explicitly a generated editorial headline, not spoken dialogue.
    title = escape_ass(plan.title.upper()[:72])
    lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(min(2.6, plan.duration))},Hook,,0,0,0,,{title}")
    for group in groups:
        chunks = []
        for idx, item in enumerate(group):
            next_start = group[idx+1]["start"] if idx + 1 < len(group) else item["end"]
            karaoke_cs = max(1, int(round((next_start - item["start"]) * 100)))
            chunks.append("{\\k" + str(karaoke_cs) + "}" + escape_ass(item["word"]) + (" " if idx + 1 < len(group) else ""))
        start = group[0]["start"]
        end = max(group[-1]["end"], start + 0.12)
        phrase = " ".join(x["word"] for x in group)
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Cap,,0,0,0,,{''.join(chunks)}")
        events.append({"start": start, "end": end, "text": phrase})
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines) + "\n")
    return events


def crop_geometry(sw, sh):
    target = CFG["out_w"] / CFG["out_h"]
    if sw / sh > target:
        ch = sh
        cw = int(round((sh * target) / 2) * 2)
    else:
        cw = sw
        ch = int(round((sw / target) / 2) * 2)
    cw, ch = min(cw, sw - sw % 2), min(ch, sh - sh % 2)
    return max(2, cw), max(2, ch)


FACE_CASCADE_NAME = "haarcascade_frontalface_default.xml"
_FACE_DETECTOR_STATE = {"resolved": False, "kind": None, "detector": None, "reason": ""}


def _load_cascade(path):
    """Return a loaded cv2.CascadeClassifier, or None if the file is missing/empty/unreadable."""
    try:
        if not path or not os.path.isfile(path):
            return None
        cascade = cv2.CascadeClassifier(path)
        return None if cascade.empty() else cascade
    except Exception:
        return None


def extract_member_from_wheel(wheel_path, member_suffix, dest_path):
    """Copy one file out of a wheel (a zip) without installing it; returns dest_path or None."""
    import zipfile
    with zipfile.ZipFile(wheel_path) as zf:
        matches = [n for n in zf.namelist() if n.endswith(member_suffix)]
        if not matches:
            return None
        data = zf.read(matches[0])
    if len(data) < 1000:
        return None
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    tmp = dest_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest_path)
    return dest_path


def recover_cascade_from_opencv4_wheel(dest_path):
    """OpenCV 5.x pip wheels ship an empty cv2/data folder. Download (not install) an OpenCV 4.x wheel
    and copy only the Haar cascade XML, so the NumPy/PyAV/OpenCV stack stays untouched."""
    tmp_dir = os.path.join(CACHE_ROOT, "_wheel_tmp")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    os.makedirs(tmp_dir, exist_ok=True)
    try:
        result = run([sys.executable, "-m", "pip", "download", "opencv-python-headless<5", "--no-deps",
                      "--only-binary=:all:", "-d", tmp_dir, "-q"], check=False, timeout=300, quiet=True)
        wheels = glob.glob(os.path.join(tmp_dir, "opencv_python_headless-*.whl"))
        if result.returncode != 0 or not wheels:
            return None
        return extract_member_from_wheel(wheels[0], "/" + FACE_CASCADE_NAME, dest_path)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def get_face_detector(force_reload=False):
    """Resolve a face detector once: bundled Haar -> cached/recovered Haar -> none (stated loudly)."""
    st = _FACE_DETECTOR_STATE
    if st["resolved"] and not force_reload:
        return st
    st.update({"resolved": True, "kind": None, "detector": None, "reason": ""})
    bundled = _load_cascade(os.path.join(cv2.data.haarcascades, FACE_CASCADE_NAME)) if hasattr(cv2, "data") else None
    if bundled is not None:
        st.update(kind="haar_bundled", detector=bundled)
    else:
        cached_path = os.path.join(CACHE_ROOT, "models", FACE_CASCADE_NAME)
        cached = _load_cascade(cached_path)
        if cached is None:
            log("framing", f"OpenCV {cv2.__version__} has no bundled face cascade; trying to recover one from an OpenCV 4.x wheel")
            try:
                cached = _load_cascade(recover_cascade_from_opencv4_wheel(cached_path))
            except Exception as exc:
                st["reason"] = f"recovery failed: {type(exc).__name__}"
        if cached is not None:
            st.update(kind="haar_recovered", detector=cached)
    if st["detector"] is None:
        st["reason"] = st["reason"] or "no face cascade bundled with OpenCV and recovery was not possible"
        log("framing", f"WARNING: no face detector available ({st['reason']}); shots keep the full frame "
                       f"(framing_uncertain_mode={CFG.get('framing_uncertain_mode')})")
    else:
        log("framing", f"face detector ready: {st['kind']}")
    return st


def _uncertain_framing(reason, samples=0, **extra):
    """Visual completeness beats a blind close-up: when unsure, keep more of the original frame."""
    mode = CFG.get("framing_uncertain_mode", "fit_blur")
    if mode not in ("fit_blur", "center_crop"):
        mode = "fit_blur"
    return {"mode": mode, "reason": f"{reason}; uncertain -> {mode}", "tracking_samples": samples,
            "uncertain": True, **extra}


def _detect_local_framing(source, segment, sample_step=0.8, detector=None):
    """Face-centre tracking heuristic; never claims speaker identity or voice diarization.
    `detector` is any object with detectMultiScale(gray, ...); default resolves via get_face_detector()."""
    cw, ch = crop_geometry(source.width, source.height)
    if source.width / source.height <= CFG["out_w"] / CFG["out_h"]:
        return {"mode": "fit_blur", "reason": "source is not wider than a 9:16 crop", "tracking_samples": 0}
    det = detector if detector is not None else get_face_detector()["detector"]
    if det is None:
        return _uncertain_framing("no face detector available")
    cap = None
    try:
        cap = cv2.VideoCapture(source.video_path)
        points, two_person_samples = [], 0
        t = 0.0
        while t < segment.duration:
            cap.set(cv2.CAP_PROP_POS_MSEC, (segment.source_start + t) * 1000)
            ok, frame = cap.read()
            if not ok:
                t += sample_step
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = det.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(36, 36))
            if len(faces) >= 2:
                centers = sorted([x + w/2 for x, y, w, h in faces], key=lambda x: x)
                if (centers[-1] - centers[0]) > source.width * 0.28:
                    two_person_samples += 1
            if len(faces):
                # Largest face is used as a framing target; it is not a verified active-speaker detector.
                x, y, w, h = max(faces, key=lambda z: z[2] * z[3])
                points.append((t, float((x + w/2) / source.width)))
            t += sample_step
        if two_person_samples >= 2:
            return {"mode": "fit_blur", "reason": "multiple separated faces detected; preserve the full composition",
                    "tracking_samples": len(points), "multi_person_samples": two_person_samples}
        if len(points) < 2:
            return _uncertain_framing("insufficient stable face detections (slides, screen share or no visible face?)", len(points))
        # Smooth tracked centre positions to avoid crop jitter.
        smoothed, alpha = [], 0.35
        state = points[0][1]
        for tt, xx in points:
            state = alpha * xx + (1-alpha) * state
            smoothed.append((tt, state))
        max_x = max(0, source.width - cw)
        keyframes = [(tt, max(0.0, min(float(max_x), cx * source.width - cw/2))) for tt, cx in smoothed]
        return {"mode": "face_track", "keyframes": keyframes, "tracking_samples": len(points),
                "reason": "largest detected face centre, smoothed over time"}
    except Exception as exc:
        return _uncertain_framing(f"tracking error {type(exc).__name__}")
    finally:
        if cap is not None:
            cap.release()


def framing_from_hint(source, segment, hint):
    """Turn Gemini's layout/speaker_x hint into a framing dict, or None if the hint cannot be trusted for this segment."""
    if not hint:
        return None
    layout = hint.get("layout")
    xs = hint.get("speakers_x") or []
    if layout == "multi_speaker" and len(xs) == 2 and CFG.get("enable_split_screen", True):
        w2 = int(round(source.height * 1080 / 960)); w2 -= w2 % 2
        if source.width >= w2 + 8 and (xs[1] - xs[0]) >= 0.18:
            xl = int(max(0, min(source.width - w2, xs[0] * source.width - w2 / 2)))
            xr = int(max(0, min(source.width - w2, xs[1] * source.width - w2 / 2)))
            if xr - xl >= 0.18 * source.width:
                return {"mode": "split_two", "crop_w": w2, "x_left": xl, "x_right": xr, "tracking_samples": 0, "decided_by": "gemini",
                        "reason": "Gemini: two speakers side by side -> stacked layout (not locally verified)"}
    if layout in ("multi_speaker", "slides_or_screen"):
        return {"mode": "fit_blur", "reason": f"Gemini layout '{layout}'; keep the full composition", "tracking_samples": 0, "decided_by": "gemini"}
    if layout != "single_speaker":
        return None
    cw, ch = crop_geometry(source.width, source.height)
    max_x = max(0, source.width - cw)
    rel = [(t - segment.source_start, x) for t, x in hint.get("points", []) if segment.source_start - 3.0 <= t <= segment.source_end + 3.0]
    if len(rel) < 2:
        return None
    if max(x for _, x in rel) - min(x for _, x in rel) > 0.45:     # implausible jumps -> do not trust
        return None
    state, kf = rel[0][1], []
    for tt, xx in rel:
        state = 0.5 * xx + 0.5 * state
        kf.append((max(0.0, tt), max(0.0, min(float(max_x), state * source.width - cw / 2))))
    return {"mode": "face_track", "keyframes": kf, "tracking_samples": len(kf),
            "reason": "Gemini speaker position samples (not locally verified)", "decided_by": "gemini"}


def detect_segment_framing(source, segment, sample_step=0.8, detector=None, hint=None):
    """Local face tracking first; Gemini's hint is used when local detection is unavailable/uncertain, or when Gemini says the shot is a
    multi-person / slides layout (where a close crop would cut content)."""
    if source.width / source.height <= CFG["out_w"] / CFG["out_h"]:
        return _detect_local_framing(source, segment, sample_step, detector)
    hinted = framing_from_hint(source, segment, hint)
    if hinted and hinted["mode"] in ("fit_blur", "split_two") and CFG.get("trust_gemini_layout", True):
        return hinted
    local = _detect_local_framing(source, segment, sample_step, detector)
    if local.get("uncertain") and hinted and CFG.get("use_gemini_framing_hint", True):
        return hinted
    return local


def ffmpeg_crop_x_expr(keyframes, max_x):
    if not keyframes:
        return str(int(max_x/2))
    # Piecewise-linear interpolation in segment-local seconds; commas are escaped for filtergraph parsing.
    expr = f"{keyframes[-1][1]:.2f}"
    for i in range(len(keyframes)-2, -1, -1):
        t0, x0 = keyframes[i]
        t1, x1 = keyframes[i+1]
        dt = max(0.001, t1-t0)
        linear = f"({x0:.2f}+({x1-x0:.2f})*(t-{t0:.3f})/{dt:.3f})"
        expr = f"if(lt(t\\,{t1:.3f})\\,{linear}\\,{expr})"
    t0, x0 = keyframes[0]
    expr = f"if(lt(t\\,{t0:.3f})\\,{x0:.2f}\\,{expr})"
    return f"max(0\\,min({max_x}\\,{expr}))"


def make_audio_mix_graph(music_input_index, total_duration, volume=0.18, ceiling=0.80, sfx_input_index=None):
    """Pure helper used by render_plan: dialogue-triggered sidechain ducking, optional SFX bed, safe final mix."""
    parts = [
        "[abase]asplit=2[speech][sidechain]",
        f"[{music_input_index}:a]aresample=48000,atrim=duration={total_duration:.3f},asetpts=PTS-STARTPTS,volume={volume:.3f}[music]",
        "[music][sidechain]sidechaincompress=threshold=0.035:ratio=8:attack=25:release=450[ducked]",
    ]
    mix_in, n = "[speech][ducked]", 2
    if sfx_input_index is not None:
        parts.append(f"[{sfx_input_index}:a]aresample=48000,atrim=duration={total_duration:.3f},asetpts=PTS-STARTPTS,volume=0.55[sfx]")
        mix_in, n = "[speech][ducked][sfx]", 3
    parts.append(f"{mix_in}amix=inputs={n}:duration=first:dropout_transition=2,highpass=f=65,loudnorm=I=-16:TP=-2.0:LRA=10,"
                 f"alimiter=limit={ceiling:.3f}:attack=3:release=40:level=disabled,aresample=48000[aout]")
    return parts


SFX_KINDS = ("whoosh", "impact", "pop", "riser")
SFX_SR = 48000


def synth_sfx(kind, seed=0):
    """Procedural sound effects (no samples, no licences). Normalised to a 0.6 peak; the mix level is set in the graph."""
    rng = np.random.default_rng(seed)
    sr = SFX_SR
    if kind == "whoosh":
        n = int(0.55 * sr); t = np.linspace(0, 1, n); noise = rng.standard_normal(n)
        low = np.convolve(noise, np.ones(15) / 15, mode="same")
        high = noise - np.convolve(noise, np.ones(61) / 61, mode="same")
        y = ((1 - t) * low + t * high) * np.sin(np.pi * t) ** 2
    elif kind == "impact":
        n = int(0.7 * sr); t = np.arange(n) / sr
        y = np.sin(2 * np.pi * (55 + 40 * np.exp(-t * 18)) * t) * np.exp(-t * 7) + 0.3 * rng.standard_normal(n) * np.exp(-t * 40)
    elif kind == "pop":
        n = int(0.12 * sr); t = np.arange(n) / sr
        y = np.sin(2 * np.pi * (700 + 500 * np.exp(-t * 60)) * t) * np.exp(-t * 45)
    else:  # riser
        n = int(1.2 * sr); t = np.linspace(0, 1, n)
        noise = np.convolve(rng.standard_normal(n), np.ones(9) / 9, mode="same")
        y = (noise * 0.6 + 0.4 * np.sin(2 * np.pi * (200 + 900 * t ** 2) * t)) * t ** 2
    return (y / max(1e-9, float(np.max(np.abs(y)))) * 0.6).astype(np.float32)


def plan_sfx_cues(plan):
    """Gemini's cue sheet (source time -> clip time), else a rule: a whoosh at each join between source intervals. Always validated."""
    total = plan.duration
    def to_clip(ts):
        off = 0.0
        for s in plan.segments:
            if s.source_start - 0.01 <= ts <= s.source_end:
                return off + (ts - s.source_start)
            off += s.duration
        return None
    cues = []
    for ts, kind in plan.notes.get("sfx_cues") or []:
        ct = to_clip(ts)
        if ct is not None and kind in SFX_KINDS:
            cues.append((ct, kind, "gemini"))
    if not cues and CFG.get("sfx_local_fallback", True):
        off = 0.0
        for s in plan.segments[:-1]:
            off += s.duration
            cues.append((off - 0.15, "whoosh", "local_rule"))
    out = []
    for c in sorted(x for x in cues if 0.5 <= x[0] <= total - 0.8):
        if not out or c[0] - out[-1][0] >= 2.0:
            out.append(c)
    return out[:int(CFG.get("sfx_max_cues", 5))]


def build_sfx_track(plan, out_path):
    """Write a stereo WAV with the clip's SFX at their cue times; returns the path, or None when there are no cues."""
    cues = plan_sfx_cues(plan)
    if not cues:
        return None
    sr = SFX_SR
    buf = np.zeros(int((plan.duration + 1.0) * sr), dtype=np.float32)
    for i, (t, kind, _who) in enumerate(cues):
        fx = synth_sfx(kind, seed=i + 1)
        start = t - (0.27 if kind == "whoosh" else (len(fx) / sr if kind == "riser" else 0.0))
        s0 = max(0, int(start * sr)); seg = fx[:max(0, len(buf) - s0)]
        buf[s0:s0 + len(seg)] += seg
    buf = np.clip(buf, -0.8, 0.8)
    stereo = np.repeat(buf[:, None], 2, axis=1)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with wave.open(out_path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((stereo * 32767).astype("<i2").tobytes())
    return out_path


def render_plan(plan, source, transcript_words, music_path, out_path, work_dir, broll_asset=None, crf=None, audio_ceiling=0.80, sfx_path=None):
    plan.validate(source.duration)
    total = plan.duration
    if total > CFG["max_clip_duration"] + 0.02:
        raise ValueError(f"Refusing to render a clip longer than {CFG['max_clip_duration']:.0f} seconds")
    os.makedirs(work_dir, exist_ok=True)
    ass_path = os.path.join(work_dir, "captions.ass")
    caption_events = build_ass(plan, transcript_words, ass_path)
    inputs, chains, concat_inputs, framing_reports = [], [], "", []
    for i, seg in enumerate(plan.segments):
        inputs += ["-ss", f"{seg.source_start:.3f}", "-t", f"{seg.duration:.3f}", "-i", source.video_path]
        framing = detect_segment_framing(source, seg, hint=plan.notes.get("gemini_framing"))
        framing_reports.append(framing)
        if framing["mode"] == "fit_blur":
            chains.append(f"[{i}:v]setpts=PTS-STARTPTS,fps={CFG['out_fps']},split=2[bg{i}][fg{i}]")
            chains.append(f"[bg{i}]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=22:4[blur{i}]")
            chains.append(f"[fg{i}]scale=1080:1920:force_original_aspect_ratio=decrease[fit{i}]")
            chains.append(f"[blur{i}][fit{i}]overlay=(W-w)/2:(H-h)/2,setsar=1[v{i}]")
        elif framing["mode"] == "split_two":
            w2, hh = framing["crop_w"], source.height
            chains.append(f"[{i}:v]setpts=PTS-STARTPTS,fps={CFG['out_fps']},split=2[sa{i}][sb{i}]")
            chains.append(f"[sa{i}]crop={w2}:{hh}:x={framing['x_left']}:y=0,scale=1080:960:flags=lanczos[ta{i}]")
            chains.append(f"[sb{i}]crop={w2}:{hh}:x={framing['x_right']}:y=0,scale=1080:960:flags=lanczos[tb{i}]")
            chains.append(f"[ta{i}][tb{i}]vstack=inputs=2,setsar=1[v{i}]")
        else:
            cw, ch = crop_geometry(source.width, source.height)
            max_x = max(0, source.width-cw)
            if framing["mode"] == "face_track":
                x_expr = ffmpeg_crop_x_expr(framing["keyframes"], max_x)
            else:
                x_expr = str(int(max_x/2))
            y = max(0, (source.height-ch)//2)
            zoom = ",scale=1166:2074:flags=lanczos,crop=1080:1920" if seg.role == "tight_zoom" else ""
            chains.append(f"[{i}:v]setpts=PTS-STARTPTS,fps={CFG['out_fps']},crop={cw}:{ch}:x='{x_expr}':y={y},scale=1080:1920:flags=lanczos{zoom},setsar=1[v{i}]")
        _fd = min(0.012, seg.duration / 4)     # 12 ms fades remove clicks at every cut
        chains.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS,afade=t=in:st=0:d={_fd:.3f},afade=t=out:st={max(0.0, seg.duration - _fd):.3f}:d={_fd:.3f}[a{i}]")
        concat_inputs += f"[v{i}][a{i}]"
    chains.insert(0, f"{concat_inputs}concat=n={len(plan.segments)}:v=1:a=1[vbase][abase]")
    music_index = len(plan.segments)
    inputs += ["-i", music_path]
    sfx_index = None
    if sfx_path and os.path.isfile(sfx_path):
        sfx_index = len(plan.segments) + 1
        inputs += ["-i", sfx_path]
    chains.extend(make_audio_mix_graph(music_index, total, volume=float(plan.notes.get("music_volume", 0.18)), ceiling=audio_ceiling, sfx_input_index=sfx_index))
    video_label = "vbase"
    broll_window = None
    if broll_asset and broll_asset.get("local_path") and os.path.isfile(broll_asset["local_path"]):
        broll_index = len(plan.segments) + 1 + (1 if sfx_index is not None else 0)
        # A single short contextual cutaway, not a random stock montage.
        broll_duration = min(4.0, max(2.5, total * 0.09))
        broll_start = min(max(5.0, total * 0.43), max(0.0, total - broll_duration - 1.0))
        broll_window = (broll_start, broll_start+broll_duration)
        inputs += ["-stream_loop", "-1", "-i", broll_asset["local_path"]]
        chains.append(f"[{broll_index}:v]trim=duration={broll_duration:.3f},setpts=PTS-STARTPTS+{broll_start:.3f}/TB,fps={CFG['out_fps']},scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[br]")
        chains.append(f"[vbase][br]overlay=0:0:enable='between(t\\,{broll_start:.3f}\\,{broll_start+broll_duration:.3f})':eof_action=pass[vbroll]")
        video_label = "vbroll"
    chains.append(f"[{video_label}]ass=captions.ass:fontsdir=/usr/share/fonts/truetype/dejavu,format=yuv420p,fade=t=out:st={max(0.0,total-0.25):.3f}:d=0.25[vout]")
    chains.append("[aout]afade=t=in:st=0:d=0.08,afade=t=out:st=" + f"{max(0.0,total-0.35):.3f}:d=0.35[aout_final]")
    tmp_path = out_path.replace(".mp4", ".rendering.mp4")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning"] + inputs + [
        "-filter_complex", ";".join(chains), "-map", "[vout]", "-map", "[aout_final]",
        "-t", f"{total:.3f}", "-r", str(CFG["out_fps"]),
        "-c:v", "libx264", "-preset", CFG["render_preset"], "-crf", str(crf if crf is not None else CFG["render_crf"]),
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", tmp_path,
    ]
    run(cmd, cwd=work_dir, timeout=1800)
    if not os.path.isfile(tmp_path) or os.path.getsize(tmp_path) == 0:
        raise RuntimeError("FFmpeg did not produce a nonempty render")
    return {"temporary_path": tmp_path, "caption_events": caption_events,
            "framing": framing_reports, "broll_window": broll_window,
            "actual_duration": total, "music_path": music_path}


def audio_metrics(path):
    # Peak is measured per channel at the native rate (never on a mono downmix, which adds +3 dB for identical channels).
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "2", "-ar", "48000", "-f", "f32le", "pipe:1"],
                          capture_output=True, text=False, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-1000:])
    stereo = np.frombuffer(proc.stdout, dtype=np.float32)
    stereo = stereo[: (stereo.size // 2) * 2].reshape(-1, 2)
    if stereo.size == 0:
        return {"rms": 0.0, "peak": 0.0}
    mono = stereo.mean(axis=1)
    return {"rms": float(np.sqrt(np.mean(mono * mono))), "peak": float(np.max(np.abs(stereo)))}


def measure_loudness(path):
    """Measure integrated loudness and true peak (dBTP) on the finished AAC master."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn",
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=10:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "FFmpeg loudness measurement failed")[-1200:])
    reports = re.findall(r"\{\s*\"input_i\".*?\}", proc.stderr or "", flags=re.S)
    if not reports:
        raise RuntimeError("FFmpeg did not return loudnorm measurement JSON")
    raw = json.loads(reports[-1])
    return {
        "integrated_lufs": float(raw["input_i"]),
        "true_peak_dbtp": float(raw["input_tp"]),
        "loudness_range_lu": float(raw.get("input_lra", "nan")),
        "normalization_type": raw.get("normalization_type", "unknown"),
    }


def run_technical_qa(path, plan, caption_events):
    checks = []
    def add(name, passed, detail):
        checks.append({"check": name, "passed": bool(passed), "detail": str(detail)})
    if not os.path.isfile(path) or os.path.getsize(path) <= 0:
        add("file_exists_nonzero", False, "missing or empty")
        return checks
    add("file_exists_nonzero", True, f"{os.path.getsize(path)/1e6:.1f} MB")
    info = ffprobe_json(path)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    duration = float(info["format"]["duration"])
    add("duration_within_limit", 0 < duration <= CFG["max_clip_duration"] + 0.05, f"{duration:.3f}s")
    add("duration_matches_timeline", abs(duration-plan.duration) <= 0.15, f"render={duration:.3f}s timeline={plan.duration:.3f}s")
    if video:
        add("resolution_1080x1920", (int(video["width"]), int(video["height"])) == (1080, 1920), f"{video['width']}x{video['height']}")
        rate = video.get("r_frame_rate", "0/1").split("/")
        fps = float(rate[0]) / max(float(rate[1]), 1.0)
        add("fps_30", abs(fps-30) < 0.02, f"{fps:.3f}")
        add("codec_h264", video.get("codec_name") == "h264", video.get("codec_name"))
    else:
        add("video_stream", False, "missing")
    if audio:
        add("codec_aac", audio.get("codec_name") == "aac", audio.get("codec_name"))
        if video and video.get("duration") and audio.get("duration"):
            av_delta = abs(float(video["duration"]) - float(audio["duration"]))
            add("audio_video_sync", av_delta <= 0.15, f"duration delta={av_delta:.3f}s")
    else:
        add("audio_stream", False, "missing")
    cap = cv2.VideoCapture(path)
    opened = cap.isOpened()
    add("opencv_decode", opened, "opened" if opened else "cannot decode")
    black_samples = 0
    if opened:
        for t in np.linspace(min(0.5, duration/4), max(0.5, duration-0.5), 8):
            cap.set(cv2.CAP_PROP_POS_MSEC, float(t)*1000)
            ok, frame = cap.read()
            if not ok:
                black_samples += 1
            else:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                if gray.mean() < 5.0 and gray.std() < 2.0:
                    black_samples += 1
        cap.release()
    add("no_black_or_unreadable_samples", black_samples == 0, f"{black_samples}/8 failed samples")
    add("caption_events_present", len(caption_events) > 0, f"{len(caption_events)} timed caption events")
    caption_hits = 0
    if opened and caption_events:
        for event in caption_events[:min(5, len(caption_events))]:
            cap = cv2.VideoCapture(path)
            cap.set(cv2.CAP_PROP_POS_MSEC, (event["start"] + min(0.12, (event["end"]-event["start"])/2)) * 1000)
            ok, frame = cap.read()
            cap.release()
            if ok:
                band = frame[int(frame.shape[0]*0.62):int(frame.shape[0]*0.88), :, :]
                b, g, r = band[:,:,0], band[:,:,1], band[:,:,2]
                yellow = ((r > 205) & (g > 125) & (g < 245) & (b < 100)).sum()
                if yellow > 35:
                    caption_hits += 1
    add("caption_pixels_visible", caption_hits >= (1 if caption_events else 0), f"{caption_hits}/{min(5, len(caption_events))} sampled caption frames")
    try:
        metrics = audio_metrics(path)
        add("audio_not_silent", metrics["rms"] > 0.003, f"RMS={metrics['rms']:.4f}")
        add("audio_peak_below_clipping", metrics["peak"] < 0.999, f"sample peak={metrics['peak']:.4f}")
        loudness = measure_loudness(path)
        add("integrated_loudness_measured", math.isfinite(loudness["integrated_lufs"]),
            f"{loudness['integrated_lufs']:.2f} LUFS")
        add("true_peak_below_minus_1dbtp", loudness["true_peak_dbtp"] <= -1.0,
            f"{loudness['true_peak_dbtp']:.2f} dBTP")
    except Exception as exc:
        add("audio_loudness_metrics", False, f"{type(exc).__name__}: {exc}")
    return checks


DANGLING_OPENERS = {"this", "that", "it", "they", "them", "those", "these", "he", "she", "so", "and", "but",
                    "because", "also", "which", "then", "its", "their"}


def _qa_tokens(text):
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def run_editorial_qa(plan, source, transcript=None):
    """Editorial gate. With `transcript`, quotation integrity and sentence-boundary checks are verified against
    the real transcript; warnings (severity="warning") never block but mark what a human must review."""
    plan.validate(source.duration)
    checks = []
    checks.append({"check": "single_central_idea", "passed": len(set(plan.notes.get("topics", []))) <= 4,
                   "detail": ", ".join(plan.notes.get("topics", [])) or "topic not classified"})
    if transcript is not None and transcript.words:
        starts = [w.start for w in transcript.words]
        worst, worst_seg = 1.0, None
        for i, seg in enumerate(plan.segments):
            lo = bisect.bisect_left(starts, seg.source_start - 0.5)
            hi = bisect.bisect_right(starts, seg.source_end + 0.5)
            rebuilt = [w.word for w in transcript.words[lo:hi]
                       if seg.source_start <= (w.start + w.end) / 2 < seg.source_end]
            a_tok, b_tok = set(_qa_tokens(" ".join(rebuilt))), set(_qa_tokens(seg.transcript))
            ratio = len(a_tok & b_tok) / max(1, len(a_tok | b_tok))
            if ratio < worst:
                worst, worst_seg = ratio, i
        checks.append({"check": "source_quotation_integrity", "passed": worst >= 0.90,
                       "detail": f"segment text matches the transcript words in its interval (worst overlap {worst:.2f}"
                                 + (f" at segment {worst_seg})" if worst_seg is not None else ")")})
        s_starts = sorted(s.start for s in transcript.sentences)
        s_ends = sorted(s.end for s in transcript.sentences)
        bad = []
        for i, seg in enumerate(plan.segments):
            d0 = min((abs(seg.source_start - x) for x in s_starts), default=99.0)
            d1 = min((abs(seg.source_end - x) for x in s_ends), default=99.0)
            # cuts made by tightening sit inside a sentence by design (in silence/filler); only the clip's real start/end must be on sentence edges
            if seg.role in ("tight_cont", "tight_zoom"):
                d0 = 0.0
            if i + 1 < len(plan.segments) and plan.segments[i + 1].role in ("tight_cont", "tight_zoom"):
                d1 = 0.0
            if d0 > 0.25 or d1 > 0.25:
                bad.append(f"seg{i}(start off {d0:.2f}s, end off {d1:.2f}s)")
        checks.append({"check": "segments_on_sentence_boundaries", "passed": not bad,
                       "detail": "every cut starts and ends on a sentence boundary" if not bad else "mid-sentence cut: " + ", ".join(bad)})
    else:
        checks.append({"check": "source_quotation_integrity", "passed": all(bool(s.transcript.strip()) for s in plan.segments),
                       "detail": "NOT verified against the transcript (no transcript supplied); only checks non-empty text"})
    checks.append({"check": "natural_duration", "passed": 8 <= plan.duration <= float(CFG["max_clip_duration"]),
                   "detail": f"{plan.duration:.1f}s actual assembled content; no cloned-frame padding"})
    checks.append({"check": "long_form_justified", "passed": plan.duration <= float(CFG.get("long_clip_threshold_sec", 90.0)) + 0.05 or bool(plan.notes.get("long_form_reason")),
                   "detail": ("normal length" if plan.duration <= float(CFG.get("long_clip_threshold_sec", 90.0)) + 0.05 else "long clip: " + str(plan.notes.get("long_form_reason") or "NO REASON RECORDED")[:140])})
    checks.append({"check": "five_video_diversity_metadata", "passed": bool(plan.notes.get("editorial_score")),
                   "detail": f"editorial heuristic score={plan.notes.get('editorial_score')}"})
    reasons = []
    order = [round(s.source_start) for s in plan.segments]
    if any(order[i] < order[i-1] for i in range(1, len(order))):
        reasons.append("clip opens with a later passage than some of the material that follows (source order: "
                       + " -> ".join(f"{x}s" for x in order) + ")")
    reasons += list(plan.notes.get("critic_flags", []))
    first = _qa_tokens(plan.segments[0].transcript)[:1]
    if first and first[0] in DANGLING_OPENERS:
        reasons.append(f"opening word '{first[0]}' may refer to something said earlier in the source")
    checks.append({"check": "context_review_flagged", "passed": True, "severity": "warning", "flagged": bool(reasons),
                   "detail": ("; ".join(reasons) + ". Review the preview before publishing.") if reasons else
                             "no automatic context warning; automatic checks cannot prove a joined story is fair, so still preview it"})
    return checks


def make_cover(video_path, title, out_path):
    frame_path = out_path + ".frame.jpg"
    run(["ffmpeg", "-y", "-ss", "1.0", "-i", video_path, "-frames:v", "1", "-q:v", "2", frame_path], quiet=True)
    image = Image.open(frame_path).convert("RGB").resize((1080, 1920))
    image = ImageEnhance.Brightness(image).enhance(0.72)
    overlay = Image.new("RGBA", image.size, (0,0,0,0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, 0, 1080, 1920), fill=(0,0,0,36))
    image = Image.alpha_composite(image.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(image)
    font_path = find_font_path()
    font = ImageFont.truetype(font_path, 84)
    words = title.split()
    lines, line = [], ""
    for word in words:
        attempt = (line + " " + word).strip()
        if draw.textbbox((0,0), attempt, font=font)[2] > 920 and line:
            lines.append(line); line = word
        else:
            line = attempt
    if line: lines.append(line)
    lines = lines[:4]
    y = 220
    draw.rounded_rectangle((56, 140, 1024, 740), radius=44, fill=(0,0,0,150), outline=(255,255,255,100), width=4)
    for line in lines:
        draw.text((104, y), line, font=font, fill=(255,255,255), stroke_width=4, stroke_fill=(0,0,0,220))
        y += 124
    image.convert("RGB").save(out_path, quality=92)
    try: os.remove(frame_path)
    except OSError: pass
    return out_path

log("render", "dynamic framing and complete render/QA functions defined")
