# CELL 5 — Full-source, checkpointed Faster-Whisper transcription
# One implementation only: audio chunks are decoded to NumPy arrays, avoiding the earlier WAV/PyAV failure path.
import traceback


def load_pcm16_wav(path):
    with wave.open(path, "rb") as wf:
        if wf.getframerate() != 16000 or wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise RuntimeError("Internal audio chunk must be mono 16 kHz PCM16")
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def extract_audio_chunk(source, start_sec, end_sec, out_path):
    if os.path.isfile(out_path) and os.path.getsize(out_path) > 44:
        return out_path
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    duration = max(0.1, end_sec - start_sec)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start_sec:.3f}",
           "-t", f"{duration:.3f}", "-i", source.video_path, "-vn", "-ac", "1", "-ar", "16000",
           "-c:a", "pcm_s16le", out_path]
    run(cmd, timeout=900, quiet=True)
    if not os.path.isfile(out_path) or os.path.getsize(out_path) <= 44:
        raise RuntimeError(f"Audio chunk extraction failed at {start_sec:.1f}s")
    return out_path


def load_whisper_model(device, model_name):
    from faster_whisper import WhisperModel
    compute = "float16" if device == "cuda" else "int8"
    log("transcribe", f"loading Faster-Whisper {model_name} on {device}/{compute}")
    return WhisperModel(model_name, device=device, compute_type=compute)


def transcribe_chunk(model, audio_array, offset_sec, keep_start, keep_end):
    segments, info = model.transcribe(
        audio_array,
        language=CFG["whisper_language"],
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 450, "speech_pad_ms": 150},
        beam_size=CFG["whisper_beam_size"],
        condition_on_previous_text=False,
    )
    result = []
    for segment in segments:
        for item in (segment.words or []):
            token = (item.word or "").strip()
            if not token:
                continue
            global_start = offset_sec + float(item.start)
            global_end = offset_sec + float(item.end)
            midpoint = (global_start + global_end) / 2
            # The overlapping edge audio provides context, but only the core interval is retained.
            if keep_start - 0.001 <= midpoint < keep_end - 0.001:
                result.append(Word(token, round(global_start, 3), round(global_end, 3), float(item.probability)))
    return result, getattr(info, "language", "unknown")


