"""Runs the ZIROQ engine (the notebook cells, unchanged apart from exact, checked patches) as a headless job.

Patches (each must match exactly once or the run stops with a clear message):
  cell 1  - work dir from env, gemini live probe off, semantic embedding model off (saves download time), consent from settings
  cell 3  - Colab Drive mount replaced by a LOCAL STAGING ROOT. The engine builds the verified folder tree there
            (FINAL_VIDEOS, COVERS, PUBLISHING_PACK, REPORTS...) and the worker uploads that tree to the real Drive afterwards
  cell 4  - no input(); URL comes from ZIROQ_URL; yt-dlp gets cookies/JS-runtime options
  cell 5  - transcription seam: Groq Whisper first, local faster-whisper as fallback
  log/save_state are wrapped so progress is reported (throttled + scrubbed) without changing engine behaviour
"""
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import engine_loader as L                      # noqa: E402
from groq_asr import GroqClient, HybridASR, load_groq_keys   # noqa: E402
from progress import Progress, scrub, register_secrets_from_env   # noqa: E402
from ytdlp_opts import YOUTUBE_FORMAT_SELECTOR   # noqa: E402

CELL4_DRIVE_REPLACEMENT = '''# CELL 3 (worker) - local staging root instead of a Colab Drive mount
DRIVE_AVAILABLE = True
DRIVE_ROOT = os.environ.get("ZIROQ_STAGING_ROOT") or os.path.join(CFG["work_dir"], "staging")
os.makedirs(DRIVE_ROOT, exist_ok=True)
print("Delivery staging root:", DRIVE_ROOT)
'''

# Hybrid seam appended to cell 5 BEFORE the pipeline step at the bottom runs.
CELL5_SEAM = '''
_WORKER_ASR = {"hybrid": _WORKER_HYBRID_INSTANCE}


def load_whisper_model(device, model_name):
    hy = _WORKER_ASR["hybrid"]
    if hy is None:
        return _local_load_whisper_model(device, model_name)
    hy.local_loader = (lambda d=device, m=model_name: _local_load_whisper_model(d, m))
    return hy


def transcribe_chunk(model, audio_array, offset_sec, keep_start, keep_end):
    if not isinstance(model, _WORKER_HYBRID_TYPE):
        return _local_transcribe_chunk(model, audio_array, offset_sec, keep_start, keep_end)
    words_raw, language = model.words_for_chunk(audio_array, CFG["whisper_language"])
    if words_raw is None:
        log("transcribe", "Groq unavailable for this chunk; using local Whisper (" + (model.fallback_reasons[-1] if model.fallback_reasons else "no key") + ")")
        local = model.local_model()
        model.used["local"] += 1
        return _local_transcribe_chunk(local, audio_array, offset_sec, keep_start, keep_end)
    result = []
    for text, start, end, prob in words_raw:
        gs, ge = offset_sec + start, offset_sec + end
        mid = (gs + ge) / 2
        if keep_start - 0.001 <= mid < keep_end - 0.001:
            result.append(Word(text, round(gs, 3), round(ge, 3), float(prob)))
    return result, language

'''


def build_patches():
    cell1 = [
        ('"work_dir": "/content/ziroq_work",', '"work_dir": os.environ.get("ZIROQ_WORK_DIR", "/tmp/ziroq_work"),'),
    ]
    cell4 = [
        ('USER_INPUT = (os.environ.get("ZIROQ_URL", "").strip() or input("YouTube URL or local video path: ")).strip().strip("\\"\'")',
         'USER_INPUT = os.environ.get("ZIROQ_URL", "").strip().strip("\\"\'")'),
        ('with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True}) as ydl:',
         'with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True, "format": YOUTUBE_FORMAT_SELECTOR, "ignore_no_formats_error": True, **_WORKER_YDL_EXTRA()}) as ydl:'),
        ('"format": "bv*+ba/b",', '"format": YOUTUBE_FORMAT_SELECTOR,\n            "ignore_no_formats_error": True,\n            "verbose": True, "quiet": False, "no_warnings": False,'),
        ('"merge_output_format": "mp4", "retries": 5,\n        }',
         '"merge_output_format": "mp4", "retries": 5, **_WORKER_YDL_EXTRA(),\n        }'),
    ]

    def cell5(src):
        src = L.apply_replacements(src, [
            ("def load_whisper_model(device, model_name):", "def _local_load_whisper_model(device, model_name):"),
            ("def transcribe_chunk(model, audio_array, offset_sec, keep_start, keep_end):",
             "def _local_transcribe_chunk(model, audio_array, offset_sec, keep_start, keep_end):"),
            ("def transcribe_full_source(source, transcript_path):", CELL5_SEAM + "\ndef transcribe_full_source(source, transcript_path):"),
        ], "08_cell_5.py")
        return src

    return {"02_cell_1.py": cell1, "04_cell_3.py": lambda s: CELL4_DRIVE_REPLACEMENT,
            "07_cell_4.py": cell4, "08_cell_5.py": cell5}


