# CELL 1 — Configuration and runtime diagnostics
import os, re, sys, json, glob, math, time, uuid, csv, wave, hashlib, shutil, subprocess, textwrap, random, bisect, importlib.util
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any, Tuple

import numpy as np

CFG = {
    "work_dir": "/content/ziroq_work",
    "drive_mount": "/content/drive",
    "drive_root_name": "PROJECT_ZIROQ",
    "out_w": 1080, "out_h": 1920, "out_fps": 30,
    "max_clip_duration": 180.0,          # HARD ceiling for any clip. Normal clips stay <= 90 s; longer ones must earn it (see long_clip_*)
    "long_clip_threshold_sec": 90.0,       # above this a clip counts as LONG and needs a reason + the long-clip gate
    "local_clip_max_sec": 90.0,            # the local (no-Gemini) selector never builds clips longer than this
    "allow_long_clips": True,
    "director_long_max_clips": 2,          # at most this many long clips per video
    "enable_final_review": True,           # Gemini watches each finished clip; may request ONE bounded fix
    "review_max_fix_rounds": 1,
    "review_min_score": 6.0,
    "preferred_min_duration": 55.0,
    "transcribe_chunk_sec": 600.0,
    "transcribe_overlap_sec": 2.0,
    "whisper_gpu_model": "small",
    "whisper_cpu_model": "base",
    "whisper_beam_size": 3,
    "whisper_language": None,          # None = automatic detection
    "use_semantic_embeddings": True,
    "semantic_model_id": "sentence-transformers/all-MiniLM-L6-v2",
    "coverr_api_key": os.environ.get("COVERR_API_KEY", ""),
    "enable_coverr_assets": True,
    "pixabay_api_key": os.environ.get("PIXABAY_API_KEY", ""),
    "enable_internet_archive_music": True,
    "enable_pixabay_broll": True,
    "broll_cache_hours": 24,
    "broll_max_downloads_per_run": 5,
    "render_preset": "veryfast",
    "render_crf": 19,
    "qa_retry_count": 1,
    "min_story_score": 32.0,
    "required_clip_count": 5,
    "allow_fewer_clips": True,
    "run_self_tests": os.environ.get("ZIROQ_RUN_TESTS", "") == "1",   # developer tests; OFF for normal use (they can fail for unrelated reasons)
    "credit_source_in_description": True,   # adds "Source: <title> — <channel>" to the YouTube description
    "verbose_qa": False,                    # True prints every QA check; False prints only failures (kinder to phones)
    "download_zip_when_no_drive": True,              # if the video only supports 3-4 distinct stories, make those instead of stopping
    "min_clip_count": 3,
    "framing_uncertain_mode": "fit_blur",   # when subject detection is unavailable/unsure: "fit_blur" (keep full frame) or "center_crop" (old behaviour)
    "resume_plans": True,                  # reuse the five story plans for the same source/transcript/config
    # ---- Gemini (batch 2). Keys come from Colab Secrets GEMINI_API_KEY_1..N — never paste keys here.
    "zero_cost_mode": True,                # never selects a paid-only path; hard request caps below
    "external_ai_enabled": None,           # None = ask once (typed consent); True/False = decide in advance
    "gemini_keys_are_separate_projects": True,   # your keys come from different projects; secret GEMINI_KEY_PROJECTS can override
    "gemini_model_reasoning": ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"],  # tried in order; unavailable ones are skipped at runtime
    "gemini_model_fast": ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"],
    "gemini_max_requests_per_run": 60,     # hard cap across all keys (cost + quota guard)
    "gemini_max_attempts_per_call": 6,
    "gemini_max_wait_sec": 120,            # longest we wait for a quota window before using the local fallback
    "gemini_min_interval_sec": 4.0,        # pacing per project
    "gemini_request_timeout_sec": 110,
    "gemini_call_budget_sec": 120,          # HARD wall-clock cap for any one Gemini task (all retries/waits included); then the local fallback runs
    "gemini_breaker_sec": 600,              # after 2 timeouts in a row, skip Gemini for this long instead of retrying again and again
    "gemini_prompt_char_cap": 400000,
    "gemini_live_probe": False,             # a few tiny requests to report what your keys can really do
    "gemini_probe_agentic": True,
    "use_gemini_director": True,           # Gemini watches the YouTube link and proposes the clips + music direction
    "gemini_agentic_video": False,         # True = "agentic" video processing (deeper, slower; test it first)
    "director_min_clip_sec": 20.0,
    "use_gemini_framing_hint": True,      # when no local face detector works, use Gemini's speaker-position samples
    "trust_gemini_layout": True,
    "gemini_rerank": True,                # Gemini orders licensed music / B-roll search results by fit (licence checks still run after)
    "gemini_upload_local": True,          # local video files: upload a small proxy to Gemini (File API, auto-deleted after use)
    "enable_sfx": True, "sfx_max_cues": 5, "sfx_local_fallback": True,
    # ---- v4 batch 6: finishing studio
    "enable_completeness_gate": True,     # never end on an unfinished sentence / open on a dangling connector
    "enable_gemini_critic": True,          # one extra Gemini request reviews all five clips as a strict editor
    "enable_tighten": True,                # remove filler words and long pauses (cuts only in silence/filler)
    "tighten_pause_sec": 0.60, "tighten_max_fraction": 0.20, "tighten_fillers": True,
    "punch_in_zoom": True,                 # alternate a mild zoom on tightened cuts to hide the jump
    "enable_split_screen": True,           # two speakers side by side -> stacked layout (needs Gemini's speaker positions)
    "director_clip_sec": [30, 75],         # length Gemini aims for (normal clips; long clips need a reason, hard ceiling 180 s)          # Gemini saying "slides/multi-speaker" forces full-frame (fit_blur) layout
}

RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6]
os.makedirs(CFG["work_dir"], exist_ok=True)


def log(stage, message):
    print(f"[{time.strftime('%H:%M:%S')}] {stage:<18} {message}", flush=True)


def first_line(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return (p.stdout or p.stderr).strip().splitlines()[0]
    except Exception as exc:
        return f"unavailable ({type(exc).__name__})"

print("Run ID:", RUN_ID)
print("Python:", sys.version.split()[0])
print("FFmpeg:", first_line(["ffmpeg", "-version"]))
print("FFprobe:", first_line(["ffprobe", "-version"]))
try:
    import ctranslate2
    GPU_COUNT = ctranslate2.get_cuda_device_count()
except Exception:
    GPU_COUNT = 0
print("CTranslate2 CUDA devices:", GPU_COUNT)
try:
    import cv2
    print("OpenCV:", cv2.__version__)
except Exception as exc:
    print("OpenCV unavailable:", exc)

# Keep all outputs and resumable caches grouped by this run.
RUN_DIR = os.path.join(CFG["work_dir"], RUN_ID)
CACHE_ROOT = os.path.join(CFG["work_dir"], "cache")
ASSET_DIR = os.path.join(RUN_DIR, "assets")
RENDER_DIR = os.path.join(RUN_DIR, "render")
REPORT_DIR = os.path.join(RUN_DIR, "reports")
for _p in (RUN_DIR, CACHE_ROOT, ASSET_DIR, RENDER_DIR, REPORT_DIR):
    os.makedirs(_p, exist_ok=True)

print("Run workspace:", RUN_DIR)