def transcribe_full_source(source, transcript_path):
    if os.path.isfile(transcript_path):
        try:
            cached = json.load(open(transcript_path, encoding="utf-8"))
            cached_words = [Word(**w) for w in cached["words"]]
            if cached.get("source_duration") and abs(cached["source_duration"] - source.duration) > 1.0:
                raise ValueError("Cached transcript belongs to a different source duration")
            log("transcribe", f"loaded completed full transcript ({len(cached_words)} words)")
            return Transcript(cached.get("language", "unknown"), cached_words,
                              [Sentence(**s) for s in cached.get("sentences", [])])
        except Exception as exc:
            log("transcribe", f"full transcript cache ignored: {exc}")

    chunk_dir = os.path.join(SOURCE_DIR, "transcript_chunks")
    os.makedirs(chunk_dir, exist_ok=True)
    chunk_sec = float(CFG["transcribe_chunk_sec"])
    overlap = float(CFG["transcribe_overlap_sec"])
    n_chunks = int(math.ceil(source.duration / chunk_sec))
    device = "cuda" if GPU_COUNT > 0 else "cpu"
    model_name = CFG["whisper_gpu_model"] if device == "cuda" else CFG["whisper_cpu_model"]
    try:
        model = load_whisper_model(device, model_name)
    except Exception as exc:
        if device != "cuda":
            raise
        log("transcribe", f"GPU model initialization failed; falling back to CPU ({type(exc).__name__})")
        device, model_name = "cpu", CFG["whisper_cpu_model"]
        model = load_whisper_model(device, model_name)

    all_words, languages = [], []
    for idx in range(n_chunks):
        core_start = idx * chunk_sec
        core_end = min(source.duration, (idx + 1) * chunk_sec)
        checkpoint = os.path.join(chunk_dir, f"chunk_{idx:05d}.json")
        if os.path.isfile(checkpoint):
            try:
                row = json.load(open(checkpoint, encoding="utf-8"))
                if abs(row.get("core_start", -999) - core_start) < 0.01 and abs(row.get("core_end", -999) - core_end) < 0.01:
                    words_here = [Word(**w) for w in row["words"]]
                    all_words.extend(words_here)
                    languages.append(row.get("language", "unknown"))
                    log("transcribe", f"chunk {idx+1}/{n_chunks}: resumed ({len(words_here)} words)")
                    continue
            except Exception:
                pass

        fetch_start = max(0.0, core_start - overlap)
        fetch_end = min(source.duration, core_end + overlap)
        wav_path = os.path.join(chunk_dir, f"audio_{idx:05d}.wav")
        extract_audio_chunk(source, fetch_start, fetch_end, wav_path)
        audio_array = load_pcm16_wav(wav_path)
        try:
            words_here, language = transcribe_chunk(model, audio_array, fetch_start, core_start, core_end)
        except Exception as exc:
            if device == "cuda":
                log("transcribe", f"chunk {idx+1}: CUDA failed, retrying this chunk on CPU: {type(exc).__name__}")
                model = load_whisper_model("cpu", CFG["whisper_cpu_model"])
                device, model_name = "cpu", CFG["whisper_cpu_model"]
                words_here, language = transcribe_chunk(model, audio_array, fetch_start, core_start, core_end)
            else:
                raise
        atomic_write_json(checkpoint, {
            "chunk_index": idx, "core_start": core_start, "core_end": core_end,
            "language": language, "device": device, "model": model_name,
            "words": [asdict(w) for w in words_here],
        })
        all_words.extend(words_here)
        languages.append(language)
        log("transcribe", f"chunk {idx+1}/{n_chunks} complete: {len(words_here)} words | {core_end/60:.1f}/{source.duration/60:.1f} min")
        save_state("transcribing", completed_chunks=idx+1, total_chunks=n_chunks, words=len(all_words), device=device, model=model_name)
        del audio_array

    all_words.sort(key=lambda w: (w.start, w.end))
    # Boundary duplicates are suppressed conservatively without discarding repeated words spoken at different times.
    deduped = []
    for word in all_words:
        if deduped and word.word.lower() == deduped[-1].word.lower() and abs(word.start - deduped[-1].start) < 0.12:
            if word.prob > deduped[-1].prob:
                deduped[-1] = word
        else:
            deduped.append(word)
    if not deduped:
        raise RuntimeError("No words were transcribed from the complete source")
    sentences = build_sentences(deduped)
    language = max(set(languages), key=languages.count) if languages else "unknown"
    transcript = Transcript(language, deduped, sentences)
    atomic_write_json(transcript_path, {
        "source_id": source.video_id, "source_duration": source.duration,
        "language": language, "words": [asdict(w) for w in deduped],
        "sentences": [asdict(s) for s in sentences],
    })
    return transcript


TRANSCRIPT = transcribe_full_source(SOURCE, TRANSCRIPT_PATH)
if not TRANSCRIPT.sentences:
    raise RuntimeError("Transcript contains no reconstructed sentences")
coverage = (max(w.end for w in TRANSCRIPT.words) / SOURCE.duration) if SOURCE.duration else 0.0
print(f"FULL TRANSCRIPT: {len(TRANSCRIPT.words):,} words | {len(TRANSCRIPT.sentences):,} sentences | language={TRANSCRIPT.language}")
print(f"Last transcribed word: {max(w.end for w in TRANSCRIPT.words)/60:.1f} min of {SOURCE.duration/60:.1f} min ({coverage:.1%} timeline coverage)")
save_state("transcribed", words=len(TRANSCRIPT.words), sentences=len(TRANSCRIPT.sentences), language=TRANSCRIPT.language)