def make_after_hooks(progress, cfg_overrides, asr_state):
    def after_cell1(ns):
        ns["CFG"].update(cfg_overrides)
        ns["YOUTUBE_FORMAT_SELECTOR"] = YOUTUBE_FORMAT_SELECTOR
        orig_log = ns["log"]

        def log(stage, message):
            orig_log(stage, scrub(message))
            progress.line(f"{stage:<14} {message}")
        ns["log"] = log
        ns["_WORKER_YDL_EXTRA"] = __import__("ytdlp_opts").ydl_extra_opts

    def after_cell2(ns):
        orig_state = ns["save_state"]

        def save_state(stage, **details):
            orig_state(stage, **details)
            progress.state(stage, **details)
        ns["save_state"] = save_state

    def after_cell10b(ns):
        orig_build = ns["build_publishing_packs"]

        def build_publishing_packs(plans):
            packs = orig_build(plans)
            ns["_LAST_PACKS"] = packs
            return packs
        ns["build_publishing_packs"] = build_publishing_packs

    return {"02_cell_1.py": after_cell1, "03_cell_2.py": after_cell2, "17_cell_10b.py": after_cell10b}


def run_job(url, work_dir, consent, progress, cfg_overrides=None, env=None):
    """Runs the full engine for one URL. Returns a dict: staging_root, delivery_dir, clips, asr_usage."""
    env = os.environ if env is None else env
    register_secrets_from_env(env)
    os.environ["ZIROQ_URL"] = url
    os.environ["ZIROQ_WORK_DIR"] = work_dir
    os.environ["ZIROQ_STAGING_ROOT"] = os.path.join(work_dir, "staging")
    os.environ["ZIROQ_GEMINI_CONSENT"] = "I AGREE" if consent else ""
    overrides = {
        "external_ai_enabled": True if consent else False,
        "gemini_live_probe": False,
        "use_semantic_embeddings": False,          # TF-IDF fallback; skips a model download
        "verbose_qa": False,
        "run_self_tests": False,
    }
    overrides.update(cfg_overrides or {})

    ns = {"__name__": "ziroq_engine"}
    asr = {"hybrid": HybridASR(GroqClient(load_groq_keys(env)) if load_groq_keys(env) else None, local_loader=lambda: None)}
    ns["_WORKER_HYBRID_TYPE"] = HybridASR
    hooks = make_after_hooks(progress, overrides, asr)

    patches = build_patches()
    # cell 5 defines its own _WORKER_ASR dict; re-point it to our hybrid right after cell 3b/before 5 executes
    original_cell5_patch = patches["08_cell_5.py"]

    def cell5_with_binding(src):
        return original_cell5_patch(src).replace('_WORKER_ASR = {"hybrid": _WORKER_HYBRID_INSTANCE}', '_WORKER_ASR = {"hybrid": _WORKER_HYBRID_INSTANCE}')
    patches["08_cell_5.py"] = cell5_with_binding
    ns["_WORKER_HYBRID_INSTANCE"] = asr["hybrid"]

    progress.state("initialized")
    L.load_full(ns, L.RUN_CELLS, patches=patches, after=hooks)

    results = ns.get("CLIP_RESULTS") or []
    return {
        "run_id": ns.get("RUN_ID"),
        "staging_root": ns["DRIVE_ROOT"],
        "delivery_dir": ns.get("DELIVERY_LOCATION"),
        "source_title": getattr(ns.get("SOURCE"), "title", ""),
        "source_video_id": getattr(ns.get("SOURCE"), "video_id", ""),
        "clips": results,
        "packs": ns.get("_LAST_PACKS") or {},
        "length_notes": {str(c["index"]): ns["length_notes"](c["duration"]) for c in results} if "length_notes" in ns else {},
        "asr_usage": dict(asr["hybrid"].used),
        "gemini_enabled": bool(getattr(ns.get("GEMINI"), "enabled", False)),
    }
