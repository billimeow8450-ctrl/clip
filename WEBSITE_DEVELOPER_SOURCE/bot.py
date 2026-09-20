#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import concurrent.futures
import html
import mimetypes
import json
import logging
import math
import os
import queue
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import tempfile
import traceback
import shlex
import urllib.error
import urllib.request
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote, urlsplit, urlunsplit

from telegram import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, download_range_func

# MEDIA_TRANSPORT_V1
from media_transport import (
    BrokerConfig as MediaBrokerConfig,
    FormatCandidate as MediaFormatCandidate,
    MediaTransportBroker,
    MediaTransportError,
    TransportCancelled,
)
try:
    from viralforge.agnes_provider import (
        available as agnes_available,
        chat_json as agnes_chat_json,
        test as agnes_test,
    )
except Exception:
    def agnes_available() -> bool:
        return False
    def agnes_chat_json(*args, **kwargs):
        raise RuntimeError("Optional Agnes provider is not installed")
    def agnes_test(*args, **kwargs):
        return {"ok": False, "reason": "Optional Agnes provider is not installed"}
from viral_research_engine import (
    collect_research as collect_viral_research,
    boost_segments_with_research,
)

# Agnes is intentionally limited to viral-moment selection only.
# All video editing, framing, split-screen decisions, rendering and QA are local.
install_viralforge_handlers = None
start_ai_backend_monitor = None
ai_service_gate = None
command_add_agnes_key = None
command_clear_agnes_key = None
command_agnes_status = None
command_agnes_test = None

try:
    from viralforge.telegram_integration import (
        install_viralforge_handlers,
        start_ai_backend_monitor,
        ai_service_gate,
        command_add_agnes_key,
        command_clear_agnes_key,
        command_agnes_status,
        command_agnes_test,
    )
except Exception:
    logging.exception("Agnes Telegram admin handlers failed to import")

# External AI is never allowed to control editing.
viralforge_deep_rank = None
viralforge_qa_render = None
viralforge_store_prediction = None
viralforge_plan_clip_edit = None
VIRALFORGE_AVAILABLE = True


# =============================================================================
# Configuration
# =============================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")

LOCAL_BOT_API_URL = os.getenv(
    "LOCAL_BOT_API_URL", "http://127.0.0.1:8081"
).rstrip("/")

OWNER_ID = int(os.getenv("OWNER_ID", "1724773970"))
OWNER_USERNAME = (
    os.getenv("OWNER_USERNAME", "roshan12221")
    .strip()
    .lstrip("@")
    .lower()
)
OWNER_DISPLAY_NAME = os.getenv("OWNER_DISPLAY_NAME", "Roshan").strip() or "Roshan"

PERSONAL_EDITING_STUDIO = os.getenv("PERSONAL_EDITING_STUDIO","1").strip().lower() not in {"0","false","no","off"}
EDITING_STUDIO_NAME = os.getenv("EDITING_STUDIO_NAME","Roshan Editing Studio").strip() or "Roshan Editing Studio"

# Research-first Auto Clipper
VIRALFORGE_DEEP_ENABLED = os.getenv("VIRALFORGE_DEEP_ENABLED", "1").strip().lower() not in {"0","false","no","off"}
VIRALFORGE_DEEP_FINAL_CLIPS = max(1, min(10, int(os.getenv("VIRALFORGE_DEEP_FINAL_CLIPS", "10"))))
VIRALFORGE_ELITE_PICKS = max(1, min(3, int(os.getenv("VIRALFORGE_ELITE_PICKS", "3"))))
VIRALFORGE_FIRST5_SECONDS = max(3.0, min(5.0, float(os.getenv("VIRALFORGE_FIRST5_SECONDS", "5.0"))))

# Paid feature access and support
FREE_TRIAL_USES_PER_FEATURE = max(0, int(os.getenv("FREE_TRIAL_USES_PER_FEATURE", "3")))
SUPPORT_AUTO_REPLY = os.getenv("SUPPORT_AUTO_REPLY", "1").strip().lower() not in {"0", "false", "no", "off"}
SUPPORT_MAX_MESSAGE_CHARS = max(200, int(os.getenv("SUPPORT_MAX_MESSAGE_CHARS", "3000")))

# Telegram Bot API bots can receive/send at most about 2000 MB. Keep the
# application ceiling explicit instead of advertising an unsupported 4 GB.
TELEGRAM_BOT_API_HARD_LIMIT_BYTES = 2_000_000_000

TELEGRAM_MAX_BYTES = min(
    TELEGRAM_BOT_API_HARD_LIMIT_BYTES,
    int(os.getenv("TELEGRAM_MAX_BYTES", "1990000000")),
)
MAX_ENHANCE_INPUT_BYTES = int(
    os.getenv("MAX_ENHANCE_INPUT_BYTES", str(750 * 1024 * 1024))
)
MAX_FAST_ENHANCE_SECONDS = int(
    os.getenv("MAX_FAST_ENHANCE_SECONDS", "1800")
)
MAX_TRANSCRIPT_SECONDS = int(
    os.getenv("MAX_TRANSCRIPT_SECONDS", "7200")
)
TRANSCRIPT_PARAGRAPH_MIN_SECONDS = max(
    6.0, float(os.getenv("TRANSCRIPT_PARAGRAPH_MIN_SECONDS", "12"))
)
TRANSCRIPT_PARAGRAPH_TARGET_SECONDS = max(
    15.0, float(os.getenv("TRANSCRIPT_PARAGRAPH_TARGET_SECONDS", "38"))
)
TRANSCRIPT_PARAGRAPH_MAX_SECONDS = max(
    45.0, float(os.getenv("TRANSCRIPT_PARAGRAPH_MAX_SECONDS", "95"))
)
TRANSCRIPT_PARAGRAPH_MAX_CHARS = max(
    500, int(os.getenv("TRANSCRIPT_PARAGRAPH_MAX_CHARS", "1400"))
)
TRANSCRIPT_PAUSE_BREAK_SECONDS = max(
    0.7, float(os.getenv("TRANSCRIPT_PAUSE_BREAK_SECONDS", "1.15"))
)

MAX_CONCURRENT_DOWNLOADS = max(
    1, int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))
)
MAX_CONCURRENT_EXTRACTS = max(
    1, int(os.getenv("MAX_CONCURRENT_EXTRACTS", "5"))
)
MAX_CONCURRENT_UPLOADS = max(
    1, int(os.getenv("MAX_CONCURRENT_UPLOADS", "3"))
)
MAX_CONCURRENT_TRANSCRIPTS = max(
    1, int(os.getenv("MAX_CONCURRENT_TRANSCRIPTS", "2"))
)
MAX_CONCURRENT_FAST_ENHANCE = max(
    1, int(os.getenv("MAX_CONCURRENT_FAST_ENHANCE", "1"))
)
MAX_CONCURRENT_AI_ENHANCE = max(
    1, int(os.getenv("MAX_CONCURRENT_AI_ENHANCE", "1"))
)
MAX_AI_ENHANCE_SECONDS = max(
    180, int(os.getenv("MAX_AI_ENHANCE_SECONDS", "600"))
)
MAX_AI_ENHANCE_WALLCLOCK_SECONDS = max(
    1200, int(os.getenv("MAX_AI_ENHANCE_WALLCLOCK_SECONDS", "1200"))
)
AI_SUBPROCESS_TIMEOUT_SECONDS = max(
    60, int(os.getenv("AI_SUBPROCESS_TIMEOUT_SECONDS", "300"))
)
CPU_AI_STDIN_WRITE_TIMEOUT_SECONDS = max(
    5, int(os.getenv("CPU_AI_STDIN_WRITE_TIMEOUT_SECONDS", "30"))
)
AI_TARGET_SHORT_SIDE = max(
    720, int(os.getenv("AI_TARGET_SHORT_SIDE", "1080"))
)
AI_UPSCALE_SCALE = min(
    4, max(2, int(os.getenv("AI_UPSCALE_SCALE", "2")))
)
AI_ENHANCE_FALLBACK = os.getenv(
    "AI_ENHANCE_FALLBACK", "1"
).strip().lower() not in {"0", "false", "no", "off"}
REALESRGAN_BIN = os.getenv(
    "REALESRGAN_BIN", "/usr/local/bin/realesrgan-ncnn-vulkan"
).strip()
REALESRGAN_MODELS_DIR = os.getenv(
    "REALESRGAN_MODELS_DIR",
    "/usr/local/share/realesrgan-ncnn-vulkan/models",
).strip()
REALESRGAN_MODEL_NAME = os.getenv(
    "REALESRGAN_MODEL_NAME", "realesrgan-x4plus"
).strip() or "realesrgan-x4plus"
REALESRGAN_TILE = max(
    0, int(os.getenv("REALESRGAN_TILE", "0"))
)
AI_PREFLIGHT_TIMEOUT_SECONDS = max(
    10, int(os.getenv("AI_PREFLIGHT_TIMEOUT_SECONDS", "30"))
)
AI_PREFLIGHT_CACHE_SECONDS = max(
    30, int(os.getenv("AI_PREFLIGHT_CACHE_SECONDS", "300"))
)
ENHANCE_PROGRESS_INTERVAL_SECONDS = max(
    5, int(os.getenv("ENHANCE_PROGRESS_INTERVAL_SECONDS", "15"))
)
CPU_AI_MODEL_PATH = os.getenv(
    "CPU_AI_MODEL_PATH",
    "/home/administrator/ytdlbot/models/FSRCNN_x2.pb",
).strip()
CPU_AI_QUALITY_MODEL_PATH = os.getenv(
    "CPU_AI_QUALITY_MODEL_PATH",
    "/home/administrator/ytdlbot/models/EDSR_x2.pb",
).strip()
CPU_AI_QUALITY_MAX_SECONDS = max(
    3, int(os.getenv("CPU_AI_QUALITY_MAX_SECONDS", "90"))
)
CPU_AI_PREP_SHORT_SIDE = max(
    240, min(720, int(os.getenv("CPU_AI_PREP_SHORT_SIDE", "360")))
)
MAX_ACTIVE_JOBS_PER_USER = max(
    1, int(os.getenv("MAX_ACTIVE_JOBS_PER_USER", "3"))
)
ADMIN_UNLIMITED_JOBS = os.getenv(
    "ADMIN_UNLIMITED_JOBS", "1"
).strip().lower() not in {"0", "false", "no", "off"}
ADMIN_DASHBOARD_MAX_ROWS = max(
    10, int(os.getenv("ADMIN_DASHBOARD_MAX_ROWS", "40"))
)

REQUEST_COOLDOWN_SECONDS = max(
    0.0, float(os.getenv("REQUEST_COOLDOWN_SECONDS", "2"))
)
FILE_CLEANUP_MINUTES = max(
    1, int(os.getenv("FILE_CLEANUP_MINUTES", "15"))
)
YTDLP_CONCURRENT_FRAGMENTS = min(
    16, max(1, int(os.getenv("YTDLP_CONCURRENT_FRAGMENTS", "8")))
)
YTDLP_HTTP_CHUNK_SIZE = max(
    0, int(os.getenv("YTDLP_HTTP_CHUNK_SIZE", "10485760"))
)
YTDLP_BUFFER_SIZE = max(
    1024, int(os.getenv("YTDLP_BUFFER_SIZE", "1048576"))
)
YTDLP_FORCE_IPV4 = os.getenv(
    "YTDLP_FORCE_IPV4", "1"
).strip().lower() not in {"0", "false", "no", "off"}
YTDLP_THROTTLED_RATE = max(
    0, int(os.getenv("YTDLP_THROTTLED_RATE", "524288"))
)
YTDLP_USE_ARIA2 = os.getenv(
    "YTDLP_USE_ARIA2", "1"
).strip().lower() not in {"0", "false", "no", "off"}
YTDLP_ARIA2_CONNECTIONS = min(
    32, max(2, int(os.getenv("YTDLP_ARIA2_CONNECTIONS", "16")))
)
YTDLP_ENABLE_EJS = os.getenv(
    "YTDLP_ENABLE_EJS", "1"
).strip().lower() not in {"0", "false", "no", "off"}
YTDLP_PUBLIC_DIRECT_FIRST = os.getenv(
    "YTDLP_PUBLIC_DIRECT_FIRST", "1"
).strip().lower() not in {"0", "false", "no", "off"}
FFMPEG_THREADS = max(1, int(os.getenv("FFMPEG_THREADS", "2")))
WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL", "distil-large-v3").strip() or "distil-large-v3"
WHISPER_CPU_THREADS = max(
    1, int(os.getenv("WHISPER_CPU_THREADS", "4"))
)
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu").strip() or "cpu"
WHISPER_COMPUTE_TYPE = os.getenv(
    "WHISPER_COMPUTE_TYPE", "int8"
).strip() or "int8"
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "en").strip() or "en"
TRANSCRIPT_CAPTIONS_FIRST = os.getenv(
    "TRANSCRIPT_CAPTIONS_FIRST", "1"
).strip().lower() not in {"0", "false", "no", "off"}
TRANSCRIPT_ALLOW_AUTO_CAPTIONS = os.getenv(
    "TRANSCRIPT_ALLOW_AUTO_CAPTIONS", "1"
).strip().lower() not in {"0", "false", "no", "off"}
TRANSLATION_RETRIES = max(
    1, int(os.getenv("TRANSLATION_RETRIES", "3"))
)
TRANSLATION_CHUNK_CHARS = max(
    400, int(os.getenv("TRANSLATION_CHUNK_CHARS", "900"))
)
TRANSLATION_BATCH_SIZE = max(
    4, int(os.getenv("TRANSLATION_BATCH_SIZE", "18"))
)
TRANSLATION_WORKERS = min(
    4, max(1, int(os.getenv("TRANSLATION_WORKERS", "3")))
)
TRANSCRIPT_CACHE_TTL_SECONDS = max(
    300, int(os.getenv("TRANSCRIPT_CACHE_TTL_SECONDS", "3600"))
)
CANCEL_POLL_SECONDS = max(
    0.15, float(os.getenv("CANCEL_POLL_SECONDS", "0.35"))
)
PROXY_FAIL_LIMIT = max(
    1, int(os.getenv("PROXY_FAIL_LIMIT", "3"))
)
PROXY_DISABLE_MINUTES = max(
    1, int(os.getenv("PROXY_DISABLE_MINUTES", "15"))
)
MAX_PROXIES = max(
    1, int(os.getenv("MAX_PROXIES", "100"))
)

# Adaptive upscale / post-delivery upscale.
UPSCALE_RESULT_TTL_SECONDS = max(
    900, int(os.getenv("UPSCALE_RESULT_TTL_SECONDS", "3600"))
)
CPU_AI_MAX_SAFE_SHORT_SIDE = max(
    360, int(os.getenv("CPU_AI_MAX_SAFE_SHORT_SIDE", "1080"))
)
CPU_AI_MAX_SAFE_SECONDS = max(
    8, int(os.getenv("CPU_AI_MAX_SAFE_SECONDS", "300"))
)
USER_UPSCALE_MAX_SHORT_SIDE = max(
    1080, min(2160, int(os.getenv("USER_UPSCALE_MAX_SHORT_SIDE", "1440")))
)
PROXY_ATTEMPT_LIMIT = min(
    20, max(1, int(os.getenv("PROXY_ATTEMPT_LIMIT", "6")))
)
PROXY_BENCHMARK_TTL_SECONDS = max(
    60, int(os.getenv("PROXY_BENCHMARK_TTL_SECONDS", "900"))
)
PROXY_BENCHMARK_TIMEOUT_SECONDS = max(
    1.0, float(os.getenv("PROXY_BENCHMARK_TIMEOUT_SECONDS", "4.5"))
)
PROXY_BENCHMARK_WORKERS = min(
    16, max(1, int(os.getenv("PROXY_BENCHMARK_WORKERS", "8")))
)
PROXY_BENCHMARK_MAX = min(
    MAX_PROXIES, max(1, int(os.getenv("PROXY_BENCHMARK_MAX", "24")))
)
PROXY_BENCHMARK_URL = os.getenv(
    "PROXY_BENCHMARK_URL", "https://www.youtube.com/generate_204"
).strip() or "https://www.youtube.com/generate_204"
DIRECT_DOWNLOAD_FIRST = os.getenv(
    "DIRECT_DOWNLOAD_FIRST", "1"
).strip().lower() not in {"0", "false", "no", "off"}
DIRECT_SLOW_FALLBACK_BPS = max(
    0, int(os.getenv("DIRECT_SLOW_FALLBACK_BPS", "1048576"))
)
DIRECT_SLOW_GRACE_SECONDS = max(
    5.0, float(os.getenv("DIRECT_SLOW_GRACE_SECONDS", "15"))
)
DIRECT_SLOW_CONFIRM_SECONDS = max(
    2.0, float(os.getenv("DIRECT_SLOW_CONFIRM_SECONDS", "6"))
)
DIRECT_SLOW_MIN_BYTES = max(
    1024 * 1024, int(os.getenv("DIRECT_SLOW_MIN_BYTES", "3145728"))
)

# =============================================================================
# Auto Clipper Pro configuration
# =============================================================================

MAX_CONCURRENT_AUTO_CLIPPER = max(
    1, int(os.getenv("MAX_CONCURRENT_AUTO_CLIPPER", "2"))
)
# Multiple jobs may stay active, but heavy local renders are queued
# by default so two simultaneous encodes cannot starve a small VPS and hit the
# guardian timeout. Strong VPSes can explicitly set this to 2.
LOCAL_EDITOR_RENDER_SLOTS = min(
    2, max(1, int(os.getenv("LOCAL_EDITOR_RENDER_SLOTS", "1")))
)
AUTO_CLIPPER_MAX_SEGMENTS = min(10, max(1, int(os.getenv("AUTO_CLIPPER_MAX_SEGMENTS", "10"))))
AUTO_CLIPPER_ALL_MAX_SEGMENTS = min(10, max(1, int(os.getenv("AUTO_CLIPPER_ALL_MAX_SEGMENTS", str(AUTO_CLIPPER_MAX_SEGMENTS)))))
AUTO_CLIPPER_MIN_VIRAL_SCORE = max(1, min(100, int(os.getenv("AUTO_CLIPPER_MIN_VIRAL_SCORE", "60"))))
AUTO_CLIPPER_MAX_WALLCLOCK_SECONDS = max(
    300, int(os.getenv("AUTO_CLIPPER_MAX_WALLCLOCK_SECONDS", "7200"))
)
AUTO_CLIPPER_FFMPEG_TIMEOUT_SECONDS = max(
    120, int(os.getenv("AUTO_CLIPPER_FFMPEG_TIMEOUT_SECONDS", "900"))
)
AUTO_CLIPPER_RENDER_TIMEOUT_SECONDS = max(
    180, int(os.getenv("AUTO_CLIPPER_RENDER_TIMEOUT_SECONDS", "1200"))
)
AUTO_CLIPPER_PROGRESS_INTERVAL_SECONDS = max(
    5, int(os.getenv("AUTO_CLIPPER_PROGRESS_INTERVAL_SECONDS", "15"))
)
AUTO_CLIPPER_FACE_SAMPLES = min(
    30, max(6, int(os.getenv("AUTO_CLIPPER_FACE_SAMPLES", "12")))
)
# Keep Auto Clipper deterministic and fast; post-delivery upscale remains separate.
AUTO_CLIPPER_AI_UPSCALE_LOW_RES = False
AUTO_CLIPPER_AI_UPSCALE_MAX_SECONDS = max(
    10, int(os.getenv("AUTO_CLIPPER_AI_UPSCALE_MAX_SECONDS", "30"))
)
AUTO_CLIPPER_SOURCE_HEIGHT = max(
    720, int(os.getenv("AUTO_CLIPPER_SOURCE_HEIGHT", "1080"))
)
AUTO_CLIPPER_LLM_API_URL = os.getenv(
    "AUTO_CLIPPER_LLM_API_URL", ""
).strip()
AUTO_CLIPPER_LLM_API_KEY = os.getenv(
    "AUTO_CLIPPER_LLM_API_KEY", ""
).strip()
AUTO_CLIPPER_LLM_MODEL = os.getenv(
    "AUTO_CLIPPER_LLM_MODEL", ""
).strip()
AUTO_CLIPPER_LLM_TIMEOUT_SECONDS = max(
    10, int(os.getenv("AUTO_CLIPPER_LLM_TIMEOUT_SECONDS", "60"))
)
AUTO_CLIPPER_LLM_CHUNK_CHARS = max(
    4000, int(os.getenv("AUTO_CLIPPER_LLM_CHUNK_CHARS", "18000"))
)
# Agnes is reserved for viral selection. Translation never calls an AI model.
TRANSLATION_USE_LLM = False
TRANSLATION_LLM_API_URL = os.getenv(
    "TRANSLATION_LLM_API_URL", AUTO_CLIPPER_LLM_API_URL
).strip()
TRANSLATION_LLM_API_KEY = os.getenv(
    "TRANSLATION_LLM_API_KEY", AUTO_CLIPPER_LLM_API_KEY
).strip()
TRANSLATION_LLM_MODEL = os.getenv(
    "TRANSLATION_LLM_MODEL", AUTO_CLIPPER_LLM_MODEL
).strip()
TRANSLATION_LLM_TIMEOUT_SECONDS = max(
    10, int(os.getenv("TRANSLATION_LLM_TIMEOUT_SECONDS", "75"))
)
AUTO_CLIPPER_FACE_TRACK_INTERVAL_SECONDS = max(
    0.4, float(os.getenv("AUTO_CLIPPER_FACE_TRACK_INTERVAL_SECONDS", "0.8"))
)
AUTO_CLIPPER_FACE_TRACK_MAX_POINTS = min(
    60, max(8, int(os.getenv("AUTO_CLIPPER_FACE_TRACK_MAX_POINTS", "36")))
)
AUTO_CLIPPER_FACE_SMOOTHING = min(
    0.95, max(0.05, float(os.getenv("AUTO_CLIPPER_FACE_SMOOTHING", "0.72")))
)
AUTO_CLIPPER_DUPLICATE_OVERLAP = min(
    0.95, max(0.50, float(os.getenv("AUTO_CLIPPER_DUPLICATE_OVERLAP", "0.72")))
)
AUTO_CLIPPER_WORD_TRANSCRIBE_TIMEOUT_SECONDS = max(
    120, int(os.getenv("AUTO_CLIPPER_WORD_TRANSCRIBE_TIMEOUT_SECONDS", "1800"))
)
AUTO_CLIPPER_WORD_AUDIO_TIMEOUT_SECONDS = max(
    60, int(os.getenv("AUTO_CLIPPER_WORD_AUDIO_TIMEOUT_SECONDS", "180"))
)

# =============================================================================
# Smart Reframe V2 + AI Video Editor configuration
# =============================================================================
EDITOR_PATCH_VERSION = "2026-08-17-local-editor-v8.25-secondbot-lock-colours-responsive-body"

MAX_CONCURRENT_VIDEO_EDITOR = max(
    1, int(os.getenv("MAX_CONCURRENT_VIDEO_EDITOR", "2"))
)
MAX_VIDEO_EDITOR_SECONDS = max(
    60, int(os.getenv("MAX_VIDEO_EDITOR_SECONDS", str(MAX_TRANSCRIPT_SECONDS)))
)
MAX_VIDEO_EDITOR_INPUT_BYTES = min(
    TELEGRAM_MAX_BYTES,
    max(
        50 * 1024 * 1024,
        int(os.getenv("MAX_VIDEO_EDITOR_INPUT_BYTES", "1900000000")),
    ),
)
VIDEO_EDITOR_MAX_WALLCLOCK_SECONDS = max(
    300, int(os.getenv("VIDEO_EDITOR_MAX_WALLCLOCK_SECONDS", "10800"))
)
YUNET_MODEL_PATH = os.getenv(
    "YUNET_MODEL_PATH",
    str(Path(__file__).resolve().parent / "models" / "face_detection_yunet_2023mar.onnx"),
).strip()
# Local Podcast Editor V5: sequential face analysis + source-friendly camera timeline.
# A 30-FPS edit master is the best default for the typical 25/30-FPS podcast
# source: it preserves every real source frame without wasteful frame duplication.
# Stronger VPSes may explicitly choose 60 in .env.
SMART_REFRAME_SAMPLE_INTERVAL_SECONDS = min(
    0.050, max(1.0 / 30.0, float(os.getenv("SMART_REFRAME_SAMPLE_INTERVAL_SECONDS", "0.034")))
)
SMART_REFRAME_MAX_SAMPLES = min(
    3600, max(600, int(os.getenv("SMART_REFRAME_MAX_SAMPLES", "3000")))
)
LOCAL_EDITOR_OUTPUT_FPS = 30  # Fallback only when source FPS probing fails; valid sources keep native cadence.
# Never manufacture 4K. Auto Clipper already downloads the highest real YouTube source.
# Edited delivery stays source-faithful; native 4K is used as input when YouTube offers it.
AUTO_CLIPPER_DELIVER_4K = False
SMART_REFRAME_SPLIT_MIN_RATIO = min(
    0.90, max(0.20, float(os.getenv("SMART_REFRAME_SPLIT_MIN_RATIO", "0.34")))
)
SMART_REFRAME_MIN_FACE_SCORE = min(
    0.99, max(0.45, float(os.getenv("SMART_REFRAME_MIN_FACE_SCORE", "0.62")))
)
SMART_REFRAME_ALLOW_HAAR_FALLBACK = os.getenv(
    "SMART_REFRAME_ALLOW_HAAR_FALLBACK", "0"
).strip().lower() in {"1", "true", "yes", "on"}
# V5.6: detector reacquisition runs much more often than the older 8fps pass.
# Dense camera motion still comes from optical flow, so this improves identity
# lock without making every 30fps frame pay for a full face detector.
SMART_REFRAME_DETECTOR_FPS = min(18.0, max(8.0, float(os.getenv("SMART_REFRAME_DETECTOR_FPS", "18"))))
SMART_REFRAME_FLOW_MAX_MISS_SECONDS = min(2.5, max(0.45, float(os.getenv("SMART_REFRAME_FLOW_MAX_MISS_SECONDS", "0.80"))))
SMART_REFRAME_NO_FACE_PREDICT_SECONDS = min(0.75, max(0.12, float(os.getenv("SMART_REFRAME_NO_FACE_PREDICT_SECONDS", "0.36"))))
SMART_REFRAME_VIRTUAL_CENTER_MARGIN = min(0.34, max(0.10, float(os.getenv("SMART_REFRAME_VIRTUAL_CENTER_MARGIN", "0.28"))))
SMART_REFRAME_EDGE_FACE_PAD_FRACTION = min(0.70, max(0.40, float(os.getenv("SMART_REFRAME_EDGE_FACE_PAD_FRACTION", "0.50"))))
SMART_REFRAME_EDGE_FACE_MAX_DIM = min(2200, max(1200, int(os.getenv("SMART_REFRAME_EDGE_FACE_MAX_DIM", "1600"))))
SMART_REFRAME_VIRTUAL_CANVAS_MAX_PAD = min(0.80, max(0.40, float(os.getenv("SMART_REFRAME_VIRTUAL_CANVAS_MAX_PAD", "0.72"))))
SMART_REFRAME_FLOW_FB_ERROR_PX = min(4.0, max(0.8, float(os.getenv("SMART_REFRAME_FLOW_FB_ERROR_PX", "1.8"))))
SMART_REFRAME_SMOOTHING = min(
    0.96, max(0.15, float(os.getenv("SMART_REFRAME_SMOOTHING", "0.78")))
)
SMART_REFRAME_MOUTH_ACTIVITY_THRESHOLD = min(
    0.30, max(0.01, float(os.getenv("SMART_REFRAME_MOUTH_ACTIVITY_THRESHOLD", "0.035")))
)
SMART_REFRAME_DUAL_PAD_SECONDS = max(
    0.04, float(os.getenv("SMART_REFRAME_DUAL_PAD_SECONDS", "0.25"))
)
SMART_REFRAME_DUAL_MIN_SECONDS = max(
    4.0, float(os.getenv("SMART_REFRAME_DUAL_MIN_SECONDS", "4.0"))
)
# Preserve enough motion keyframes that a moving speaker stays centred while
# keeping the FFmpeg expression below its practical nesting limit.
SMART_REFRAME_TRACK_MAX_POINTS = min(
    180, max(72, int(os.getenv("SMART_REFRAME_TRACK_MAX_POINTS", "144")))
)
SMART_REFRAME_SPEAKER_SWITCH_DISTANCE = min(
    0.45, max(0.08, float(os.getenv("SMART_REFRAME_SPEAKER_SWITCH_DISTANCE", "0.16")))
)
SMART_REFRAME_FAST_SWITCH_SECONDS = min(
    0.035, max(0.008, float(os.getenv("SMART_REFRAME_FAST_SWITCH_SECONDS", "0.012")))
)
SMART_REFRAME_DEAD_ZONE = min(
    0.06, max(0.004, float(os.getenv("SMART_REFRAME_DEAD_ZONE", "0.032")))
)
# OpenSource-Clipping-inspired production tracking controls. These are kept
# local/no-Gemini: 5px micro-jitter suppression, scene-aware smoothing, and
# a hard speaker-snap threshold so the crop never glides through empty space.
OPENCLIP_TRACK_JITTER_PX = max(1.0, float(os.getenv("OPENCLIP_TRACK_JITTER_PX", "3.0")))
OPENCLIP_TRACK_SNAP = min(0.45, max(0.12, float(os.getenv("OPENCLIP_TRACK_SNAP", "0.25"))))
OPENCLIP_TRACK_SMOOTH_WINDOW = min(18, max(3, int(os.getenv("OPENCLIP_TRACK_SMOOTH_WINDOW", "7"))))
OPENCLIP_SPLIT_V_ALIGN = min(0.62, max(0.32, float(os.getenv("OPENCLIP_SPLIT_V_ALIGN", "0.42"))))
OPENCLIP_SPLIT_AUTO_ZOOM = os.getenv("OPENCLIP_SPLIT_AUTO_ZOOM", "1").strip().lower() in {"1", "true", "yes", "on"}
# V5.8 body-follow tuning.  Detector keeps identity locked while optical flow
# is allowed to carry real torso/upper-body movement instead of being snapped
# back to the face centre every detector sample.
SMART_REFRAME_FLOW_FACE_CORRECTION = min(
    0.24, max(0.01, float(os.getenv("SMART_REFRAME_FLOW_FACE_CORRECTION", "0.015")))
)
SMART_REFRAME_BODY_FOLLOW_GAIN = min(
    1.85, max(0.90, float(os.getenv("SMART_REFRAME_BODY_FOLLOW_GAIN", "1.42")))
)
# V6.9: a face detector is an identity anchor, while a short-term upper-body
# tracker supplies the composition movement. This prevents detector refreshes
# from flattening the camera back to a static face-centre crop.
SMART_REFRAME_BODY_COMPOSITION_WEIGHT = min(
    0.52, max(0.18, float(os.getenv("SMART_REFRAME_BODY_COMPOSITION_WEIGHT", "0.54")))
)
SMART_REFRAME_BODY_TRACKER_MAX_DRIFT = min(
    0.24, max(0.08, float(os.getenv("SMART_REFRAME_BODY_TRACKER_MAX_DRIFT", "0.16")))
)
SMART_REFRAME_CROSS_SHOT_SPLIT = os.getenv(
    "SMART_REFRAME_CROSS_SHOT_SPLIT", "1"
).strip().lower() in {"1", "true", "yes", "on"}
SMART_REFRAME_VERTICAL_TRAVEL_FRACTION = min(
    0.18, max(0.04, float(os.getenv("SMART_REFRAME_VERTICAL_TRAVEL_FRACTION", "0.16")))
)
SMART_REFRAME_MIN_FACE_AREA = min(
    0.20, max(0.0015, float(os.getenv("SMART_REFRAME_MIN_FACE_AREA", "0.0025")))
)
SMART_REFRAME_SWITCH_CONFIRM_SAMPLES = min(
    8, max(1, int(os.getenv("SMART_REFRAME_SWITCH_CONFIRM_SAMPLES", "5")))
)
SMART_REFRAME_MIN_SPEAKER_HOLD_SECONDS = max(
    0.02, float(os.getenv("SMART_REFRAME_MIN_SPEAKER_HOLD_SECONDS", "1.4"))
)
SMART_REFRAME_SWITCH_SCORE_RATIO = min(
    3.0, max(1.05, float(os.getenv("SMART_REFRAME_SWITCH_SCORE_RATIO", "1.35")))
)
SMART_REFRAME_NO_FACE_HOLD_SECONDS = max(
    1.0, float(os.getenv("SMART_REFRAME_NO_FACE_HOLD_SECONDS", "8.0"))
)
EDITOR_REFRESH_HOURS = max(1, int(os.getenv("EDITOR_REFRESH_HOURS", "6")))
EDITOR_BLUR_CANVAS_ENABLED = False  # Reference edit: NEVER blur the full-video canvas
ENABLE_REALESRGAN_GPU = os.getenv(
    "ENABLE_REALESRGAN_GPU", "0"
).strip().lower() in {"1", "true", "yes", "on"}

# =============================================================================
# AVCLabs MCP enhancement configuration
# AVCLabs removed in V8.3; local Real-ESRGAN/FSRCNN/Lanczos enhancement only
# =============================================================================

AVCLABS_API_KEY = os.getenv("AVCLABS_API_KEY", "").strip()
AVCLABS_MCP_COMMAND = os.getenv("AVCLABS_MCP_COMMAND", "npx").strip() or "npx"
AVCLABS_MCP_PACKAGE = os.getenv(
    "AVCLABS_MCP_PACKAGE", "@avclabs.ai/media-mcp@latest"
).strip() or "@avclabs.ai/media-mcp@latest"
AVCLABS_HTTP_API_BASE_URL = os.getenv(
    "AVCLABS_HTTP_API_BASE_URL", "https://mcp.avc.ai/enhance"
).strip()
AVCLABS_SAM3_API_BASE_URL = os.getenv(
    "AVCLABS_SAM3_API_BASE_URL", "https://mcp.avc.ai/sam"
).strip()
AVCLABS_POLL_SECONDS = max(2, int(os.getenv("AVCLABS_POLL_SECONDS", "8")))
AVCLABS_TIMEOUT_SECONDS = max(60, int(os.getenv("AVCLABS_TIMEOUT_SECONDS", "21600")))
AVCLABS_MCP_CALL_TIMEOUT_SECONDS = max(
    20, int(os.getenv("AVCLABS_MCP_CALL_TIMEOUT_SECONDS", "90"))
)
AVCLABS_MAX_LOCAL_BYTES = min(
    100 * 1024 * 1024,
    max(1 * 1024 * 1024, int(os.getenv("AVCLABS_MAX_LOCAL_BYTES", str(100 * 1024 * 1024)))),
)
AVCLABS_SAM3_POLL_INTERVAL = max(
    500, int(os.getenv("AVCLABS_SAM3_POLL_INTERVAL", "2000"))
)
AVCLABS_SAM3_POLL_MAX_ATTEMPTS = max(
    1, int(os.getenv("AVCLABS_SAM3_POLL_MAX_ATTEMPTS", "25"))
)

# Only operations documented by the official MCP package are exposed.
AVCLABS_OPERATIONS = {
    "video_480p": "Video Enhance • 480p",
    "video_540p": "Video Enhance • 540p",
    "video_720p": "Video Enhance • 720p",
    "video_1080p": "Video Enhance • 1080p",
    "video_2k": "Video Enhance • 2K",
    "sam3_objects": "SAM3 • Segment Objects",
}

# =============================================================================
# Local transcription + AI enhancement configuration
# =============================================================================

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
AUTH_DIR = BASE_DIR / "auth"

DEEP_RESEARCH_STATE_FILE = AUTH_DIR / "deep_research_state.json"
_DEEP_RESEARCH_STATE_LOCK = threading.RLock()


def _read_deep_research_state() -> Dict[str, Any]:
    with _DEEP_RESEARCH_STATE_LOCK:
        try:
            if DEEP_RESEARCH_STATE_FILE.exists():
                data = json.loads(
                    DEEP_RESEARCH_STATE_FILE.read_text(
                        encoding="utf-8"
                    )
                )
                if isinstance(data, dict):
                    return data
        except Exception:
            logging.exception(
                "Could not read Deep Viral Research state"
            )
        return {
            "enabled": bool(VIRALFORGE_DEEP_ENABLED),
            "updated_at": 0,
        }


def _write_deep_research_state(enabled: bool) -> None:
    with _DEEP_RESEARCH_STATE_LOCK:
        DEEP_RESEARCH_STATE_FILE.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        tmp = DEEP_RESEARCH_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "enabled": bool(enabled),
                    "updated_at": time.time(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.chmod(tmp, 0o600)
        tmp.replace(DEEP_RESEARCH_STATE_FILE)
        os.chmod(DEEP_RESEARCH_STATE_FILE, 0o600)


def deep_research_enabled() -> bool:
    """Deep selection is Agnes-only; editing remains fully local."""
    state = _read_deep_research_state()
    try:
        agnes_ok = bool(agnes_available())
    except Exception:
        agnes_ok = False
    return bool(
        VIRALFORGE_DEEP_ENABLED
        and bool(state.get("enabled", True))
        and agnes_ok
    )


def deep_research_admin_menu() -> InlineKeyboardMarkup:
    enabled = deep_research_enabled()
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    (
                        "✅ Deep Research Enabled"
                        if enabled
                        else "⛔ Deep Research Disabled"
                    ),
                    callback_data=(
                        "admin:deepviral_off"
                        if enabled
                        else "admin:deepviral_on"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "🧠 Agent Status",
                    callback_data="admin:deepviral_status",
                ),
                InlineKeyboardButton(
                    "⬅️ Admin Settings",
                    callback_data="menu:admin",
                ),
            ],
        ]
    )


def deep_research_status_text() -> str:
    state = _read_deep_research_state()
    return (
        "🧠 Deep Viral Research\n\n"
        f"Status: {'✅ Enabled' if deep_research_enabled() else '⛔ Disabled'}\n"
        f"Module installed: {'✅ Yes' if VIRALFORGE_AVAILABLE else '❌ No'}\n"
        f"Full-source-first analysis: ✅ Enabled\n"
        f"Final clips: {VIRALFORGE_DEEP_FINAL_CLIPS}\n"
        f"Elite picks: {VIRALFORGE_ELITE_PICKS}\n"
        f"First 5-second hook: {VIRALFORGE_FIRST5_SECONDS:.1f}s\n\n"
        "Agent order:\n"
        "🔎 Research → 👥 Audience → 📝 Full Transcript → "
        "🎞 Full Video Understanding → ⛏ Candidate Mining → "
        "👁 Visual Reactions → ⏱ Timing → 🪝 Hook Lab → 🏆 Ranking\n\n"
        "Candidate extraction starts only AFTER the complete source-analysis pass."
    )
MODEL_DIR = BASE_DIR / "models"
AUTH_SETTINGS_FILE = AUTH_DIR / "runtime_auth.json"
UPLOADED_COOKIE_FILE = AUTH_DIR / "youtube_cookies.txt"
AUTO_CLIPPER_STATE_FILE = AUTH_DIR / "auto_clipper_state.json"
AUTO_CLIPPER_STATE_LOCK = threading.RLock()
VIDEO_EDITOR_STATE_FILE = AUTH_DIR / "video_editor_state.json"
VIDEO_EDITOR_STATE_LOCK = threading.RLock()
USER_HISTORY_FILE = AUTH_DIR / "user_history.json"
ACCESS_KEYS_FILE = AUTH_DIR / "access_keys.json"
ACCESS_KEYS_LOCK = threading.RLock()
RESULT_JOBS_FILE = AUTH_DIR / "result_jobs.json"
RESULT_JOBS_LOCK = threading.RLock()
ANALYZER_MAX_TEXT_CHARS = max(
    5000, int(os.getenv("ANALYZER_MAX_TEXT_CHARS", "120000"))
)
RESULT_REEDIT_TTL_SECONDS = max(
    1800, int(os.getenv("RESULT_REEDIT_TTL_SECONDS", "21600"))
)
USER_HISTORY_LOCK = threading.RLock()
UPSCALE_JOB_LOCK = threading.RLock()
UPSCALE_JOBS: Dict[str, Dict[str, Any]] = {}
DEJAVU_FONT = Path(
    os.getenv(
        "PDF_FONT_FILE",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
)

for directory in (DOWNLOAD_DIR, AUTH_DIR, MODEL_DIR):
    directory.mkdir(parents=True, exist_ok=True)

try:
    AUTH_DIR.chmod(0o700)
except OSError:
    pass

DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
EXTRACT_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_EXTRACTS)
UPLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_UPLOADS)
TRANSCRIPT_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_TRANSCRIPTS)
FAST_ENHANCE_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_FAST_ENHANCE)
AI_ENHANCE_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_AI_ENHANCE)
AUTO_CLIPPER_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_AUTO_CLIPPER)
LOCAL_EDITOR_RENDER_SEMAPHORE = asyncio.Semaphore(LOCAL_EDITOR_RENDER_SLOTS)
VIDEO_EDITOR_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_VIDEO_EDITOR)

_AI_RUNTIME_PROBE_LOCK = threading.Lock()
_AI_RUNTIME_PROBE_RESULT: Optional[Tuple[bool, str]] = None
_AI_RUNTIME_PROBE_AT = 0.0

QUALITY_ORDER = [
    "144p", "240p", "360p", "480p", "720p", "1080p", "2K", "4K"
]
QUALITY_TO_HEIGHT = {
    "144p": 144,
    "240p": 240,
    "360p": 360,
    "480p": 480,
    "720p": 720,
    "1080p": 1080,
    "2K": 1440,
    "4K": 2160,
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts"
}

YOUTUBE_URL_RE = re.compile(
    r"""(?ix)
    (?:https?://)?
    (?:
        (?:www\.|m\.|music\.)?youtube\.com/
        (?:
            watch\?[^\s]+ |
            shorts/[A-Za-z0-9_-]{6,}[^\s]* |
            live/[A-Za-z0-9_-]{6,}[^\s]* |
            embed/[A-Za-z0-9_-]{6,}[^\s]*
        )
        |
        youtu\.be/[A-Za-z0-9_-]{6,}[^\s]*
    )
    """
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("mediautilitybot")


def _env_truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def _persist_runtime_env_value(key: str, value: str) -> None:
    """Atomically update the bot .env without printing or logging secret values."""
    env_path = BASE_DIR / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    except Exception:
        lines = []
    prefix = f"{key}="
    replaced = False
    output: List[str] = []
    for line in lines:
        if line.lstrip().startswith(prefix):
            if not replaced:
                output.append(prefix + value)
                replaced = True
            continue
        output.append(line)
    if not replaced:
        output.append(prefix + value)
    temp = env_path.with_suffix(".env.tmp")
    temp.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    temp.replace(env_path)
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass
    os.environ[key] = value


# =============================================================================
# Persistent authentication and rotating proxy pool
# =============================================================================

class RuntimeAuthStore:
    """Persistent YouTube cookies plus a round-robin proxy pool."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._cookie_file: Optional[str] = None
        self._proxies: List[Dict[str, Any]] = []
        self._cursor = 0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._cookie_file = (
                str(raw.get("cookie_file") or "").strip() or None
            )
            proxies = raw.get("proxies") or []
            if not proxies and raw.get("proxy"):
                proxies = [{"url": raw.get("proxy")}]
            if isinstance(proxies, list):
                self._proxies = [
                    {
                        "url": str(item.get("url") or "").strip(),
                        "fails": int(item.get("fails") or 0),
                        "successes": int(item.get("successes") or 0),
                        "last_used": float(item.get("last_used") or 0),
                        "disabled_until": float(
                            item.get("disabled_until") or 0
                        ),
                        "added_at": float(item.get("added_at") or time.time()),
                        "benchmark_ms": float(item.get("benchmark_ms") or 0),
                        "throughput_mbps": float(item.get("throughput_mbps") or 0),
                        "tested_at": float(item.get("tested_at") or 0),
                    }
                    for item in proxies
                    if isinstance(item, dict) and item.get("url")
                ][:MAX_PROXIES]
        except Exception:
            logger.exception("Could not load runtime authentication settings")

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "cookie_file": self._cookie_file,
                    "proxies": self._proxies,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        os.chmod(self.path, 0o600)

    def get_cookie_file(self) -> Optional[Path]:
        with self._lock:
            if not self._cookie_file:
                return None
            return Path(self._cookie_file).expanduser()

    def set_cookie_file(self, path: Path) -> None:
        with self._lock:
            self._cookie_file = str(path.resolve())
            self._save_locked()

    def clear_cookie_file(self) -> Optional[Path]:
        with self._lock:
            old = (
                Path(self._cookie_file).expanduser()
                if self._cookie_file
                else None
            )
            self._cookie_file = None
            self._save_locked()
            return old

    def add_proxies(self, raw_text: str) -> Tuple[int, int]:
        lines = [
            line.strip()
            for line in (raw_text or "").replace(",", "\n").splitlines()
        ]
        added = 0
        skipped = 0
        now = time.time()

        with self._lock:
            existing = {
                str(item.get("url") or "")
                for item in self._proxies
            }
            for line in lines:
                if not line or line.startswith("#"):
                    skipped += 1
                    continue
                try:
                    proxy_url = normalize_proxy(line)
                except ValueError:
                    skipped += 1
                    continue
                if proxy_url in existing or len(self._proxies) >= MAX_PROXIES:
                    skipped += 1
                    continue
                self._proxies.append(
                    {
                        "url": proxy_url,
                        "fails": 0,
                        "successes": 0,
                        "last_used": 0.0,
                        "disabled_until": 0.0,
                        "added_at": now,
                        "benchmark_ms": 0.0,
                        "throughput_mbps": 0.0,
                        "tested_at": 0.0,
                    }
                )
                existing.add(proxy_url)
                added += 1
            self._save_locked()

        return added, skipped

    def clear_proxies(self) -> None:
        with self._lock:
            self._proxies = []
            self._cursor = 0
            self._save_locked()

    def proxy_count(self) -> int:
        with self._lock:
            return len(self._proxies)

    def enabled_proxy_count(self) -> int:
        now = time.time()
        with self._lock:
            return sum(
                1
                for item in self._proxies
                if float(item.get("disabled_until") or 0) <= now
            )

    def list_proxies(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._proxies]

    def get_proxy_candidates(self, limit: int = PROXY_ATTEMPT_LIMIT) -> List[str]:
        """Return healthy proxies ranked by measured speed and reliability."""
        now = time.time()
        with self._lock:
            enabled = [
                item for item in self._proxies
                if float(item.get("disabled_until") or 0) <= now
            ]

            def rank(item: Dict[str, Any]) -> Tuple[float, float, float, float]:
                throughput = float(item.get("throughput_mbps") or 0.0)
                latency = float(item.get("benchmark_ms") or 0.0)
                successes = float(item.get("successes") or 0.0)
                fails = float(item.get("fails") or 0.0)
                reliability = successes / max(1.0, successes + fails)
                # Known high throughput wins first. When throughput is unknown,
                # use low benchmark latency and then historical reliability.
                return (
                    0.0 if throughput > 0 else 1.0,
                    -throughput,
                    latency if latency > 0 else 999999.0,
                    -reliability,
                )

            enabled.sort(key=rank)
            selected = enabled[: max(1, int(limit))]
            for item in selected:
                item["last_used"] = now
            if selected:
                self._save_locked()
            return [str(item["url"]) for item in selected]

    def get_proxy(self) -> Optional[str]:
        candidates = self.get_proxy_candidates(1)
        return candidates[0] if candidates else None

    def proxies_needing_benchmark(self, force: bool = False) -> List[str]:
        now = time.time()
        with self._lock:
            rows = [
                item for item in self._proxies
                if float(item.get("disabled_until") or 0) <= now
                and (
                    force
                    or now - float(item.get("tested_at") or 0)
                    >= PROXY_BENCHMARK_TTL_SECONDS
                )
            ]
            rows.sort(key=lambda item: float(item.get("tested_at") or 0))
            return [str(item["url"]) for item in rows[:PROXY_BENCHMARK_MAX]]

    def mark_proxy_benchmark(
        self,
        proxy_url: str,
        latency_ms: Optional[float],
        ok: bool,
    ) -> None:
        with self._lock:
            for item in self._proxies:
                if item.get("url") != proxy_url:
                    continue
                item["tested_at"] = time.time()
                if ok and latency_ms and latency_ms > 0:
                    previous = float(item.get("benchmark_ms") or 0.0)
                    item["benchmark_ms"] = (
                        latency_ms if previous <= 0
                        else previous * 0.65 + latency_ms * 0.35
                    )
                    item["disabled_until"] = 0.0
                else:
                    item["benchmark_ms"] = 999999.0
                break
            self._save_locked()

    def mark_proxy_ok(
        self,
        proxy_url: Optional[str],
        elapsed_seconds: Optional[float] = None,
        bytes_transferred: Optional[int] = None,
    ) -> None:
        if not proxy_url:
            return
        with self._lock:
            for item in self._proxies:
                if item.get("url") == proxy_url:
                    item["successes"] = int(item.get("successes") or 0) + 1
                    item["fails"] = max(
                        0, int(item.get("fails") or 0) - 1
                    )
                    item["disabled_until"] = 0.0
                    if elapsed_seconds and elapsed_seconds > 0:
                        elapsed_ms = elapsed_seconds * 1000.0
                        previous_latency = float(item.get("benchmark_ms") or 0.0)
                        if not bytes_transferred:
                            item["benchmark_ms"] = (
                                elapsed_ms if previous_latency <= 0
                                else previous_latency * 0.75 + elapsed_ms * 0.25
                            )
                        if bytes_transferred and bytes_transferred > 0:
                            mbps = (bytes_transferred * 8.0) / elapsed_seconds / 1_000_000.0
                            previous_speed = float(item.get("throughput_mbps") or 0.0)
                            item["throughput_mbps"] = (
                                mbps if previous_speed <= 0
                                else previous_speed * 0.70 + mbps * 0.30
                            )
                    item["tested_at"] = time.time()
                    break
            self._save_locked()

    def mark_proxy_fail(self, proxy_url: Optional[str]) -> None:
        if not proxy_url:
            return
        with self._lock:
            for item in self._proxies:
                if item.get("url") == proxy_url:
                    fails = int(item.get("fails") or 0) + 1
                    item["fails"] = fails
                    if fails >= PROXY_FAIL_LIMIT:
                        item["disabled_until"] = (
                            time.time() + PROXY_DISABLE_MINUTES * 60
                        )
                        item["fails"] = 0
                    break
            self._save_locked()


runtime_auth = RuntimeAuthStore(AUTH_SETTINGS_FILE)


# MEDIA_TRANSPORT_V1 central YouTube transport.
# This is intentionally attached to the existing RuntimeAuthStore so cookies and
# ranked proxies continue to come from the bot's current admin/runtime settings.
media_transport = MediaTransportBroker(
    MediaBrokerConfig(
        download_dir=DOWNLOAD_DIR,
        health_file=BASE_DIR / "data" / "media_route_health.json",
        bgutil_base_url=os.getenv("BGUTIL_BASE_URL", "http://127.0.0.1:4416").strip() or "http://127.0.0.1:4416",
        hedge_delay_seconds=max(1.0, min(3.0, float(os.getenv("MEDIA_HEDGE_DELAY_SECONDS", "1.5")))),
        hedge_transfer_bytes=max(32768, int(os.getenv("MEDIA_HEDGE_TRANSFER_BYTES", "131072"))),
        route_timeout_seconds=max(180.0, float(os.getenv("MEDIA_ROUTE_TIMEOUT_SECONDS", "1800"))),
        extract_timeout_seconds=max(20.0, float(os.getenv("MEDIA_EXTRACT_TIMEOUT_SECONDS", "45"))),
        verify_full_range_max_seconds=max(30.0, float(os.getenv("MEDIA_VERIFY_FULL_RANGE_MAX_SECONDS", "180"))),
        verify_sample_seconds=max(2.0, float(os.getenv("MEDIA_VERIFY_SAMPLE_SECONDS", "5"))),
        force_ipv4=bool(YTDLP_FORCE_IPV4),
        concurrent_fragments=int(YTDLP_CONCURRENT_FRAGMENTS),
        http_chunk_size=int(YTDLP_HTTP_CHUNK_SIZE),
        socket_timeout=30,
        retries=3,
        fragment_retries=3,
        ejs_enabled=bool(YTDLP_ENABLE_EJS),
        deno_path=os.getenv("DENO_BIN", "").strip(),
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
        pytubefix_enabled=os.getenv("MEDIA_PYTUBEFIX", "1").strip().lower() not in {"0", "false", "no", "off"},
    ),
    cookie_getter=runtime_auth.get_cookie_file,
    proxy_getter=lambda: runtime_auth.get_proxy_candidates(PROXY_ATTEMPT_LIMIT),
    mark_proxy_ok=runtime_auth.mark_proxy_ok,
    mark_proxy_fail=runtime_auth.mark_proxy_fail,
)


def _benchmark_proxy_once(proxy_url: str) -> Tuple[str, Optional[float], bool]:
    started = time.monotonic()
    try:
        handler = urllib.request.ProxyHandler({
            "http": proxy_url,
            "https": proxy_url,
        })
        opener = urllib.request.build_opener(handler)
        request = urllib.request.Request(
            PROXY_BENCHMARK_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            method="GET",
        )
        with opener.open(
            request,
            timeout=PROXY_BENCHMARK_TIMEOUT_SECONDS,
        ) as response:
            response.read(64)
        return proxy_url, (time.monotonic() - started) * 1000.0, True
    except Exception:
        return proxy_url, None, False


def _benchmark_proxy_pool_sync(force: bool = False) -> int:
    candidates = runtime_auth.proxies_needing_benchmark(force=force)
    if not candidates:
        return 0
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(PROXY_BENCHMARK_WORKERS, len(candidates)),
        thread_name_prefix="proxy-speed",
    ) as executor:
        futures = [executor.submit(_benchmark_proxy_once, url) for url in candidates]
        for future in concurrent.futures.as_completed(futures):
            proxy_url, latency_ms, ok = future.result()
            runtime_auth.mark_proxy_benchmark(proxy_url, latency_ms, ok)
    return len(candidates)


async def refresh_proxy_benchmarks_if_needed(force: bool = False) -> int:
    if runtime_auth.proxy_count() <= 1 and not force:
        return 0
    return await asyncio.to_thread(_benchmark_proxy_pool_sync, force)


# =============================================================================
# Day-based access keys
# =============================================================================

def _read_access_keys() -> Dict[str, Any]:
    with ACCESS_KEYS_LOCK:
        try:
            if not ACCESS_KEYS_FILE.exists():
                return {"keys": {}, "users": {}}
            data = json.loads(ACCESS_KEYS_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"keys": {}, "users": {}}
            data.setdefault("keys", {})
            data.setdefault("users", {})
            return data
        except Exception:
            logger.exception("Could not read access key database")
            return {"keys": {}, "users": {}}


def _write_access_keys(data: Dict[str, Any]) -> None:
    with ACCESS_KEYS_LOCK:
        ACCESS_KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = ACCESS_KEYS_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(ACCESS_KEYS_FILE)
        os.chmod(ACCESS_KEYS_FILE, 0o600)


def create_access_key(scope: str, days: int) -> str:
    scope = scope.lower().strip()
    if scope not in {"clipper", "editor", "both"}:
        raise ValueError("Scope must be clipper, editor, or both")
    days = max(1, min(3650, int(days)))
    token = f"MV-{scope[:1].upper()}{days}-{uuid.uuid4().hex[:10].upper()}"
    data = _read_access_keys()
    data["keys"][token] = {
        "scope": scope,
        "days": days,
        "created_at": time.time(),
        "redeemed_by": 0,
        "redeemed_at": 0,
    }
    _write_access_keys(data)
    return token


def redeem_access_key(user_id: int, token: str) -> Tuple[bool, str]:
    token = str(token or "").strip().upper()
    data = _read_access_keys()
    item = data.get("keys", {}).get(token)
    if not isinstance(item, dict):
        return False, "Invalid key."
    if int(item.get("redeemed_by") or 0):
        return False, "This key has already been used."

    scope = str(item.get("scope") or "").strip().lower()
    if scope not in {"clipper", "editor", "both"}:
        return False, "Invalid key scope."
    days = max(1, int(item.get("days") or 1))
    now = time.time()
    users = data.setdefault("users", {})
    user = users.setdefault(str(int(user_id)), {})
    scopes = ["clipper", "editor"] if scope == "both" else [scope]

    for name in scopes:
        current = float(user.get(name) or 0)
        user[name] = max(now, current) + days * 86400

    item["redeemed_by"] = int(user_id)
    item["redeemed_at"] = now
    _write_access_keys(data)
    return True, f"Key activated for {scope}: {days} day(s)."


def user_access_expiry(user_id: int, scope: str) -> float:
    if scope not in {"clipper", "editor"}:
        return 0.0
    data = _read_access_keys()
    user = data.get("users", {}).get(str(int(user_id)), {})
    if not isinstance(user, dict):
        return 0.0
    try:
        return float(user.get(scope) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def user_has_key_access(user_id: int, scope: str) -> bool:
    return user_access_expiry(user_id, scope) > time.time()


def create_access_keys(scope: str, days: int, count: int = 1) -> List[str]:
    count = max(1, min(100, int(count)))
    return [create_access_key(scope, days) for _ in range(count)]


def _feature_trial_field(scope: str) -> str:
    if scope not in {"clipper", "editor"}:
        raise ValueError("Unknown paid feature scope")
    return f"trial_{scope}_used"


def feature_trial_used(user_id: int, scope: str) -> int:
    data = _read_access_keys()
    user = data.get("users", {}).get(str(int(user_id)), {})
    if not isinstance(user, dict):
        return 0
    return max(0, int(user.get(_feature_trial_field(scope)) or 0))


def feature_trial_remaining(user_id: int, scope: str) -> int:
    return max(0, FREE_TRIAL_USES_PER_FEATURE - feature_trial_used(user_id, scope))


def consume_feature_trial(user_id: int, scope: str) -> Tuple[bool, int]:
    """Atomically consume one free use. Paid keys never consume trials."""
    if user_has_key_access(user_id, scope):
        return True, feature_trial_remaining(user_id, scope)
    field = _feature_trial_field(scope)
    with ACCESS_KEYS_LOCK:
        data = _read_access_keys()
        users = data.setdefault("users", {})
        user = users.setdefault(str(int(user_id)), {})
        used = max(0, int(user.get(field) or 0))
        if used >= FREE_TRIAL_USES_PER_FEATURE:
            return False, 0
        used += 1
        user[field] = used
        _write_access_keys(data)
    return True, max(0, FREE_TRIAL_USES_PER_FEATURE - used)


def paid_access_text(scope: str, user_id: int) -> str:
    label = "AI Clipper Pro" if scope == "clipper" else "AI Video Editor"
    remaining = feature_trial_remaining(user_id, scope)
    admin = f"@{OWNER_USERNAME}" if OWNER_USERNAME else OWNER_DISPLAY_NAME
    if remaining > 0:
        return (
            f"{label}\n\n"
            f"🎁 Free trials remaining: {remaining}/{FREE_TRIAL_USES_PER_FEATURE}\n"
            "A trial is counted only when final processing starts.\n\n"
            "Paid key already hai to use karein:\n/redeem YOUR_KEY\n\n"
            f"Key ke liye contact admin: {admin}"
        )
    return (
        f"🔐 {label} requires an access key.\n\n"
        f"Your {FREE_TRIAL_USES_PER_FEATURE} free uses are finished.\n"
        "Activate a key with:\n/redeem YOUR_KEY\n\n"
        f"Contact admin: {admin}"
    )


async def command_genkey(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    args = context.args or []
    if len(args) not in {2, 3}:
        await update.message.reply_text(
            "Usage:\n"
            "/genkey both 1       (one key, 1 day)\n"
            "/genkey both 1 10    (10 keys, 1 day)\n"
            "/genkey clipper 30 5\n"
            "/genkey editor 30"
        )
        return
    try:
        scope = args[0].lower()
        days = int(args[1])
        count = int(args[2]) if len(args) == 3 else 1
        tokens = create_access_keys(scope, days, count)
    except Exception as exc:
        await update.message.reply_text(f"Key error: {str(exc)[:200]}")
        return
    body = (
        f"🔑 New Access Key{'s' if len(tokens) != 1 else ''}\n\n"
        + "\n".join(tokens)
        + f"\n\nScope: {scope}\nDays: {days}\nCount: {len(tokens)}"
    )
    if len(body) <= 3900:
        await update.message.reply_text(body)
    else:
        path = DOWNLOAD_DIR / f"keys_{scope}_{days}d_{int(time.time())}.txt"
        path.write_text(body, encoding="utf-8")
        try:
            await send_local_document(context, update.effective_chat.id, path, f"{len(tokens)} access keys")
        finally:
            path.unlink(missing_ok=True)


async def command_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    token = " ".join(context.args or []).strip()
    if not token:
        await update.message.reply_text("Usage: /redeem YOUR_KEY")
        return
    ok, message = redeem_access_key(update.effective_user.id, token)
    await update.message.reply_text(("✅ " if ok else "❌ ") + message)


async def command_keystatus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    uid = update.effective_user.id
    now = time.time()
    lines = ["🔐 Access Status", ""]
    for scope in ("clipper", "editor"):
        expiry = user_access_expiry(uid, scope)
        if expiry > now:
            remaining = max(0, int((expiry - now + 86399) // 86400))
            lines.append(
                f"{scope.title()}: Active ({remaining} day(s))\n"
                f"Expires: {_format_history_time(expiry)}"
            )
        else:
            lines.append(
                f"{scope.title()}: Inactive\n"
                f"Free trials remaining: {feature_trial_remaining(uid, scope)}/{FREE_TRIAL_USES_PER_FEATURE}"
            )
    await update.message.reply_text("\n\n".join(lines))


async def command_keys(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    data = _read_access_keys()
    keys = data.get("keys", {})
    unused = []
    used = []
    for token, item in keys.items():
        row = f"{token} | {item.get('scope')} | {item.get('days')}d"
        if int(item.get("redeemed_by") or 0):
            used.append(row + f" | user {item.get('redeemed_by')}")
        else:
            unused.append(row)
    body = (
        f"🔑 Keys\n\nUnused: {len(unused)}\n"
        + ("\n".join(unused[-30:]) or "-")
        + f"\n\nUsed: {len(used)}\n"
        + ("\n".join(used[-20:]) or "-")
    )
    await update.message.reply_text(body[:3900])


# =============================================================================
# Persistent user history
# =============================================================================

def _read_user_history() -> Dict[str, Any]:
    with USER_HISTORY_LOCK:
        try:
            if not USER_HISTORY_FILE.exists():
                return {"users": {}, "total_starts": 0}
            data = json.loads(USER_HISTORY_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"users": {}, "total_starts": 0}
            if not isinstance(data.get("users"), dict):
                data["users"] = {}
            data["total_starts"] = int(data.get("total_starts") or 0)
            return data
        except Exception:
            logger.exception("Could not read user history")
            return {"users": {}, "total_starts": 0}


def record_user_start(user: Any) -> None:
    if user is None:
        return
    now = time.time()
    with USER_HISTORY_LOCK:
        data = _read_user_history()
        users = data.setdefault("users", {})
        key = str(int(user.id))
        old = users.get(key) if isinstance(users.get(key), dict) else {}
        users[key] = {
            "user_id": int(user.id),
            "username": str(getattr(user, "username", "") or ""),
            "first_name": str(getattr(user, "first_name", "") or ""),
            "last_name": str(getattr(user, "last_name", "") or ""),
            "language_code": str(getattr(user, "language_code", "") or ""),
            "first_seen": float(old.get("first_seen") or now),
            "last_seen": now,
            "start_count": int(old.get("start_count") or 0) + 1,
        }
        data["total_starts"] = int(data.get("total_starts") or 0) + 1
        temp = USER_HISTORY_FILE.with_suffix(".tmp")
        temp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.chmod(temp, 0o600)
        temp.replace(USER_HISTORY_FILE)
        os.chmod(USER_HISTORY_FILE, 0o600)


def _format_history_time(value: Any) -> str:
    try:
        return time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(float(value)),
        )
    except Exception:
        return "-"


async def command_history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message:
        return
    if not is_owner(update):
        await update.message.reply_text("Owner-only command.")
        return

    data = _read_user_history()
    users = list((data.get("users") or {}).values())
    users = [item for item in users if isinstance(item, dict)]
    users.sort(
        key=lambda item: float(item.get("last_seen") or 0),
        reverse=True,
    )

    lines = [
        "📊 Bot User History",
        "",
        f"Unique users who used /start: {len(users)}",
        f"Total /start events: {int(data.get('total_starts') or 0)}",
        "",
    ]

    for index, item in enumerate(users, 1):
        username = str(item.get("username") or "")
        full_name = " ".join(
            value for value in [
                str(item.get("first_name") or ""),
                str(item.get("last_name") or ""),
            ] if value
        ).strip() or "-"
        lines.extend([
            f"{index}. {full_name}",
            f"ID: {item.get('user_id')}",
            f"Username: @{username}" if username else "Username: -",
            f"Language: {item.get('language_code') or '-'}",
            f"Starts: {int(item.get('start_count') or 0)}",
            f"First: {_format_history_time(item.get('first_seen'))}",
            f"Last: {_format_history_time(item.get('last_seen'))}",
            "",
        ])

    body = "\n".join(lines)
    if len(body) <= 3900:
        await update.message.reply_text(body)
        return

    report = DOWNLOAD_DIR / f"bot_user_history_{int(time.time())}.txt"
    report.write_text(body, encoding="utf-8")
    try:
        await send_local_document(
            context,
            update.effective_chat.id,
            report,
            (
                f"Bot user history: {len(users)} unique users, "
                f"{int(data.get('total_starts') or 0)} total starts."
            ),
        )
    finally:
        report.unlink(missing_ok=True)


# =============================================================================
# Job and rate management
# =============================================================================

class JobCancelled(RuntimeError):
    """Raised when a user requests cancellation of an active task."""


class SlowDownloadRoute(RuntimeError):
    """Raised to leave a persistently slow direct YouTube route for a proxy."""


@dataclass
class RunningJob:
    user_id: int
    job_key: str
    kind: str
    cancel_event: threading.Event
    started_at: float
    title: str = ""
    username: str = ""
    stage: str = "Queued"
    updated_at: float = 0.0


class ActiveJobRegistry:
    """Thread-safe registry for live processing, cancellation, and admin monitoring."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: Dict[str, RunningJob] = {}
        self._user_keys: Dict[int, set[str]] = {}

    def try_start(
        self,
        user_id: int,
        job_key: str,
        kind: str = "task",
        *,
        is_admin: bool = False,
        title: str = "",
        username: str = "",
    ) -> Tuple[bool, str, Optional[threading.Event]]:
        with self._lock:
            if job_key in self._jobs:
                return False, "This task is already running.", None

            user_keys = self._user_keys.setdefault(user_id, set())
            if (
                not (is_admin and ADMIN_UNLIMITED_JOBS)
                and len(user_keys) >= MAX_ACTIVE_JOBS_PER_USER
            ):
                return (
                    False,
                    "You already have an active task. Use /stop to cancel it.",
                    None,
                )

            now = time.time()
            event = threading.Event()
            self._jobs[job_key] = RunningJob(
                user_id=user_id,
                job_key=job_key,
                kind=kind,
                cancel_event=event,
                started_at=now,
                title=title[:160],
                username=username[:64],
                stage="Queued",
                updated_at=now,
            )
            user_keys.add(job_key)
            return True, "", event

    def update(
        self,
        job_key: str,
        *,
        stage: Optional[str] = None,
        title: Optional[str] = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_key)
            if not job:
                return
            if stage is not None:
                job.stage = str(stage)[:220]
            if title is not None:
                job.title = str(title)[:160]
            job.updated_at = time.time()

    def finish(self, user_id: int, job_key: str) -> None:
        with self._lock:
            self._jobs.pop(job_key, None)
            user_keys = self._user_keys.get(user_id)
            if user_keys is not None:
                user_keys.discard(job_key)
                if not user_keys:
                    self._user_keys.pop(user_id, None)

    def cancel_user(self, user_id: int) -> int:
        with self._lock:
            keys = list(self._user_keys.get(user_id, set()))
            for key in keys:
                job = self._jobs.get(key)
                if job:
                    job.cancel_event.set()
                    job.stage = "Stopping..."
                    job.updated_at = time.time()
            return len(keys)

    def cancel_all(self) -> int:
        with self._lock:
            count = 0
            for job in self._jobs.values():
                if not job.cancel_event.is_set():
                    job.cancel_event.set()
                    job.stage = "Stopping..."
                    job.updated_at = time.time()
                    count += 1
            return count

    def active_count(self) -> int:
        with self._lock:
            return len(self._jobs)

    def describe_user_jobs(self, user_id: int) -> List[str]:
        with self._lock:
            return [
                self._jobs[key].kind
                for key in self._user_keys.get(user_id, set())
                if key in self._jobs
            ]

    def snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows: List[Dict[str, Any]] = []
            for job in self._jobs.values():
                rows.append(
                    {
                        "user_id": job.user_id,
                        "job_key": job.job_key,
                        "kind": job.kind,
                        "started_at": job.started_at,
                        "title": job.title,
                        "username": job.username,
                        "stage": job.stage,
                        "updated_at": job.updated_at,
                        "cancel_requested": job.cancel_event.is_set(),
                    }
                )
            return sorted(rows, key=lambda item: item["started_at"])

    def counts_by_kind(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        with self._lock:
            for job in self._jobs.values():
                counts[job.kind] = counts.get(job.kind, 0) + 1
        return counts


@asynccontextmanager
async def cancellable_slot(
    semaphore: asyncio.Semaphore,
    cancel_event: threading.Event,
):
    acquired = False
    try:
        while not acquired:
            if cancel_event.is_set():
                raise JobCancelled("Task stopped by the user")
            try:
                await asyncio.wait_for(
                    semaphore.acquire(),
                    timeout=CANCEL_POLL_SECONDS,
                )
                acquired = True
            except asyncio.TimeoutError:
                continue
        if cancel_event.is_set():
            raise JobCancelled("Task stopped by the user")
        yield
    finally:
        if acquired:
            semaphore.release()


def ensure_not_cancelled(cancel_event: threading.Event) -> None:
    if cancel_event.is_set():
        raise JobCancelled("Task stopped by the user")


class RequestRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._last: Dict[int, float] = {}

    def allow(self, user_id: int) -> Tuple[bool, float]:
        if REQUEST_COOLDOWN_SECONDS <= 0:
            return True, 0.0
        now = time.monotonic()
        with self._lock:
            previous = self._last.get(user_id, 0.0)
            remaining = REQUEST_COOLDOWN_SECONDS - (now - previous)
            if remaining > 0:
                return False, remaining
            self._last[user_id] = now
            return True, 0.0


active_jobs = ActiveJobRegistry()
request_limiter = RequestRateLimiter()


class CleanupManager:
    def __init__(self) -> None:
        self._tasks: Dict[str, asyncio.Task] = {}

    async def schedule(
        self,
        paths: Iterable[Optional[Path]],
        minutes: int = FILE_CLEANUP_MINUTES,
    ) -> None:
        for path in paths:
            if path is None:
                continue
            key = str(path.resolve())
            old = self._tasks.pop(key, None)
            if old:
                old.cancel()

            async def delete_later(
                target: Path = path,
                target_key: str = key,
            ) -> None:
                try:
                    await asyncio.sleep(minutes * 60)
                    if target.is_dir():
                        shutil.rmtree(target, ignore_errors=True)
                    else:
                        target.unlink(missing_ok=True)
                except asyncio.CancelledError:
                    return
                except Exception:
                    logger.exception("Could not clean up %s", target)
                finally:
                    self._tasks.pop(target_key, None)

            self._tasks[key] = asyncio.create_task(delete_later())

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


cleanup_manager = CleanupManager()


# =============================================================================
# Data models
# =============================================================================

@dataclass
class QualityChoice:
    label: str
    height: int
    selector: str
    estimated_size: Optional[int]
    approximate: bool
    telegram_video: bool
    output_container: str
    width: Optional[int] = None
    fps: Optional[float] = None


@dataclass
class ViralSegment:
    start: float
    end: float
    score: int
    reason: str
    text: str
    hook: str = ""
    caption: str = ""
    hashtags: str = ""
    # Deep Viral Research metadata. Defaults preserve every existing constructor.
    rank: int = 0
    elite_pick: bool = False
    hook_duration: float = 5.0
    deep_meta: Dict[str, Any] = field(default_factory=dict)



def _vf_num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _vf_text(value: Any, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def viralforge_segments_from_result(
    result: Dict[str, Any],
    paragraphs: List["TranscriptParagraph"],
) -> List[ViralSegment]:
    """Map the modular research/ranking output into the EXISTING renderer model."""
    ranking = result.get("ranking") or {}
    rows = list(ranking.get("final10") or ranking.get("top10") or [])
    segments: List[ViralSegment] = []
    for index, item in enumerate(rows[:VIRALFORGE_DEEP_FINAL_CLIPS], 1):
        if not isinstance(item, dict):
            continue
        start = max(0.0, _vf_num(item.get("start")))
        end = max(start + 0.5, _vf_num(item.get("end"), start + 30.0))
        clip_text = _segment_text(paragraphs, start, end)
        roman = _vf_text(item.get("roman_urdu"), 600)
        why = _vf_text(item.get("why_viral") or item.get("why_candidate"), 500)
        hook_lab = item.get("hook_lab") if isinstance(item.get("hook_lab"), dict) else {}
        hook = _vf_text(
            item.get("video_hook_first5")
            or hook_lab.get("text_hook")
            or item.get("first5_hook")
            or item.get("topic"),
            90,
        )
        title_hooks = item.get("title_hooks") or hook_lab.get("title_hooks") or []
        if not hook and isinstance(title_hooks, list) and title_hooks:
            hook = _vf_text(title_hooks[0], 90)
        caption_cta = _vf_text(item.get("caption_cta") or hook_lab.get("caption_cta"), 500)
        if caption_cta:
            caption = caption_cta
            _, hashtags = generate_local_caption_and_hashtags(clip_text or roman)
        else:
            caption, hashtags = generate_local_caption_and_hashtags(clip_text or roman)
        score = int(max(1, min(100, round(_vf_num(item.get("score"), 50)))))
        rank = int(item.get("rank") or index)
        elite = bool(item.get("elite_pick", rank <= VIRALFORGE_ELITE_PICKS))
        reason = why or _vf_text(item.get("reason"), 300) or "Research-first multimodal ranking"
        deep_meta = dict(item)
        deep_meta["research_summary"] = result.get("research") or {}
        deep_meta["audience_profile"] = result.get("audience") or {}
        segments.append(
            ViralSegment(
                start=start,
                end=end,
                score=score,
                reason=reason,
                text=clip_text or roman,
                hook=hook or generate_local_hook(clip_text or roman),
                caption=caption,
                hashtags=hashtags,
                rank=rank,
                elite_pick=elite,
                hook_duration=max(5.0, VIRALFORGE_FIRST5_SECONDS),
                deep_meta=deep_meta,
            )
        )
    return segments


@dataclass
class ClipperWordTiming:
    start: float
    end: float
    text: str


@dataclass
class SmartReframePlan:
    mode: str
    primary_track: List[Tuple[float, float]]
    secondary_track: List[Tuple[float, float]]
    note: str
    dual_speaker_intervals: List[Tuple[float, float]] = field(default_factory=list)
    single_speaker_emphasis_intervals: List[Tuple[float, float]] = field(default_factory=list)
    split_primary_track: List[Tuple[float, float]] = field(default_factory=list)
    split_secondary_track: List[Tuple[float, float]] = field(default_factory=list)
    split_primary_y_track: List[Tuple[float, float]] = field(default_factory=list)
    split_secondary_y_track: List[Tuple[float, float]] = field(default_factory=list)
    split_primary_face_h: float = 0.0
    split_secondary_face_h: float = 0.0
    primary_y_track: List[Tuple[float, float]] = field(default_factory=list)
    primary_face_h: float = 0.0
    # Cross-camera podcast fallback. If two speakers occur in alternating shots
    # rather than the same source frame, renderer can stack two verified
    # reference portraits for a minimum four-second split instead of silently
    # never showing the requested split. Times are clip-relative.
    cross_scene_split_intervals: List[Tuple[float, float]] = field(default_factory=list)
    split_primary_reference_time: float = -1.0
    split_secondary_reference_time: float = -1.0
    split_primary_reference_window: Tuple[float, float] = (-1.0, -1.0)
    split_secondary_reference_window: Tuple[float, float] = (-1.0, -1.0)
    # Shot-level A/B identity timeline for cross-camera splits.  The renderer
    # uses this to keep the CURRENT speaking shot live and lip-synced to source
    # audio while only the non-visible listener uses a moving reference panel.
    cross_scene_speaker_intervals: List[Tuple[float, float, str]] = field(default_factory=list)


ProgressCallback = Callable[[str], Awaitable[None]]


# =============================================================================
# Auto Clipper Pro access control and core helpers
# =============================================================================

def _read_auto_clipper_state() -> Dict[str, Any]:
    with AUTO_CLIPPER_STATE_LOCK:
        try:
            if not AUTO_CLIPPER_STATE_FILE.exists():
                return {"public_enabled": False}
            data = json.loads(
                AUTO_CLIPPER_STATE_FILE.read_text(encoding="utf-8")
            )
            if not isinstance(data, dict):
                return {"public_enabled": False}
            return {
                "public_enabled": bool(data.get("public_enabled", False)),
                "updated_at": float(data.get("updated_at") or 0),
            }
        except Exception as exc:
            logger.warning("Could not read Auto Clipper state: %s", exc)
            return {"public_enabled": False}


def set_auto_clipper_public_enabled(enabled: bool) -> None:
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "public_enabled": bool(enabled),
        "updated_at": time.time(),
    }
    temp_path = AUTO_CLIPPER_STATE_FILE.with_suffix(".tmp")
    with AUTO_CLIPPER_STATE_LOCK:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp_path, AUTO_CLIPPER_STATE_FILE)
        try:
            AUTO_CLIPPER_STATE_FILE.chmod(0o600)
        except OSError:
            pass


def auto_clipper_public_enabled() -> bool:
    return bool(_read_auto_clipper_state().get("public_enabled", False))


def is_auto_clipper_allowed(user_id: int) -> bool:
    if OWNER_ID > 0 and int(user_id) == OWNER_ID:
        return True
    return user_has_key_access(user_id, "clipper") or feature_trial_remaining(user_id, "clipper") > 0


def _read_video_editor_state() -> Dict[str, Any]:
    with VIDEO_EDITOR_STATE_LOCK:
        try:
            if not VIDEO_EDITOR_STATE_FILE.exists():
                return {"public_enabled": False}
            data = json.loads(
                VIDEO_EDITOR_STATE_FILE.read_text(encoding="utf-8")
            )
            if not isinstance(data, dict):
                return {"public_enabled": False}
            return {
                "public_enabled": bool(data.get("public_enabled", False)),
                "updated_at": float(data.get("updated_at") or 0),
            }
        except Exception as exc:
            logger.warning("Could not read Video Editor state: %s", exc)
            return {"public_enabled": False}


def set_video_editor_public_enabled(enabled: bool) -> None:
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "public_enabled": bool(enabled),
        "updated_at": time.time(),
    }
    temp_path = VIDEO_EDITOR_STATE_FILE.with_suffix(".tmp")
    with VIDEO_EDITOR_STATE_LOCK:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp_path, VIDEO_EDITOR_STATE_FILE)
        try:
            VIDEO_EDITOR_STATE_FILE.chmod(0o600)
        except OSError:
            pass


def video_editor_public_enabled() -> bool:
    return bool(_read_video_editor_state().get("public_enabled", False))


def is_video_editor_allowed(user_id: int) -> bool:
    if OWNER_ID > 0 and int(user_id) == OWNER_ID:
        return True
    return user_has_key_access(user_id, "editor") or feature_trial_remaining(user_id, "editor") > 0


def _clipper_duration_seconds(value: str) -> Optional[int]:
    """
    Internal duration selector:
    60  -> natural 30-60s
    90  -> natural 60-90s
    120 -> natural 90-180s
    all -> Auto chooses the best range from the viral core
    """
    mapping = {
        "60": 60,
        "90": 90,
        "120": 120,
        "all": 0,
    }
    return mapping.get(value)


def _clipper_duration_label(value: str) -> str:
    return {
        "60": "Natural 30-60 seconds",
        "90": "Natural 60-90 seconds",
        "120": "Natural 90 seconds - 3 minutes",
        "all": "Auto - best natural viral length",
    }.get(value, value)


def _clipper_overlap_ratio(
    a_start: float,
    a_end: float,
    b_start: float,
    b_end: float,
) -> float:
    overlap = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    if overlap <= 0:
        return 0.0
    shortest = max(0.001, min(a_end - a_start, b_end - b_start))
    return overlap / shortest


def _clipper_clean_text(value: str, max_len: int = 280) -> str:
    value = normalize_transcript_text(str(value or ""))
    value = re.sub(r"\s+", " ", value).strip()
    return value[:max_len].strip()


def _clipper_escape_drawtext(value: str) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
    )


def _clipper_ass_escape(value: str) -> str:
    return (
        str(value or "")
        .replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\n", r"\N")
    )


def _clipper_format_timestamp(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _clipper_keyword_tokens(text_value: str) -> List[str]:
    stopwords = {
        "the", "and", "that", "this", "with", "from", "have", "your", "you",
        "was", "were", "are", "for", "but", "not", "they", "their", "there",
        "what", "when", "where", "which", "about", "into", "then", "than",
        "just", "like", "really", "would", "could", "should", "because",
        "been", "being", "some", "more", "very", "over", "under", "our",
        "his", "her", "she", "him", "them", "who", "why", "how", "can",
        "did", "does", "had", "has", "will", "its", "it's", "i'm", "we're",
    }
    words = re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}", text_value.lower())
    counts: Dict[str, int] = {}
    for word in words:
        if word in stopwords:
            continue
        counts[word] = counts.get(word, 0) + 1
    return [
        word
        for word, _ in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]


def generate_local_hook(text_value: str) -> str:
    text_value = _clipper_clean_text(text_value, 220)
    sentences = re.split(r"(?<=[.!?])\s+", text_value)
    candidate = next(
        (item.strip() for item in sentences if 18 <= len(item.strip()) <= 95),
        text_value,
    )
    candidate = candidate.strip(" .!?")
    if not candidate:
        return "You need to hear this"
    if len(candidate) > 82:
        candidate = candidate[:79].rsplit(" ", 1)[0] + "..."
    return candidate


def generate_local_caption_and_hashtags(
    text_value: str,
) -> Tuple[str, str]:
    cleaned = _clipper_clean_text(text_value, 320)
    first = re.split(r"(?<=[.!?])\s+", cleaned)[0].strip()
    if not first:
        first = "A moment worth watching."
    if len(first) > 150:
        first = first[:147].rsplit(" ", 1)[0] + "..."

    keywords = _clipper_keyword_tokens(cleaned)[:5]
    tags = ["#Podcast", "#ViralClip", "#TikTok"]
    for word in keywords:
        tag = "#" + re.sub(r"[^A-Za-z0-9]", "", word.title())
        if len(tag) > 1 and tag.lower() not in {t.lower() for t in tags}:
            tags.append(tag)
    tags = tags[:8]
    return first, " ".join(tags)


def _extract_json_from_llm_text(value: str) -> Any:
    value = str(value or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)

    for candidate in (value,):
        try:
            return json.loads(candidate)
        except Exception:
            pass

    first_array = value.find("[")
    last_array = value.rfind("]")
    if first_array >= 0 and last_array > first_array:
        try:
            return json.loads(value[first_array:last_array + 1])
        except Exception:
            pass

    first_obj = value.find("{")
    last_obj = value.rfind("}")
    if first_obj >= 0 and last_obj > first_obj:
        try:
            return json.loads(value[first_obj:last_obj + 1])
        except Exception:
            pass
    raise ValueError("LLM response did not contain valid JSON")


def _bot_ai_text_ready() -> bool:
    """Quick Clip AI readiness = Agnes only."""
    try:
        return bool(agnes_available())
    except Exception:
        return False


def _call_clipper_llm_sync(prompt: str) -> Optional[Any]:
    """
    Quick Auto Clipper AI selection route.

    Agnes is used ONLY here for viral moment selection. It does not
    control video editing, rendering, framing or Local QA.
    """
    if not agnes_available():
        return None

    system = (
        "You are Agnes, the dedicated Viral Clip Selection Expert for Quick Auto Clipper. "
        "Your only job is to choose the strongest standalone viral podcast moments from "
        "the supplied timestamped transcript. Prioritize first-3/5-second stopping power, "
        "curiosity, emotional payoff, controversy/debate, funny/awkward reactions, "
        "comment potential, clear context and a satisfying ending. "
        "Never invent timestamps. Return strict JSON only."
    )
    try:
        return agnes_chat_json(system, prompt, max_tokens=8192)
    except Exception as exc:
        logger.warning("Agnes Quick Clip selection failed: %s", exc)
        return None

def _nearest_paragraph_boundary(
    paragraphs: List["TranscriptParagraph"],
    value: float,
    use_start: bool,
) -> float:
    if not paragraphs:
        return max(0.0, value)
    values = [
        float(p.start if use_start else p.end)
        for p in paragraphs
    ]
    return min(values, key=lambda item: abs(item - value))


def _segment_text(
    paragraphs: List["TranscriptParagraph"],
    start: float,
    end: float,
) -> str:
    values = [
        paragraph.text
        for paragraph in paragraphs
        if paragraph.end > start and paragraph.start < end
    ]
    return normalize_transcript_text(" ".join(values))


def _normalize_viral_segment(
    item: Dict[str, Any],
    paragraphs: List["TranscriptParagraph"],
    video_duration: float,
) -> Optional[ViralSegment]:
    try:
        start = float(item.get("start", item.get("start_time", 0)))
        end = float(item.get("end", item.get("end_time", 0)))
        score = int(round(float(item.get("score", 0))))
    except Exception:
        return None

    if end <= start:
        return None
    start = max(0.0, min(start, video_duration))
    end = max(start + 1.0, min(end, video_duration))

    start = _nearest_paragraph_boundary(paragraphs, start, True)
    end = _nearest_paragraph_boundary(paragraphs, end, False)
    end = max(start + 1.0, min(end, video_duration))

    text_value = _segment_text(paragraphs, start, end)
    if not text_value:
        return None

    hook = _clipper_clean_text(str(item.get("hook") or ""), 100)
    caption = _clipper_clean_text(str(item.get("caption") or ""), 180)
    hashtags_value = item.get("hashtags") or ""
    if isinstance(hashtags_value, list):
        hashtags = " ".join(str(value) for value in hashtags_value)
    else:
        hashtags = str(hashtags_value)
    hashtags = " ".join(
        token
        for token in hashtags.split()
        if token.startswith("#")
    )[:300]

    if not hook:
        hook = generate_local_hook(text_value)
    if not caption or not hashtags:
        local_caption, local_tags = generate_local_caption_and_hashtags(
            text_value
        )
        caption = caption or local_caption
        hashtags = hashtags or local_tags

    return ViralSegment(
        start=start,
        end=end,
        score=max(1, min(100, score or 50)),
        reason=_clipper_clean_text(
            str(item.get("reason") or "High-engagement transcript moment"),
            220,
        ),
        text=text_value,
        hook=hook,
        caption=caption,
        hashtags=hashtags,
    )


def _dedupe_viral_segments(
    segments: List[ViralSegment],
    limit: int,
) -> List[ViralSegment]:
    output: List[ViralSegment] = []
    for segment in sorted(segments, key=lambda item: item.score, reverse=True):
        if any(
            _clipper_overlap_ratio(
                segment.start,
                segment.end,
                existing.start,
                existing.end,
            ) >= 0.60
            for existing in output
        ):
            continue
        output.append(segment)
        if len(output) >= limit:
            break
    return output


def _transcript_prompt_chunks(
    paragraphs: List["TranscriptParagraph"],
) -> List[str]:
    """Build size-bounded timestamped transcript chunks for the clipper LLM."""
    max_chars = max(4000, int(AUTO_CLIPPER_LLM_CHUNK_CHARS))
    chunks: List[str] = []
    current: List[str] = []
    current_chars = 0

    for paragraph in paragraphs:
        text_value = normalize_transcript_text(str(paragraph.text or ""))
        if not text_value:
            continue

        prefix = f"[{float(paragraph.start):.2f}-{float(paragraph.end):.2f}] "
        available = max(500, max_chars - len(prefix) - 1)
        parts = [
            text_value[index:index + available].strip()
            for index in range(0, len(text_value), available)
        ]

        for part in parts:
            if not part:
                continue
            row = prefix + part
            row_chars = len(row) + 1
            if current and current_chars + row_chars > max_chars:
                chunks.append("\n".join(current))
                current = []
                current_chars = 0
            current.append(row)
            current_chars += row_chars

    if current:
        chunks.append("\n".join(current))

    return chunks


def detect_viral_segments_with_llm(
    paragraphs: List["TranscriptParagraph"],
    video_duration: float,
    cancel_event: threading.Event,
    limit: int,
) -> List[ViralSegment]:
    if not (
        _bot_ai_text_ready()
    ):
        return []

    candidates: List[ViralSegment] = []
    # Packaging-safe fallback: older releases accidentally shipped a call to
    # _transcript_prompt_chunks without the helper definition.  Never allow that
    # regression to take the whole Auto Clipper down again.
    chunk_builder = globals().get("_transcript_prompt_chunks")
    if callable(chunk_builder):
        chunks = chunk_builder(paragraphs)
    else:
        max_chars = max(4000, int(AUTO_CLIPPER_LLM_CHUNK_CHARS))
        rows = [
            f"[{float(p.start):.2f}-{float(p.end):.2f}] {normalize_transcript_text(str(p.text or ''))}"
            for p in paragraphs if normalize_transcript_text(str(p.text or ""))
        ]
        chunks, current, size = [], [], 0
        for row in rows:
            if current and size + len(row) + 1 > max_chars:
                chunks.append("\n".join(current)); current, size = [], 0
            current.append(row); size += len(row) + 1
        if current:
            chunks.append("\n".join(current))

    for chunk_index, chunk in enumerate(chunks):
        ensure_not_cancelled(cancel_event)
        prompt = f"""
Select up to 3 of the BEST viral moments from this timestamped transcript chunk.
You are choosing clips only; do not make editing/framing decisions.
The moments must work as standalone TikTok/Reels/Shorts clips.

Return ONLY a JSON array. Each item must contain:
start: number in seconds
end: number in seconds
score: integer 1-100 representing viral potential, not a guarantee
reason: short reason
hook: short content-specific hook headline, maximum 12 words
caption: engaging 1-2 line social caption
hashtags: array of 5-8 relevant hashtags

Prefer:
- surprising, controversial, emotional, funny, awkward, useful or quotable moments
- moments that make sense with limited surrounding context
- clean sentence boundaries
- strong opening statements and clear payoffs

Do not invent timestamps. Use only the timestamps below.

TRANSCRIPT CHUNK {chunk_index + 1}/{len(chunks)}:
{chunk}
""".strip()

        try:
            payload = _call_clipper_llm_sync(prompt)
        except Exception as exc:
            logger.warning(
                "Auto Clipper LLM chunk %d failed: %s",
                chunk_index + 1,
                exc,
            )
            continue

        if isinstance(payload, dict):
            payload = payload.get("segments") or payload.get("clips") or []
        if not isinstance(payload, list):
            continue

        for item in payload:
            if not isinstance(item, dict):
                continue
            segment = _normalize_viral_segment(
                item,
                paragraphs,
                video_duration,
            )
            if segment:
                candidates.append(segment)

    return _dedupe_viral_segments(candidates, limit)


def _text_viral_score(text_value: str) -> Tuple[float, List[str]]:
    text_lower = text_value.lower()
    score = 35.0
    reasons: List[str] = []

    emotional = {
        "shocking", "crazy", "insane", "unbelievable", "terrified", "afraid",
        "love", "hate", "angry", "cry", "cried", "death", "died", "secret",
        "truth", "never", "always", "worst", "best", "amazing", "surprise",
        "unexpected", "scared", "pain", "risk", "danger", "warning",
    }
    controversy = {
        "lie", "lied", "fraud", "fake", "scam", "illegal", "banned",
        "controversial", "government", "police", "hospital", "doctor",
        "court", "lawsuit", "exposed", "evidence", "proof", "mistake",
    }
    quotable = {
        "the reason", "the truth is", "what people don't", "nobody tells",
        "i realized", "i learned", "the biggest", "you have to", "you need to",
        "here's why", "this is why", "imagine", "listen", "believe me",
    }

    emotional_hits = sum(1 for word in emotional if word in text_lower)
    controversy_hits = sum(1 for word in controversy if word in text_lower)
    quotable_hits = sum(1 for phrase in quotable if phrase in text_lower)

    if emotional_hits:
        score += min(20, emotional_hits * 4)
        reasons.append("emotional language")
    if controversy_hits:
        score += min(18, controversy_hits * 4)
        reasons.append("high-stakes/controversial language")
    if quotable_hits:
        score += min(18, quotable_hits * 5)
        reasons.append("strong quotable phrasing")
    if "?" in text_value:
        score += 7
        reasons.append("question-driven curiosity")
    if "!" in text_value:
        score += 4
        reasons.append("high-energy delivery")
    if re.search(r"\b\d+(?:\.\d+)?%?\b", text_value):
        score += 4
        reasons.append("specific number/detail")
    word_count = len(text_value.split())
    if 30 <= word_count <= 180:
        score += 5

    return max(1.0, min(100.0, score)), reasons


def measure_audio_energy_score(
    source_path: Path,
    start: float,
    end: float,
    cancel_event: threading.Event,
) -> float:
    ensure_not_cancelled(cancel_event)
    duration = max(1.0, min(20.0, end - start))
    command = [
        "ffmpeg", "-hide_banner",
        "-ss", f"{start:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(source_path),
        "-vn",
        "-af", "volumedetect",
        "-f", "null",
        "-",
    ]

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except Exception:
        return 50.0

    match = re.search(
        r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB",
        result.stderr or "",
    )
    if not match:
        return 50.0

    max_db = float(match.group(1))
    # -30 dB => 0, -3 dB or louder => 100.
    return max(
        0.0,
        min(100.0, ((max_db + 30.0) / 27.0) * 100.0),
    )


def measure_visual_engagement_score(
    source_path: Path,
    start: float,
    end: float,
    cancel_event: threading.Event,
) -> float:
    """
    Lightweight multimodal visual score:
    motion/shot-change energy across sampled frames. This is used as one
    signal only; it is not a guarantee that a clip will go viral.
    """
    ensure_not_cancelled(cancel_event)
    try:
        import cv2  # type: ignore
        capture = cv2.VideoCapture(str(source_path))
        if not capture.isOpened():
            return 50.0

        duration = max(1.0, end - start)
        sample_count = 10
        previous = None
        changes: List[float] = []

        for index in range(sample_count):
            ensure_not_cancelled(cancel_event)
            position = start + duration * index / max(1, sample_count - 1)
            capture.set(cv2.CAP_PROP_POS_MSEC, position * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            frame = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if previous is not None:
                diff = float(cv2.absdiff(gray, previous).mean())
                changes.append(diff)
            previous = gray

        capture.release()
        if not changes:
            return 50.0

        mean_change = sum(changes) / len(changes)
        peak_change = max(changes)
        # Conversational clips usually sit in a useful band: not static,
        # but not chaotic. Reward moderate motion and meaningful cuts.
        motion = min(100.0, mean_change * 5.2)
        cuts = min(100.0, peak_change * 3.4)
        return max(0.0, min(100.0, motion * 0.62 + cuts * 0.38))
    except Exception:
        return 50.0


def pro_viral_potential_score(
    text_value: str,
    source_path: Path,
    start: float,
    end: float,
    cancel_event: threading.Event,
    base_hint: Optional[int] = None,
    channel_profile: Optional[Dict[str, Any]] = None,
) -> Tuple[int, str]:
    text_score, text_reasons = _text_viral_score(text_value)
    audio_score = measure_audio_energy_score(
        source_path, start, end, cancel_event
    )
    visual_score = measure_visual_engagement_score(
        source_path, start, end, cancel_event
    )

    duration = max(1.0, end - start)
    words = max(1, len(text_value.split()))
    words_per_second = words / duration
    speech_score = max(
        0.0,
        min(100.0, 100.0 - abs(words_per_second - 2.55) * 32.0),
    )

    local_score = (
        text_score * 0.52
        + audio_score * 0.20
        + visual_score * 0.18
        + speech_score * 0.10
    )
    if base_hint is not None and base_hint > 0:
        final = local_score * 0.55 + float(base_hint) * 0.45
    else:
        final = local_score

    # Private per-channel audience fit. This is intentionally NOT applied to
    # normal users or other Admin Channel profiles.
    channel_bonus = 0.0
    channel_reasons: List[str] = []
    if channel_profile:
        low = text_value.lower()
        reference_terms = set(channel_profile.get("viral_terms") or [])
        reference_phrases = set(channel_profile.get("viral_phrases") or [])
        term_hits = sum(1 for item in reference_terms if item and item in low)
        phrase_hits = sum(1 for item in reference_phrases if item and item in low)

        # Successful supplied content repeatedly uses personal testimony,
        # conflict/opinion, grief/trauma, social controversy and direct claims.
        if re.search(r"\b(i|i'm|i've|my|me|we|our)\b", low):
            channel_bonus += 4.0
            channel_reasons.append("personal-story fit")
        if any(x in low for x in (
            "police", "fight", "fought", "banned", "government", "immigrant",
            "immigration", "gender", "bullshit", "embarrassing", "wrong",
            "argument", "interview", "evidence", "truth",
        )):
            channel_bonus += 7.0
            channel_reasons.append("conflict/opinion fit")
        if any(x in low for x in (
            "died", "death", "passed away", "cry", "cried", "broke down",
            "scared", "terrified", "love you", "hate", "trauma", "childhood",
        )):
            channel_bonus += 8.0
            channel_reasons.append("high-emotion story")
        if any(x in low for x in (
            "do you know", "why", "what happened", "the truth", "right now",
            "you know", "if you", "i can't", "i remember",
        )):
            channel_bonus += 5.0
            channel_reasons.append("curiosity/conversation hook")
        if term_hits:
            channel_bonus += min(10.0, term_hits * 2.0)
        if phrase_hits:
            channel_bonus += min(10.0, phrase_hits * 3.0)

        # Channel fit helps ranking, but cannot turn weak material into 100.
        channel_bonus = min(22.0, channel_bonus)
        final = min(100.0, final + channel_bonus)

    reasons = list(text_reasons[:2])
    reasons.extend(channel_reasons[:2])
    if audio_score >= 68:
        reasons.append("strong vocal energy")
    if visual_score >= 62:
        reasons.append("good visual movement/cut energy")
    if speech_score >= 70:
        reasons.append("strong short-form speaking pace")
    if not reasons:
        reasons.append("balanced transcript, audio and visual signals")

    return max(1, min(100, int(round(final)))), ", ".join(reasons[:4])


def fallback_detect_viral_segments(
    paragraphs: List["TranscriptParagraph"],
    source_path: Path,
    video_duration: float,
    cancel_event: threading.Event,
    limit: int,
    channel_profile: Optional[Dict[str, Any]] = None,
) -> List[ViralSegment]:
    candidates: List[Tuple[float, float, float, str, List[str]]] = []

    for index, paragraph in enumerate(paragraphs):
        ensure_not_cancelled(cancel_event)
        start_index = max(0, index - 1)
        end_index = min(len(paragraphs), index + 2)
        group = paragraphs[start_index:end_index]
        start = group[0].start
        end = group[-1].end
        text_value = normalize_transcript_text(
            " ".join(item.text for item in group)
        )
        score, reasons = _text_viral_score(text_value)
        candidates.append((score, start, end, text_value, reasons))

    candidates.sort(key=lambda item: item[0], reverse=True)
    enriched: List[ViralSegment] = []

    # Audio analysis is only applied to the strongest text candidates.
    for score, start, end, text_value, reasons in candidates[:max(15, limit * 3)]:
        ensure_not_cancelled(cancel_event)
        final_score, pro_reason = pro_viral_potential_score(
            text_value,
            source_path,
            start,
            end,
            cancel_event,
            base_hint=int(round(score)),
            channel_profile=channel_profile,
        )
        reason_values = list(reasons)
        if pro_reason:
            reason_values.extend(
                part.strip()
                for part in pro_reason.split(",")
                if part.strip()
            )
        if not reason_values:
            reason_values.append("clear standalone quote")

        hook = generate_local_hook(text_value)
        caption, hashtags = generate_local_caption_and_hashtags(text_value)
        enriched.append(
            ViralSegment(
                start=max(0.0, start),
                end=min(video_duration, end),
                score=max(1, min(100, final_score)),
                reason=", ".join(reason_values[:3]),
                text=text_value,
                hook=hook,
                caption=caption,
                hashtags=hashtags,
            )
        )

    return _dedupe_viral_segments(enriched, limit)


def detect_viral_segments(
    paragraphs: List["TranscriptParagraph"],
    source_path: Path,
    cancel_event: threading.Event,
    limit: int = AUTO_CLIPPER_MAX_SEGMENTS,
    channel_profile: Optional[Dict[str, Any]] = None,
) -> List[ViralSegment]:
    media = probe_media(source_path)
    video_duration = float(media.get("duration") or 0)
    if video_duration <= 0 and paragraphs:
        video_duration = max(p.end for p in paragraphs)

    segments = detect_viral_segments_with_llm(
        paragraphs,
        video_duration,
        cancel_event,
        limit,
    )

    # LLM selection is useful for semantics, but the displayed viral score is
    # strengthened with local audio energy, visual-change energy and speech
    # pace so it is not based on text alone.
    for segment in segments:
        ensure_not_cancelled(cancel_event)
        score, local_reason = pro_viral_potential_score(
            segment.text,
            source_path,
            segment.start,
            segment.end,
            cancel_event,
            base_hint=segment.score,
            channel_profile=channel_profile,
        )
        segment.score = score
        if local_reason:
            existing = _clipper_clean_text(segment.reason, 160)
            segment.reason = _clipper_clean_text(
                f"{existing}; {local_reason}",
                220,
            )

    if len(segments) >= max(1, min(3, limit)):
        return segments[:limit]

    fallback = fallback_detect_viral_segments(
        paragraphs,
        source_path,
        video_duration,
        cancel_event,
        limit,
        channel_profile,
    )

    combined = _dedupe_viral_segments(
        segments + fallback,
        limit,
    )
    return combined


def expand_segment_to_duration(
    segment: ViralSegment,
    paragraphs: List["TranscriptParagraph"],
    target_seconds: int,
    video_duration: float,
) -> Tuple[float, float, str]:
    """
    Natural range-based clip selection.

    target_seconds == 60:
        choose a natural 30-60 second clip.
    target_seconds == 90:
        choose a natural 60-90 second clip.
    target_seconds == 120:
        choose a natural 90-180 second clip.
    target_seconds == 0:
        AUTO chooses the most suitable range from the viral core length.

    The clip is never padded to one exact duration. Real paragraph boundaries
    and the complete viral thought are preferred.
    """
    video_duration = max(0.0, float(video_duration))
    if video_duration <= 0:
        return 0.0, 0.0, segment.text

    core_start = max(0.0, min(float(segment.start), video_duration))
    core_end = max(core_start, min(float(segment.end), video_duration))
    core_duration = max(0.1, core_end - core_start)

    # AUTO: choose the most useful editing range from the viral core.
    if target_seconds == 0:
        if core_duration <= 48.0:
            min_len, max_len = 30.0, 60.0
        elif core_duration <= 82.0:
            min_len, max_len = 60.0, 90.0
        else:
            min_len, max_len = 90.0, 180.0
    elif target_seconds <= 60:
        min_len, max_len = 30.0, 60.0
    elif target_seconds <= 90:
        min_len, max_len = 60.0, 90.0
    else:
        min_len, max_len = 90.0, 180.0

    min_len = min(min_len, video_duration)
    max_len = min(max_len, video_duration)

    if max_len <= 0:
        return 0.0, 0.0, segment.text

    if max_len <= min_len + 0.1:
        start_value = max(0.0, min(core_start, video_duration - max_len))
        end_value = min(video_duration, start_value + max_len)
        return (
            start_value,
            end_value,
            _segment_text(paragraphs, start_value, end_value) or segment.text,
        )

    # Give a viral thought enough setup and payoff, but do not force the
    # maximum range. Longer cores naturally receive longer clips.
    natural_target = max(
        min_len,
        min(max_len, core_duration + max(10.0, core_duration * 0.22)),
    )

    starts = [0.0]
    ends = [video_duration]

    starts.extend(
        float(p.start)
        for p in paragraphs
        if p.start <= core_start + 0.25
        and p.start >= max(0.0, core_end - max_len)
    )
    ends.extend(
        float(p.end)
        for p in paragraphs
        if p.end >= core_end - 0.25
        and p.end <= min(video_duration, core_start + max_len)
    )

    starts = sorted(
        set(round(max(0.0, min(video_duration, value)), 3) for value in starts)
    )
    ends = sorted(
        set(round(max(0.0, min(video_duration, value)), 3) for value in ends)
    )

    candidates: List[Tuple[float, float, float]] = []
    core_mid = (core_start + core_end) / 2.0

    for start_value in starts:
        if start_value > core_start + 0.25:
            continue

        for end_value in ends:
            if end_value < core_end - 0.25 or end_value <= start_value:
                continue

            duration = end_value - start_value
            if duration < min_len - 0.35 or duration > max_len + 0.35:
                continue

            context_before = max(0.0, core_start - start_value)
            context_after = max(0.0, end_value - core_end)

            # Editorial scoring:
            # - preserve complete viral core,
            # - prefer natural paragraph boundaries,
            # - avoid an unnecessarily long intro before the hook,
            # - allow enough payoff after the viral moment.
            penalty = (
                abs(duration - natural_target) * 0.16
                + abs(((start_value + end_value) / 2.0) - core_mid) * 0.06
                + max(0.0, context_before - 14.0) * 0.48
                + max(0.0, context_after - 24.0) * 0.18
            )
            candidates.append((penalty, start_value, end_value))

    if candidates:
        _, start_value, end_value = min(
            candidates,
            key=lambda item: item[0],
        )
    else:
        duration = natural_target
        before = max(
            5.0,
            min(20.0, (duration - core_duration) * 0.40),
        )
        start_value = max(0.0, core_start - before)
        start_value = min(
            start_value,
            max(0.0, video_duration - duration),
        )
        end_value = min(video_duration, start_value + duration)

        if (
            end_value - start_value < min_len
            and video_duration >= min_len
        ):
            end_value = min(video_duration, start_value + min_len)
            start_value = max(0.0, end_value - min_len)

    return (
        start_value,
        end_value,
        _segment_text(paragraphs, start_value, end_value) or segment.text,
    )


def _planned_clip_is_duplicate(
    start: float,
    end: float,
    existing: List[Tuple[float, float]],
) -> bool:
    for old_start, old_end in existing:
        if (
            abs(start - old_start) <= 1.5
            and abs(end - old_end) <= 1.5
        ):
            return True
        if _clipper_overlap_ratio(
            start,
            end,
            old_start,
            old_end,
        ) >= AUTO_CLIPPER_DUPLICATE_OVERLAP:
            return True
    return False


def _deep_expand_from_hook(
    segment: ViralSegment,
    paragraphs: List["TranscriptParagraph"],
    duration_value: str,
    video_duration: float,
) -> Tuple[float, float, str]:
    """Preserve the research-selected opening so the first 5 seconds stay strong."""
    start = max(0.0, min(video_duration, float(segment.start)))
    end = max(start + 0.5, min(video_duration, float(segment.end)))
    bounds = {
        "60": (30.0, 60.0),
        "90": (60.0, 90.0),
        "120": (90.0, 180.0),
        "all": (12.0, 180.0),
    }
    min_len, max_len = bounds.get(duration_value, (12.0, 180.0))
    # Never prepend generic context before a Deep-mode hook. Extend forward only.
    target_end = min(video_duration, start + max_len)
    if end - start < min_len:
        for para in paragraphs:
            if para.end <= end:
                continue
            if para.start > target_end:
                break
            end = min(target_end, para.end)
            if end - start >= min_len:
                break
    minimum_end = min(video_duration, start + min_len)
    end = min(target_end, max(end, minimum_end))
    if end <= start:
        end = min(video_duration, start + max(1.0, min_len))
    return start, end, _segment_text(paragraphs, start, end) or segment.text


def plan_unique_auto_clips(
    segments: List[ViralSegment],
    paragraphs: List["TranscriptParagraph"],
    duration_values: List[str],
    video_duration: float,
) -> List[Tuple[ViralSegment, str, float, float, str]]:
    """
    Build a deduplicated rendering plan.

    Duplicate/near-identical clips are removed independently for each selected
    duration. "All durations" can still produce multiple durations from the
    same viral moment, but it will not repeatedly send the same range for the
    same duration.
    """
    plan: List[Tuple[ViralSegment, str, float, float, str]] = []

    for duration_value in duration_values:
        target_seconds = _clipper_duration_seconds(duration_value)
        if target_seconds is None:
            continue

        existing_ranges: List[Tuple[float, float]] = []
        for segment in sorted(
            segments,
            key=lambda item: item.score,
            reverse=True,
        ):
            start_value, end_value, clip_text = expand_segment_to_duration(
                segment,
                paragraphs,
                target_seconds,
                video_duration,
            )
            if end_value <= start_value:
                continue
            if _planned_clip_is_duplicate(
                start_value,
                end_value,
                existing_ranges,
            ):
                continue

            existing_ranges.append((start_value, end_value))
            plan.append(
                (
                    segment,
                    duration_value,
                    start_value,
                    end_value,
                    clip_text,
                )
            )

    return plan


def normalize_quality_choice(
    choice: Any,
) -> Dict[str, Any]:
    """
    Return a dict representation for a quality choice.

    Normal YouTube download jobs store choices as dictionaries, while some
    internal helpers may use the QualityChoice dataclass. Download helpers use
    dict-style .get(), so normalizing here prevents runtime type mismatches.
    """
    if isinstance(choice, dict):
        return choice

    if isinstance(choice, QualityChoice):
        return asdict(choice)

    try:
        payload = asdict(choice)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass

    raise TypeError(
        f"Unsupported quality choice type: {type(choice).__name__}"
    )


EDITOR_SOURCE_HEIGHT_LADDER: Tuple[int, ...] = (2160, 1440, 1080, 720, 480, 360, 240, 144)


def editor_source_format_selector(max_height: int = 2160) -> str:
    """Choose real YouTube source quality: 4K -> 2K -> 1080p -> lower."""
    ceiling = max(144, int(max_height or 2160))
    heights = [h for h in EDITOR_SOURCE_HEIGHT_LADDER if h <= ceiling] or [144]
    attempts: List[str] = []
    for h in heights:
        attempts.append(f"bestvideo[height={h}]+bestaudio")
        attempts.append(f"best[height={h}][vcodec!=none][acodec!=none]")
    attempts.append(f"bestvideo[height<={ceiling}]+bestaudio")
    attempts.append(f"best[height<={ceiling}][vcodec!=none][acodec!=none]")
    attempts.append("bestvideo+bestaudio/best")
    return "/".join(attempts)


def choose_clipper_quality(
    job: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Choose the preferred source quality for Auto Clipper.

    Auto Clipper passes the stored dictionary directly to download_video(),
    matching the same representation used by the normal YouTube download
    flow. This avoids converting it into QualityChoice and then calling .get()
    on the dataclass later.
    """
    raw_choices = job.get("choices") or {}
    choices: Dict[str, Dict[str, Any]] = {}

    for label, payload in raw_choices.items():
        try:
            normalized = normalize_quality_choice(payload)
            if normalized.get("selector"):
                choices[label] = normalized
        except Exception:
            continue

    # Always edit from the highest source YouTube exposes.
    # Final delivery quality can still be changed to 1080p/720p later.
    for label in (
        "4K",
        "2K",
        "1080p",
        "720p",
        "480p",
        "360p",
        "240p",
        "144p",
    ):
        if label in choices:
            return choices[label]

    if choices:
        return max(
            choices.values(),
            key=lambda choice: int(choice.get("height") or 0),
        )

    return None


async def get_clipper_transcript(
    job: Dict[str, Any],
    source_path: Path,
    progress_cb: ProgressCallback,
    cancel_event: threading.Event,
) -> Tuple[List["TranscriptParagraph"], str, List[Path]]:
    cleanup_files: List[Path] = []

    cached = get_cached_transcript(job)
    if cached:
        paragraphs, detected_language = cached
        await progress_cb("⚡ Using cached transcript for viral analysis...")
        return paragraphs, detected_language, cleanup_files

    build_lock = get_transcript_build_lock(job)
    async with build_lock:
        cached = get_cached_transcript(job)
        if cached:
            paragraphs, detected_language = cached
            await progress_cb("⚡ Using cached transcript for viral analysis...")
            return paragraphs, detected_language, cleanup_files

        paragraphs: List["TranscriptParagraph"] = []
        detected_language = ""
        if str(job.get("source_type") or "youtube") == "youtube":
            await progress_cb("🔎 Checking YouTube captions for fast analysis...")
            paragraphs, caption_path = await try_fast_youtube_captions(
                job,
                cancel_event,
            )
            detected_language = "English captions"
            if caption_path:
                cleanup_files.append(caption_path)

        if not paragraphs:
            await progress_cb(
                f"🎙 Running local {WHISPER_MODEL_NAME} transcript..."
            )
            paragraphs, detected_language = await asyncio.to_thread(
                run_transcription,
                source_path,
                cancel_event,
            )

        set_cached_transcript(
            job,
            paragraphs,
            detected_language,
        )
        return paragraphs, detected_language, cleanup_files


def _create_face_detector(
    cv2_module: Any,
) -> Tuple[Any, str]:
    """
    Prefer the official OpenCV YuNet DNN for podcast/interview face detection.

    V5.6 prefers the 2026 dynamic-input export on OpenCV 5.x and keeps the
    2023 model as a compatibility fallback for OpenCV 4.x. Haar is only a
    last-resort offline fallback.
    """
    model_dir = Path(__file__).resolve().parent / "models"
    try:
        cv_major = int(str(getattr(cv2_module, "__version__", "4")).split(".", 1)[0])
    except Exception:
        cv_major = 4

    preferred = [
        model_dir / ("face_detection_yunet_2026may.onnx" if cv_major >= 5 else "face_detection_yunet_2023mar.onnx"),
        model_dir / ("face_detection_yunet_2023mar.onnx" if cv_major >= 5 else "face_detection_yunet_2026may.onnx"),
    ]
    configured_model = Path(YUNET_MODEL_PATH)
    standard_yunet_names = {
        "face_detection_yunet_2023mar.onnx",
        "face_detection_yunet_2026may.onnx",
    }
    # Known OpenCV Zoo exports follow runtime compatibility order. A genuinely
    # custom path still gets first priority so an operator can override the model.
    candidates = (
        [*preferred, configured_model]
        if configured_model.name in standard_yunet_names
        else [configured_model, *preferred]
    ) + [
        Path("/root/editingbot_v78/models/face_detection_yunet_2026may.onnx"),
        Path("/root/editingbot_v78/models/face_detection_yunet_2023mar.onnx"),
        Path("/home/administrator/ytdlbot/models/face_detection_yunet_2026may.onnx"),
        Path("/home/administrator/ytdlbot/models/face_detection_yunet_2023mar.onnx"),
    ]
    seen = set()
    model_paths: List[Path] = []
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            model_paths.append(candidate)

    if hasattr(cv2_module, "FaceDetectorYN"):
        # Keep the network threshold slightly permissive; the row-level filter
        # below uses confidence + landmark geometry + face size to reject false
        # positives without losing genuine side/profile faces.
        network_threshold = max(0.50, min(0.56, SMART_REFRAME_MIN_FACE_SCORE - 0.04))
        for model_path in model_paths:
            if not model_path.exists() or model_path.stat().st_size <= 100000:
                continue
            try:
                detector = cv2_module.FaceDetectorYN.create(
                    str(model_path),
                    "",
                    (320, 320),
                    network_threshold,
                    0.3,
                    5000,
                )
                logger.info("Smart reframe face detector: YuNet %s", model_path.name)
                return detector, "yunet"
            except Exception as exc:
                logger.warning("YuNet face detector could not start from %s: %s", model_path, exc)

    # Production default: do NOT silently downgrade to Haar. If the YuNet model
    # is missing, a stable centre crop is safer than steering toward a microphone,
    # poster or background texture. Haar can still be explicitly enabled for an
    # offline emergency via SMART_REFRAME_ALLOW_HAAR_FALLBACK=1.
    if not SMART_REFRAME_ALLOW_HAAR_FALLBACK:
        raise RuntimeError("YuNet model unavailable; refusing unsafe Haar steering")

    cascade_root = Path(cv2_module.data.haarcascades)
    frontal = cv2_module.CascadeClassifier(str(cascade_root / "haarcascade_frontalface_default.xml"))
    profile = cv2_module.CascadeClassifier(str(cascade_root / "haarcascade_profileface.xml"))
    if frontal.empty() and profile.empty():
        raise RuntimeError("No usable face detector is available")
    logger.warning("Smart reframe is using Haar fallback; install the bundled YuNet model for production.")
    return (frontal, profile), "haar_multi"


def _yunet_row_is_plausible(
    row: Any,
    frame_width: int,
    frame_height: int,
) -> bool:
    """Reject low-confidence/non-face YuNet rows before they can steer a crop.

    YuNet returns bbox + five facial landmarks + confidence. A microphone or
    random texture can occasionally resemble a box, but it normally cannot also
    satisfy coherent eye/nose/mouth landmark geometry. Small background faces
    (posters/photos) are held to a higher confidence threshold.
    """
    try:
        if len(row) < 15 or frame_width <= 0 or frame_height <= 0:
            return False
        x, y, w, h = [float(v) for v in row[:4]]
        score = float(row[-1])
        if w <= 0.0 or h <= 0.0:
            return False
        area = (w * h) / max(1.0, float(frame_width * frame_height))
        min_score = float(SMART_REFRAME_MIN_FACE_SCORE)
        if area < 0.012:
            min_score = min(0.82, min_score + 0.07)
        elif area >= 0.030:
            min_score = max(0.55, min_score - 0.04)
        if score < min_score:
            return False

        pts = [
            (float(row[i]), float(row[i + 1]))
            for i in range(4, 14, 2)
        ]
        # Allow generous margins for profile faces while still rejecting wildly
        # inconsistent landmark layouts.
        margin_x = w * 0.28
        margin_y = h * 0.24
        inside = sum(
            1
            for px, py in pts
            if x - margin_x <= px <= x + w + margin_x
            and y - margin_y <= py <= y + h + margin_y
        )
        if inside < 4:
            return False

        right_eye, left_eye, nose, right_mouth, left_mouth = pts
        eye_y = (right_eye[1] + left_eye[1]) * 0.5
        mouth_y = (right_mouth[1] + left_mouth[1]) * 0.5
        if mouth_y <= eye_y + h * 0.055:
            return False
        if nose[1] < eye_y - h * 0.14 or nose[1] > mouth_y + h * 0.16:
            return False

        eye_span = abs(right_eye[0] - left_eye[0])
        mouth_span = abs(right_mouth[0] - left_mouth[0])
        if eye_span < w * 0.035 and mouth_span < w * 0.025:
            return False
        return True
    except Exception:
        return False


def _detect_faces_for_reframe(
    frame: Any,
    detector: Any,
    detector_kind: str,
    cv2_module: Any,
) -> List[Tuple[float, float, float]]:
    """
    Return (normalized_x_center, normalized_y_center, normalized_area).
    """
    if frame is None:
        return []

    height, width = frame.shape[:2]
    if width <= 0 or height <= 0:
        return []

    detect_max_dim = 640.0 if detector_kind == "yunet" else 360.0
    scale = min(1.0, detect_max_dim / max(width, height))
    if scale < 1.0:
        detect_frame = cv2_module.resize(
            frame,
            (
                max(2, int(round(width * scale))),
                max(2, int(round(height * scale))),
            ),
            interpolation=cv2_module.INTER_AREA,
        )
    else:
        detect_frame = frame

    dh, dw = detect_frame.shape[:2]
    faces_out: List[Tuple[float, float, float]] = []

    if detector_kind == "yunet":
        try:
            detector.setInputSize((dw, dh))
            _, faces = detector.detect(detect_frame)
        except Exception:
            faces = None

        if faces is not None:
            for row in faces:
                if not _yunet_row_is_plausible(row, dw, dh):
                    continue
                x, y, w, h = [float(value) for value in row[:4]]
                cx = (x + w / 2.0) / dw
                cy = (y + h / 2.0) / dh
                area = (w * h) / max(1.0, float(dw * dh))
                faces_out.append(
                    (
                        max(0.0, min(1.0, cx)),
                        max(0.0, min(1.0, cy)),
                        max(0.0, area),
                    )
                )
    else:
        gray = cv2_module.cvtColor(detect_frame, cv2_module.COLOR_BGR2GRAY)
        detectors = detector if isinstance(detector, (tuple, list)) else (detector, None)
        raw_faces: List[Tuple[int, int, int, int]] = []
        frontal, profile = detectors[0], (detectors[1] if len(detectors) > 1 else None)
        if frontal is not None and not frontal.empty():
            raw_faces.extend(tuple(map(int, box)) for box in frontal.detectMultiScale(
                gray, scaleFactor=1.08, minNeighbors=5, minSize=(32, 32)
            ))
        if profile is not None and not profile.empty():
            # Always include side-profile passes. A frontal false-positive in a
            # poster/background must not suppress the real podcast speaker.
            raw_faces.extend(tuple(map(int, box)) for box in profile.detectMultiScale(
                gray, scaleFactor=1.09, minNeighbors=4, minSize=(28, 28)
            ))
            flipped = cv2_module.flip(gray, 1)
            for x, y, w, h in profile.detectMultiScale(
                flipped, scaleFactor=1.09, minNeighbors=4, minSize=(28, 28)
            ):
                raw_faces.append((int(dw - (x + w)), int(y), int(w), int(h)))

        # Deduplicate frontal/profile hits of the same real person.
        raw_faces.sort(key=lambda b: b[2] * b[3], reverse=True)
        kept: List[Tuple[int, int, int, int]] = []
        for x, y, w, h in raw_faces:
            cx0, cy0 = x + w * 0.5, y + h * 0.5
            if any(
                abs(cx0 - (kx + kw * 0.5)) <= max(w, kw) * 0.35
                and abs(cy0 - (ky + kh * 0.5)) <= max(h, kh) * 0.35
                for kx, ky, kw, kh in kept
            ):
                continue
            kept.append((x, y, w, h))

        for x, y, w, h in kept:
            cx = (float(x) + float(w) / 2.0) / dw
            cy = (float(y) + float(h) / 2.0) / dh
            area = (float(w) * float(h)) / max(1.0, float(dw * dh))
            faces_out.append((
                max(0.0, min(1.0, cx)),
                max(0.0, min(1.0, cy)),
                max(0.0, area),
            ))

    faces_out.sort(key=lambda item: item[2], reverse=True)
    return faces_out[:4]



_DLIB_FACE_DETECTOR = None
_DLIB_FACE_DETECTOR_TRIED = False

def _dlib_edge_face_boxes(frame: Any, cv2_module: Any) -> List[Tuple[float, float, float, float, float, float, float]]:
    """High-resolution edge/profile face rescue with reflected side context.

    YuNet analysis intentionally runs on a compact frame, but a half-clipped
    side-profile can disappear at that resolution. This fallback must therefore
    receive the ORIGINAL frame. Two reflection widths are tried because HOG
    profile recovery is sensitive to where the reflected seam lands.
    """
    global _DLIB_FACE_DETECTOR, _DLIB_FACE_DETECTOR_TRIED
    if frame is None:
        return []
    if not _DLIB_FACE_DETECTOR_TRIED:
        _DLIB_FACE_DETECTOR_TRIED = True
        try:
            import dlib  # type: ignore
            _DLIB_FACE_DETECTOR = dlib.get_frontal_face_detector()
        except Exception:
            _DLIB_FACE_DETECTOR = None
    if _DLIB_FACE_DETECTOR is None:
        return []
    try:
        h, w = frame.shape[:2]
        if h < 48 or w < 48:
            return []
        out: List[Tuple[float, float, float, float, float, float, float]] = []
        pad_fractions = []
        for pf in (SMART_REFRAME_EDGE_FACE_PAD_FRACTION, 0.50, 0.60):
            pf = max(0.40, min(0.70, float(pf)))
            if not any(abs(pf - seen) < 0.015 for seen in pad_fractions):
                pad_fractions.append(pf)
        for pad_fraction in pad_fractions:
            pad = max(24, int(round(w * pad_fraction)))
            padded = cv2_module.copyMakeBorder(frame, 0, 0, pad, pad, cv2_module.BORDER_REFLECT_101)
            max_dim = float(SMART_REFRAME_EDGE_FACE_MAX_DIM)
            scale = min(1.0, max_dim / max(padded.shape[:2]))
            work = padded
            if scale < 0.999:
                work = cv2_module.resize(
                    padded,
                    (max(2, int(round(padded.shape[1] * scale))), max(2, int(round(padded.shape[0] * scale)))),
                    interpolation=cv2_module.INTER_AREA,
                )
            rgb = cv2_module.cvtColor(work, cv2_module.COLOR_BGR2RGB)
            rects = _DLIB_FACE_DETECTOR(rgb, 0)
            if not rects:
                rects = _DLIB_FACE_DETECTOR(rgb, 1)
            inv = 1.0 / max(1e-6, scale)
            for r in rects:
                x = float(r.left()) * inv - pad
                y = float(r.top()) * inv
                fw = float(r.width()) * inv
                fh = float(r.height()) * inv
                if fw <= 2 or fh <= 2:
                    continue
                vis_x0 = max(0.0, x)
                vis_x1 = min(float(w), x + fw)
                visible_w = max(0.0, vis_x1 - vis_x0)
                if visible_w < fw * 0.34:
                    continue
                predicted_cx = (x + fw * 0.5) / max(1.0, float(w))
                visible_cx = (vis_x0 + vis_x1) * 0.5 / max(1.0, float(w))
                visible_fraction = visible_w / max(1.0, fw)
                cx = visible_cx if visible_fraction < 0.72 else predicted_cx * 0.70 + visible_cx * 0.30
                cy = (y + fh * 0.5) / max(1.0, float(h))
                nw = visible_w / max(1.0, float(w))
                nh = min(float(h), y + fh) / max(1.0, float(h)) - max(0.0, y) / max(1.0, float(h))
                aspect = fw / max(1.0, fh)
                if not (0.52 <= aspect <= 1.58):
                    continue
                if cy > 0.66 or nh < 0.050:
                    continue
                nx = vis_x0 / max(1.0, float(w))
                ny = max(0.0, y) / max(1.0, float(h))
                area = max(0.0, nw * nh)
                candidate = (
                    max(-SMART_REFRAME_VIRTUAL_CENTER_MARGIN, min(1.0 + SMART_REFRAME_VIRTUAL_CENTER_MARGIN, cx)),
                    max(0.01, min(0.99, cy)), area, nx, ny, nw, nh,
                )
                if any(abs(candidate[0] - old[0]) < 0.06 and abs(candidate[1] - old[1]) < 0.06 for old in out):
                    continue
                out.append(candidate)
            if out:
                break
        out.sort(key=lambda b: float(b[2]), reverse=True)
        return out[:4]
    except Exception:
        return []

def _detect_face_boxes_for_reframe(
    frame: Any,
    detector: Any,
    detector_kind: str,
    cv2_module: Any,
) -> List[Tuple[float, float, float, float, float, float, float]]:
    """Return (cx, cy, area, x, y, w, h), all normalized to the source frame."""
    if frame is None:
        return []
    height, width = frame.shape[:2]
    if width <= 0 or height <= 0:
        return []

    detect_max_dim = 640.0 if detector_kind == "yunet" else 360.0
    scale = min(1.0, detect_max_dim / max(width, height))
    if scale < 1.0:
        detect_frame = cv2_module.resize(
            frame,
            (max(2, int(round(width * scale))), max(2, int(round(height * scale)))),
            interpolation=cv2_module.INTER_AREA,
        )
    else:
        detect_frame = frame

    dh, dw = detect_frame.shape[:2]
    boxes: List[Tuple[float, float, float, float, float, float, float]] = []

    if detector_kind == "yunet":
        def collect_yunet(image: Any, source_offset_x: int = 0, source_width: Optional[int] = None) -> None:
            ih, iw = image.shape[:2]
            try:
                detector.setInputSize((iw, ih))
                _, faces_local = detector.detect(image)
            except Exception:
                faces_local = None
            if faces_local is None:
                return
            base_w = int(source_width or iw)
            for row in faces_local:
                if not _yunet_row_is_plausible(row, iw, ih):
                    continue
                x, y, w, h = [float(value) for value in row[:4]]
                x0 = x - float(source_offset_x)
                x1 = x0 + w
                # For an edge-rescue detection, require a meaningful visible
                # intersection with the real source; reflected padding is only
                # context, never a new person.
                vis_x0 = max(0.0, x0)
                vis_x1 = min(float(base_w), x1)
                visible_w = max(0.0, vis_x1 - vis_x0)
                if visible_w < w * 0.42:
                    continue
                nx = vis_x0 / max(1.0, float(base_w))
                ny = max(0.0, y) / max(1.0, float(ih))
                nw = visible_w / max(1.0, float(base_w))
                nh = min(float(ih), y + h) / max(1.0, float(ih)) - ny
                predicted_cx = (x0 + w * 0.5) / max(1.0, float(base_w))
                visible_cx = (vis_x0 + vis_x1) * 0.5 / max(1.0, float(base_w))
                visible_fraction = visible_w / max(1.0, w)
                # If most of the head is already outside the source, centring the
                # invisible predicted half makes the remaining visible face look
                # displaced. Centre the visible face for severe clipping; retain
                # predicted-head composition for mild clipping.
                if visible_fraction < 0.72:
                    cx_value = visible_cx
                else:
                    cx_value = predicted_cx * 0.70 + visible_cx * 0.30
                cx = max(-SMART_REFRAME_VIRTUAL_CENTER_MARGIN, min(1.0 + SMART_REFRAME_VIRTUAL_CENTER_MARGIN, cx_value))
                cy = max(0.01, min(0.99, (y + h * 0.5) / max(1.0, float(ih))))
                area = max(0.0, nw * nh)
                if nw > 0.0 and nh > 0.0:
                    boxes.append((cx, cy, area, nx, ny, nw, nh))

        collect_yunet(detect_frame)
        # Edge rescue: YuNet can miss a genuine speaker when the source camera
        # already cuts part of the head at x=0/x=W. Re-run only on a miss with
        # reflected side context, then map the predicted face back to source
        # coordinates. This remains YuNet/landmark validated, not Haar steering.
        if not boxes:
            pad = max(12, int(round(dw * 0.18)))
            try:
                padded = cv2_module.copyMakeBorder(
                    detect_frame, 0, 0, pad, pad, cv2_module.BORDER_REFLECT_101
                )
                collect_yunet(padded, source_offset_x=pad, source_width=dw)
            except Exception:
                pass

        # V7.4 second detector: dlib HOG with reflection padding is especially
        # useful for faces already clipped by the source edge/profile camera.
        if not boxes:
            boxes.extend(_dlib_edge_face_boxes(frame, cv2_module))

        # Deduplicate normal + rescued rows.
        boxes.sort(key=lambda item: item[2], reverse=True)
        deduped = []
        for b in boxes:
            if any(abs(float(b[0]) - float(k[0])) < 0.055 and abs(float(b[1]) - float(k[1])) < 0.055 for k in deduped):
                continue
            deduped.append(b)
        boxes[:] = deduped[:4]
    else:
        gray = cv2_module.cvtColor(detect_frame, cv2_module.COLOR_BGR2GRAY)
        detectors = detector if isinstance(detector, (tuple, list)) else (detector, None)
        raw_faces: List[Tuple[int, int, int, int]] = []
        frontal, profile = detectors[0], (detectors[1] if len(detectors) > 1 else None)
        if frontal is not None and not frontal.empty():
            raw_faces.extend(tuple(map(int, box)) for box in frontal.detectMultiScale(
                gray, scaleFactor=1.08, minNeighbors=5, minSize=(32, 32)
            ))
        if profile is not None and not profile.empty():
            raw_faces.extend(tuple(map(int, box)) for box in profile.detectMultiScale(
                gray, scaleFactor=1.09, minNeighbors=4, minSize=(28, 28)
            ))
            flipped = cv2_module.flip(gray, 1)
            for x, y, w, h in profile.detectMultiScale(
                flipped, scaleFactor=1.09, minNeighbors=4, minSize=(28, 28)
            ):
                raw_faces.append((int(dw - (x + w)), int(y), int(w), int(h)))

        raw_faces.sort(key=lambda b: b[2] * b[3], reverse=True)
        kept: List[Tuple[int, int, int, int]] = []
        for x, y, w, h in raw_faces:
            cx0, cy0 = x + w * 0.5, y + h * 0.5
            if any(
                abs(cx0 - (kx + kw * 0.5)) <= max(w, kw) * 0.35
                and abs(cy0 - (ky + kh * 0.5)) <= max(h, kh) * 0.35
                for kx, ky, kw, kh in kept
            ):
                continue
            kept.append((x, y, w, h))

        for x, y, w, h in kept:
            nx, ny, nw, nh = float(x) / dw, float(y) / dh, float(w) / dw, float(h) / dh
            cx, cy = nx + nw / 2.0, ny + nh / 2.0
            boxes.append((cx, cy, nw * nh, nx, ny, nw, nh))
        # Fast reflected-edge rescue for Haar fallback.  Use the compact frame so
        # a half-clipped profile can be recovered without invoking slow dlib on
        # every detector miss during dense tracking.
        if not boxes:
            try:
                pad = max(18, int(round(dw * 0.40)))
                padded_gray = cv2_module.copyMakeBorder(gray, 0, 0, pad, pad, cv2_module.BORDER_REFLECT_101)
                rescued: List[Tuple[int, int, int, int]] = []
                if frontal is not None and not frontal.empty():
                    for x, y, fw, fh in frontal.detectMultiScale(padded_gray, scaleFactor=1.06, minNeighbors=4, minSize=(28, 28)):
                        rescued.append((int(x - pad), int(y), int(fw), int(fh)))
                if profile is not None and not profile.empty():
                    for x, y, fw, fh in profile.detectMultiScale(padded_gray, scaleFactor=1.06, minNeighbors=3, minSize=(24, 24)):
                        rescued.append((int(x - pad), int(y), int(fw), int(fh)))
                    flipped_pad = cv2_module.flip(padded_gray, 1)
                    for x, y, fw, fh in profile.detectMultiScale(flipped_pad, scaleFactor=1.06, minNeighbors=3, minSize=(24, 24)):
                        rescued.append((int(padded_gray.shape[1] - (x + fw) - pad), int(y), int(fw), int(fh)))
                for x, y, fw, fh in sorted(rescued, key=lambda b: b[2] * b[3], reverse=True):
                    vis_x0 = max(0, x); vis_x1 = min(dw, x + fw)
                    visible_w = max(0, vis_x1 - vis_x0)
                    if visible_w < fw * 0.34:
                        continue
                    predicted_cx = (x + fw * 0.5) / max(1.0, float(dw))
                    visible_cx = (vis_x0 + vis_x1) * 0.5 / max(1.0, float(dw))
                    visible_fraction = visible_w / max(1.0, float(fw))
                    cx = visible_cx if visible_fraction < 0.72 else predicted_cx * 0.70 + visible_cx * 0.30
                    nx = vis_x0 / max(1.0, float(dw)); ny = max(0, y) / max(1.0, float(dh))
                    nw = visible_w / max(1.0, float(dw)); nh = max(0, min(dh, y + fh) - max(0, y)) / max(1.0, float(dh))
                    cy = (max(0, y) + max(0, min(dh, y + fh) - max(0, y)) * 0.5) / max(1.0, float(dh))
                    if nh >= 0.045 and cy <= 0.68:
                        boxes.append((max(-SMART_REFRAME_VIRTUAL_CENTER_MARGIN, min(1.0 + SMART_REFRAME_VIRTUAL_CENTER_MARGIN, cx)), cy, nw * nh, nx, ny, nw, nh))
                        break
            except Exception:
                pass

    boxes.sort(key=lambda item: item[2], reverse=True)
    return boxes[:4]


def _create_upper_body_detector(cv2_module: Any) -> Optional[Any]:
    """Return a local upper-body detector; face identity always remains primary."""
    try:
        cascade = cv2_module.CascadeClassifier(
            str(Path(cv2_module.data.haarcascades) / "haarcascade_upperbody.xml")
        )
        return None if cascade.empty() else cascade
    except Exception:
        return None


def _create_fast_subject_tracker(cv2_module: Any) -> Optional[Any]:
    """Prefer a fast upper-body appearance tracker; optical flow is fallback.

    KCF is available in opencv-contrib (the production requirement) and is much
    lighter than MIL on a CPU VPS.  MIL can take seconds per update on some
    headless builds, creating apparent camera lag even when the coordinates are
    correct.  If KCF is unavailable we simply keep the face-gated LK/body-motion
    tracker rather than stalling the render pipeline.
    """
    factories = []
    direct = getattr(cv2_module, "TrackerKCF_create", None)
    if callable(direct):
        factories.append(direct)
    legacy = getattr(cv2_module, "legacy", None)
    legacy_kcf = getattr(legacy, "TrackerKCF_create", None) if legacy is not None else None
    if callable(legacy_kcf):
        factories.append(legacy_kcf)
    for factory in factories:
        try:
            return factory()
        except Exception:
            continue
    return None


def _detect_upper_body_for_face(
    frame: Any,
    face: Tuple[float, float, float, float, float, float, float],
    detector: Optional[Any],
    cv2_module: Any,
) -> Optional[Tuple[float, float, float, float, float, float]]:
    """Return a body box only when it contains the already-verified face.

    This face-gating prevents microphones, chairs, photos and empty background
    regions from becoming camera subjects.
    """
    if detector is None or frame is None:
        return None
    try:
        h, w = frame.shape[:2]
        if w < 32 or h < 32:
            return None
        _, _, _, fnx, fny, fnw, fnh = face
        fcx = (float(fnx) + float(fnw) * 0.5) * w
        fcy = (float(fny) + float(fnh) * 0.5) * h
        gray = cv2_module.cvtColor(frame, cv2_module.COLOR_BGR2GRAY)
        boxes = detector.detectMultiScale(
            gray, scaleFactor=1.08, minNeighbors=3,
            minSize=(max(24, int(float(fnw) * w * 1.10)),
                     max(28, int(float(fnh) * h * 1.25))),
        )
        candidates = []
        for x, y, bw, bh in boxes:
            x, y, bw, bh = map(float, (x, y, bw, bh))
            contains_face = (
                x - bw * 0.06 <= fcx <= x + bw * 1.06
                and y - bh * 0.08 <= fcy <= y + bh * 0.72
            )
            extends_below = y + bh >= (float(fny) + float(fnh) * 1.45) * h
            if not contains_face or not extends_below:
                continue
            cx = (x + bw * 0.5) / w
            cy = (y + bh * 0.5) / h
            score = (
                abs(cx - float(face[0])) * 2.0
                + abs(y / h - float(fny)) * 0.6
                - min(0.25, (bw * bh) / (w * h))
            )
            candidates.append((score, cx, cy, x / w, y / h, bw / w, bh / h))
        if not candidates:
            return None
        _, cx, cy, nx, ny, nw, nh = min(candidates, key=lambda item: item[0])
        return (
            max(0.01, min(0.99, cx)), max(0.05, min(0.95, cy)),
            max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny)),
            max(0.01, min(1.0, nw)), max(0.01, min(1.0, nh)),
        )
    except Exception:
        return None


def _speaker_visual_signature(
    frame: Any,
    face: Tuple[float, float, float, float, float, float, float],
    cv2_module: Any,
) -> Optional[Tuple[float, ...]]:
    """Lightweight local appearance descriptor for duplicate/identity checks.

    It combines contrast-normalized low-frequency face structure with HSV face
    and upper-torso histograms. This is not a biometric recognition system; it
    is a conservative edit-time guard against duplicating the same visible
    person and a stronger cross-camera cue than a single mean hue value.
    """
    try:
        import numpy as np  # type: ignore
        h, w = frame.shape[:2]
        _, _, _, nx, ny, nw, nh = face
        x1 = max(0, int(round(nx * w))); x2 = min(w, int(round((nx + nw) * w)))
        y1 = max(0, int(round(ny * h))); y2 = min(h, int(round((ny + nh) * h)))
        face_roi = frame[y1:y2, x1:x2]
        if getattr(face_roi, "size", 0) <= 0:
            return None
        gray = cv2_module.cvtColor(face_roi, cv2_module.COLOR_BGR2GRAY)
        gray = cv2_module.resize(gray, (24, 24), interpolation=cv2_module.INTER_AREA)
        gray = cv2_module.equalizeHist(gray)
        dct = cv2_module.dct(gray.astype("float32") / 255.0)[:8, :8].reshape(-1)[1:]
        dct = dct / max(1e-6, float(np.linalg.norm(dct)))

        hsv = cv2_module.cvtColor(
            cv2_module.resize(face_roi, (48, 48), interpolation=cv2_module.INTER_AREA),
            cv2_module.COLOR_BGR2HSV,
        )
        fhist = cv2_module.calcHist([hsv], [0, 1], None, [12, 4], [0, 180, 0, 256]).reshape(-1).astype("float32")
        fhist = fhist / max(1e-6, float(np.linalg.norm(fhist)))

        tx1 = max(0, int(round((nx - nw * 0.40) * w)))
        tx2 = min(w, int(round((nx + nw * 1.40) * w)))
        ty1 = max(0, int(round((ny + nh * 0.80) * h)))
        ty2 = min(h, int(round((ny + nh * 3.20) * h)))
        torso = frame[ty1:ty2, tx1:tx2]
        if getattr(torso, "size", 0) > 0:
            thsv = cv2_module.cvtColor(
                cv2_module.resize(torso, (48, 48), interpolation=cv2_module.INTER_AREA),
                cv2_module.COLOR_BGR2HSV,
            )
            thist = cv2_module.calcHist([thsv], [0, 1], None, [12, 4], [0, 180, 0, 256]).reshape(-1).astype("float32")
            thist = thist / max(1e-6, float(np.linalg.norm(thist)))
        else:
            thist = np.zeros(48, dtype="float32")
        vector = np.concatenate([dct * 0.70, fhist * 0.50, thist * 0.45]).astype("float32")
        return tuple(float(v) for v in vector)
    except Exception:
        return None


def _speaker_signature_distance(a: Optional[Tuple[float, ...]], b: Optional[Tuple[float, ...]]) -> float:
    if not a or not b or len(a) != len(b):
        return 1.0
    try:
        return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)) / max(1, len(a)))
    except Exception:
        return 1.0


def _cluster_two_speaker_signatures(
    samples: List[Tuple[float, float, float, float, Tuple[float, ...]]],
) -> Optional[Tuple[List[Tuple[float, float, float, float, Tuple[float, ...]]], List[Tuple[float, float, float, float, Tuple[float, ...]]], float, float]]:
    """Conservative two-identity clustering for A/B camera-cut podcasts."""
    if len(samples) < 8:
        return None
    vectors = [row[4] for row in samples if row[4]]
    if len(vectors) != len(samples) or not vectors:
        return None
    # Farthest-pair initialization, then a few deterministic 2-means steps.
    far_i, far_j, far_d = 0, 1, -1.0
    for i in range(len(samples)):
        for j in range(i + 1, len(samples)):
            d = _speaker_signature_distance(samples[i][4], samples[j][4])
            if d > far_d:
                far_i, far_j, far_d = i, j, d
    c1 = list(samples[far_i][4]); c2 = list(samples[far_j][4])
    labels = [0] * len(samples)
    for _ in range(8):
        new_labels = []
        for row in samples:
            sig = row[4]
            d1 = math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(sig, c1)) / max(1, len(c1)))
            d2 = math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(sig, c2)) / max(1, len(c2)))
            new_labels.append(0 if d1 <= d2 else 1)
        if 0 not in new_labels or 1 not in new_labels:
            return None
        labels = new_labels
        for label in (0, 1):
            members = [samples[i][4] for i, lab in enumerate(labels) if lab == label]
            centroid = [sum(float(v[k]) for v in members) / len(members) for k in range(len(members[0]))]
            if label == 0:
                c1 = centroid
            else:
                c2 = centroid
    a = [row for row, lab in zip(samples, labels) if lab == 0]
    b = [row for row, lab in zip(samples, labels) if lab == 1]
    if len(a) < 3 or len(b) < 3:
        return None
    between = math.sqrt(sum((x - y) ** 2 for x, y in zip(c1, c2)) / max(1, len(c1)))
    within = []
    for row, lab in zip(samples, labels):
        c = c1 if lab == 0 else c2
        within.append(math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(row[4], c)) / max(1, len(c))))
    within_med = float(statistics.median(within)) if within else 0.0
    # A single person's pose/lighting often forms weak sub-clusters. Require
    # strong separation relative to that within-person variation before calling
    # the shots two different speakers.
    if between < 0.035 or between < max(0.035, within_med * 2.15):
        return None
    return a, b, between, within_med



def _cluster_two_speaker_shots(
    samples: List[Tuple[float, float, float, float, Tuple[float, ...]]],
    scene_cuts: List[float],
) -> Optional[Tuple[List[Tuple[float, float, float, float, Tuple[float, ...]]], List[Tuple[float, float, float, float, Tuple[float, ...]]], float, float]]:
    """Cluster A/B camera podcast identities at SHOT level, not raw frames.

    Frame-level appearance varies a lot with profile angle, expression and motion.
    Aggregating each hard-cut shot first removes that noise and makes a genuine
    alternating-camera conversation much easier to distinguish without inventing
    a second person from one speaker's pose changes.  Scene cuts are mandatory.
    """
    if len(samples) < 6 or not scene_cuts:
        return None
    ordered = sorted(samples, key=lambda row: float(row[0]))
    cuts = sorted(float(c) for c in scene_cuts if math.isfinite(float(c)))
    if not cuts:
        return None

    # Assign each identity sample to the hard-cut shot that contains it.
    shot_rows: List[List[Tuple[float, float, float, float, Tuple[float, ...]]]] = []
    current: List[Tuple[float, float, float, float, Tuple[float, ...]]] = []
    cut_index = 0
    for row in ordered:
        t = float(row[0])
        while cut_index < len(cuts) and t >= cuts[cut_index]:
            if current:
                shot_rows.append(current)
                current = []
            cut_index += 1
        current.append(row)
    if current:
        shot_rows.append(current)

    stable: List[Tuple[List[Tuple[float, float, float, float, Tuple[float, ...]]], Tuple[float, ...], float]] = []
    for rows in shot_rows:
        valid = [r for r in rows if r[4]]
        if len(valid) < 2:
            continue
        span = float(valid[-1][0]) - float(valid[0][0])
        # A couple of detector samples across ~0.12 s is enough to identify a
        # shot; the moving-panel extractor separately requires a longer run.
        if span < 0.10 and len(valid) < 3:
            continue
        dim = len(valid[0][4])
        if dim <= 0 or any(len(r[4]) != dim for r in valid):
            continue
        centroid = tuple(
            sum(float(r[4][k]) for r in valid) / len(valid)
            for k in range(dim)
        )
        stable.append((valid, centroid, max(span, 0.001)))
    if len(stable) < 2:
        return None

    # Farthest shot-pair initialization followed by weighted two-means.  Using
    # shot centroids prevents one person's changing expression from dominating.
    far_i, far_j, far_d = 0, 1, -1.0
    for i in range(len(stable)):
        for j in range(i + 1, len(stable)):
            d = _speaker_signature_distance(stable[i][1], stable[j][1])
            if d > far_d:
                far_i, far_j, far_d = i, j, d
    c1 = list(stable[far_i][1])
    c2 = list(stable[far_j][1])
    labels = [0] * len(stable)
    for _ in range(10):
        new_labels: List[int] = []
        for _, sig, _ in stable:
            d1 = _speaker_signature_distance(sig, tuple(c1))
            d2 = _speaker_signature_distance(sig, tuple(c2))
            new_labels.append(0 if d1 <= d2 else 1)
        if 0 not in new_labels or 1 not in new_labels:
            return None
        labels = new_labels
        for label in (0, 1):
            members = [stable[i] for i, lab in enumerate(labels) if lab == label]
            total_w = sum(max(0.25, item[2]) for item in members)
            centroid = []
            for k in range(len(members[0][1])):
                centroid.append(
                    sum(float(item[1][k]) * max(0.25, item[2]) for item in members)
                    / max(1e-6, total_w)
                )
            if label == 0:
                c1 = centroid
            else:
                c2 = centroid

    between = _speaker_signature_distance(tuple(c1), tuple(c2))
    within: List[float] = []
    for (_, sig, _), lab in zip(stable, labels):
        within.append(_speaker_signature_distance(sig, tuple(c1 if lab == 0 else c2)))
    within_med = float(statistics.median(within)) if within else 0.0

    # Duplicate guard.  Shot aggregation lets us use a lower absolute threshold
    # than frame-level clustering, but separation still has to beat within-person
    # variation by a useful margin.  Two-shot conversations need a slightly
    # stronger absolute distinction because there is no recurrence evidence.
    minimum_between = 0.030 if len(stable) == 2 else 0.022
    if between < minimum_between or between < max(minimum_between, within_med * 1.35 + 0.004):
        return None

    cluster_a: List[Tuple[float, float, float, float, Tuple[float, ...]]] = []
    cluster_b: List[Tuple[float, float, float, float, Tuple[float, ...]]] = []
    for (rows, _, _), lab in zip(stable, labels):
        (cluster_a if lab == 0 else cluster_b).extend(rows)
    if len(cluster_a) < 3 or len(cluster_b) < 3:
        return None
    return cluster_a, cluster_b, float(between), float(within_med)


def _mouth_roi_for_face(
    frame: Any,
    face: Tuple[float, float, float, float, float, float, float],
    cv2_module: Any,
) -> Optional[Any]:
    """Extract a normalized lower-face ROI used as a lightweight speaking cue."""
    height, width = frame.shape[:2]
    _, _, _, nx, ny, nw, nh = face
    x1 = int(max(0, min(width - 1, (nx + nw * 0.18) * width)))
    x2 = int(max(x1 + 2, min(width, (nx + nw * 0.82) * width)))
    y1 = int(max(0, min(height - 1, (ny + nh * 0.55) * height)))
    y2 = int(max(y1 + 2, min(height, (ny + nh * 0.92) * height)))
    roi = frame[y1:y2, x1:x2]
    if roi is None or getattr(roi, "size", 0) <= 0:
        return None
    gray = cv2_module.cvtColor(roi, cv2_module.COLOR_BGR2GRAY)
    return cv2_module.resize(gray, (64, 32), interpolation=cv2_module.INTER_AREA)


def _mouth_motion_score(previous: Optional[Any], current: Optional[Any], cv2_module: Any) -> float:
    if previous is None or current is None:
        return 0.0
    try:
        diff = cv2_module.absdiff(previous, current)
        return float(diff.mean()) / 255.0
    except Exception:
        return 0.0


def _simultaneous_speech_confidence(left_activity: float, right_activity: float) -> float:
    """Return dual-speaker confidence only when BOTH visible faces are active.

    A strong mouth motion on one side must never be enough to open the 50/50
    split. The weaker side therefore has its own floor and the pair must have a
    reasonable activity balance. This is intentionally stricter than the
    single-speaker switch detector.
    """
    lm = max(0.0, float(left_activity))
    rm = max(0.0, float(right_activity))
    threshold = max(1e-6, float(SMART_REFRAME_MOUTH_ACTIVITY_THRESHOLD))
    peak = max(lm, rm)
    weak = min(lm, rm)
    if peak <= 0.0:
        return 0.0
    balance = weak / peak
    combined = lm + rm
    if weak < threshold * 0.50:
        return 0.0
    if balance < 0.28:
        return 0.0
    if combined < threshold * 1.30:
        return 0.0
    strength_score = min(0.65, combined / (threshold * 4.0))
    balance_score = min(0.35, balance * 0.42)
    return min(1.0, strength_score + balance_score)


def _dual_times_to_intervals(times: List[float], clip_duration: float) -> List[Tuple[float, float]]:
    """Build real two-speaker split windows from sustained simultaneous speech.

    A split starts at the first trustworthy overlap sample, stays on screen for at
    least four seconds, and naturally extends while both speakers keep talking.
    Isolated one-frame mouth/noise spikes are ignored.
    """
    if not times:
        return []
    times = sorted(float(t) for t in times)
    # Detector samples can momentarily miss a profile mouth. A short gap is still
    # one continuous simultaneous-speech event, but a long gap starts a new event.
    max_gap = max(0.22, min(0.45, SMART_REFRAME_SAMPLE_INTERVAL_SECONDS * 10.0))
    groups: List[List[float]] = [[times[0]]]
    for value in times[1:]:
        if value - groups[-1][-1] <= max_gap:
            groups[-1].append(value)
        else:
            groups.append([value])

    intervals: List[Tuple[float, float]] = []
    minimum = min(4.0, clip_duration)
    for group in groups:
        # Require persistence. At the default 15fps detector cadence this is only
        # ~0.13s, so real overlap triggers quickly without false split flashes.
        if len(group) < 3 or (group[-1] - group[0]) < 0.09:
            continue
        start_t = max(0.0, group[0])
        if clip_duration >= minimum and start_t + minimum > clip_duration:
            start_t = max(0.0, clip_duration - minimum)
        # Keep a little post-roll so tiny detector gaps do not visibly flicker the
        # split. Long simultaneous talk simply keeps extending this end time.
        end_t = min(clip_duration, max(group[-1] + 0.40, start_t + minimum))
        if intervals and start_t <= intervals[-1][1] + 0.45:
            intervals[-1] = (intervals[-1][0], max(intervals[-1][1], end_t))
        else:
            intervals.append((start_t, end_t))
    return intervals[:120]


def _group_visibility_times(times: List[float]) -> List[Tuple[float, float]]:
    if not times:
        return []
    ordered = sorted(float(t) for t in times)
    max_gap = max(0.10, SMART_REFRAME_SAMPLE_INTERVAL_SECONDS * 3.5)
    groups: List[Tuple[float, float]] = []
    start_t = last_t = ordered[0]
    for value in ordered[1:]:
        if value - last_t <= max_gap:
            last_t = value
            continue
        groups.append((start_t, last_t))
        start_t = last_t = value
    groups.append((start_t, last_t))
    return groups


def _dual_visibility_to_reference_intervals(
    times: List[float], clip_duration: float
) -> List[Tuple[float, float]]:
    """Conservative visual fallback for a sustained genuine two-face shot.

    Speech/mouth evidence is still the primary trigger.  If that evidence is weak,
    this fallback creates a reference-style top/bottom window of at least four
    seconds, without turning an entire two-person podcast into a permanent split.
    """
    out: List[Tuple[float, float]] = []
    for start_t, end_t in _group_visibility_times(times):
        run_len = end_t - start_t
        if run_len < 0.65:
            continue
        cursor = start_t + min(0.10, run_len * 0.05)
        while cursor < end_t + 0.05:
            finish = min(clip_duration, max(cursor + 4.35, min(end_t + 0.20, cursor + 7.00)))
            if finish - cursor >= min(4.0, max(0.0, clip_duration - cursor)):
                out.append((max(0.0, cursor), finish))
            cursor += 14.0
    return out[:40]



def _split_director_select_intervals(
    dual_evidence: List[Tuple[float, float]],
    dual_visibility_times: List[float],
    scene_cuts: List[float],
    clip_duration: float,
) -> List[Tuple[float, float]]:
    """Scene-aware same-timeline podcast split director.

    Editorial invariant: a top/bottom split is allowed ONLY while two distinct
    speakers are simultaneously visible in the SAME source frames.  This function
    never creates cross-camera/reference splits and never borrows another timestamp.

    The director scores sustained two-face runs, keeps a small safety margin from
    hard cuts, requires >=4 seconds of continuous genuine dual visibility, prefers
    4.4-6.8 second windows, and adds hysteresis so split does not flicker or repeat
    too frequently.
    """
    duration = max(0.0, float(clip_duration))
    if duration < SMART_REFRAME_DUAL_MIN_SECONDS or not dual_visibility_times:
        return []

    visibility = sorted(
        max(0.0, min(duration, float(t)))
        for t in dual_visibility_times
        if math.isfinite(float(t))
    )
    evidence = sorted(
        (max(0.0, min(duration, float(t))), max(0.0, min(1.0, float(c))))
        for t, c in dual_evidence
        if math.isfinite(float(t)) and math.isfinite(float(c))
    )
    cuts = sorted({
        max(0.0, min(duration, float(c)))
        for c in scene_cuts
        if 0.05 < float(c) < duration - 0.05 and math.isfinite(float(c))
    })

    # Detector cadence can fluctuate.  Allow a few missed samples, but do not bridge
    # a real shot/camera change.  A split must represent one continuous two-person shot.
    sample_gap = max(0.12, min(0.30, SMART_REFRAME_SAMPLE_INTERVAL_SECONDS * 6.5))
    groups: List[Tuple[float, float, List[float]]] = []
    start = last = visibility[0]
    rows = [visibility[0]]
    for t in visibility[1:]:
        crossed_cut = any(last + 0.025 < c < t - 0.025 for c in cuts)
        if (t - last) <= sample_gap and not crossed_cut:
            rows.append(t)
            last = t
            continue
        groups.append((start, last, rows))
        start = last = t
        rows = [t]
    groups.append((start, last, rows))

    candidates: List[Tuple[float, float, float]] = []
    min_len = float(SMART_REFRAME_DUAL_MIN_SECONDS)
    for raw_a, raw_b, vis_rows in groups:
        # Continuous detector span plus one cadence on each edge estimates the actual
        # visible run.  Keep an edit-safe margin from both ends/cuts.
        a = max(0.0, raw_a - SMART_REFRAME_SAMPLE_INTERVAL_SECONDS * 0.55)
        b = min(duration, raw_b + SMART_REFRAME_SAMPLE_INTERVAL_SECONDS * 0.55)
        safety = 0.10
        a += safety
        b -= safety
        if b - a < min_len:
            continue

        # Never let a split straddle a scene cut, even if detector timestamps were noisy.
        inside_cuts = [c for c in cuts if a < c < b]
        if inside_cuts:
            boundaries = [a] + inside_cuts + [b]
            pieces = [(x + 0.08, y - 0.08) for x, y in zip(boundaries, boundaries[1:])]
        else:
            pieces = [(a, b)]

        for pa, pb in pieces:
            run = pb - pa
            if run < min_len:
                continue

            # Prefer a clean editorial beat around 5 seconds, but allow longer when
            # the genuine two-person shot supports it.  Never invent frames beyond run.
            target = min(run, 6.8)
            target = max(min_len, target)
            if run > target + 0.25:
                # Centre the split in the stable dual-speaker run instead of always
                # snapping to its first detector hit.
                centre = (pa + pb) * 0.5
                wa = max(pa, centre - target * 0.5)
                wb = min(pb, wa + target)
                wa = max(pa, wb - target)
            else:
                wa, wb = pa, pb

            vis_in = [t for t in vis_rows if wa - 0.04 <= t <= wb + 0.04]
            expected = max(1.0, (wb - wa) / max(0.02, SMART_REFRAME_SAMPLE_INTERVAL_SECONDS))
            coverage = min(1.0, len(vis_in) / expected)
            confs = [c for t, c in evidence if wa <= t <= wb]
            speech = statistics.median(confs) if confs else 0.0
            duration_score = min(1.0, (wb - wa) / 5.0)
            # Visibility dominates; mouth evidence is a useful confidence boost but
            # quiet listeners must not make a valid two-person shot disappear.
            score = coverage * 0.62 + speech * 0.23 + duration_score * 0.15
            if coverage < 0.50:
                continue
            candidates.append((wa, wb, score))

    # Highest-quality windows first, then chronological output.  Hysteresis keeps
    # the editor from hammering the viewer with repeated split layouts.
    chosen: List[Tuple[float, float, float]] = []
    for a, b, score in sorted(candidates, key=lambda r: (-r[2], r[0])):
        if any(not (b + 7.0 <= x or a >= y + 7.0) for x, y, _ in chosen):
            continue
        chosen.append((a, b, score))
        if len(chosen) >= 4:
            break
    return [(a, b) for a, b, _ in sorted(chosen, key=lambda r: r[0])]


def _single_face_times_to_emphasis_intervals(
    times: List[float], clip_duration: float
) -> List[Tuple[float, float]]:
    """Non-mirrored same-speaker stacked emphasis.

    Only use it on genuinely long solo runs.  Each emphasis lasts at least five
    seconds and there are never more than two occurrences in one delivered clip.
    """
    out: List[Tuple[float, float]] = []
    for start_t, end_t in _group_visibility_times(times):
        if len(out) >= 2:
            break
        run_len = end_t - start_t
        if run_len < 7.0:
            continue
        cursor = start_t + 1.0
        while cursor <= end_t - 5.0 and len(out) < 2:
            finish = min(clip_duration, end_t + 0.08, cursor + min(6.0, max(5.0, run_len * 0.36)))
            if finish - cursor >= 5.0:
                out.append((max(0.0, cursor), finish))
            cursor += 12.0
    return out[:2]


def _dominant_times_to_emphasis_intervals(
    samples: List[Tuple[float, str]], clip_duration: float
) -> List[Tuple[float, float]]:
    """Fallback same-speaker stack: long dominant-speaker runs only, max twice."""
    if not samples:
        return []
    ordered = sorted(samples, key=lambda item: item[0])
    runs: List[Tuple[float, float, str]] = []
    run_start, last_t, side = ordered[0][0], ordered[0][0], ordered[0][1]
    max_gap = max(0.12, SMART_REFRAME_SAMPLE_INTERVAL_SECONDS * 5.0)
    for t, new_side in ordered[1:]:
        if new_side == side and t - last_t <= max_gap:
            last_t = t
            continue
        runs.append((run_start, last_t, side))
        run_start, last_t, side = t, t, new_side
    runs.append((run_start, last_t, side))

    out: List[Tuple[float, float]] = []
    for a, b, _ in runs:
        if len(out) >= 2:
            break
        run_len = b - a
        if run_len < 7.0:
            continue
        cursor = a + 1.0
        while cursor <= b - 5.0 and len(out) < 2:
            end_t = min(clip_duration, b, cursor + min(6.0, max(5.0, run_len * 0.36)))
            if end_t - cursor >= 5.0:
                out.append((max(0.0, cursor), end_t))
            cursor += 12.0
    return out[:2]

def _safe_face_center(
    face: Tuple[float, float, float, float, float, float, float],
) -> float:
    """Return the real face centre, including edge-clipped speakers.

    V7.4 uses a virtual padded camera canvas, so artificially pushing an edge
    face inward here is counterproductive: it leaves the face visibly stuck at
    the side of the final 9:16 frame. Keep only a tiny numerical clamp; the
    renderer supplies the side room needed to centre the subject.
    """
    cx = float(face[0])
    # Edge-rescue detectors may reconstruct the hidden half of a clipped head
    # in reflected padding. Preserve that virtual centre outside 0..1 so the
    # padded render canvas can place the *real head* at 50%, not merely the
    # visible sliver that remains inside the source frame.
    return max(-SMART_REFRAME_VIRTUAL_CENTER_MARGIN, min(1.0 + SMART_REFRAME_VIRTUAL_CENTER_MARGIN, cx))


def _motion_subject_evidence(
    previous_gray: Optional[Any],
    current_gray: Any,
    cv2_module: Any,
) -> Tuple[Optional[float], Optional[float], float, Optional[float], Optional[float], float, float]:
    """Token-free body-motion fallback for camera steering and two-side split.

    Returns overall (x, y, confidence), left/right motion centroids and their
    confidences. Hook/caption bands are masked so animated text cannot steer the
    camera. This is deliberately only a fallback/assist; a verified face remains
    the identity anchor.
    """
    if previous_gray is None or current_gray is None:
        return None, None, 0.0, None, None, 0.0, 0.0
    try:
        if previous_gray.shape != current_gray.shape:
            return None, None, 0.0, None, None, 0.0, 0.0
        h, w = current_gray.shape[:2]
        if h < 32 or w < 32:
            return None, None, 0.0, None, None, 0.0, 0.0
        diff = cv2_module.absdiff(previous_gray, current_gray)
        diff = cv2_module.GaussianBlur(diff, (5, 5), 0)
        # Adaptive threshold keeps subtle torso/head movement while rejecting
        # compression shimmer. Ignore top hook and bottom caption/legs bands.
        mean_v = float(diff.mean())
        threshold = max(14.0, min(28.0, mean_v * 1.5 + 12.0))
        _, mask = cv2_module.threshold(diff, threshold, 255, cv2_module.THRESH_BINARY)
        mask[: int(h * 0.12), :] = 0
        mask[int(h * 0.70) :, :] = 0
        kernel = cv2_module.getStructuringElement(cv2_module.MORPH_ELLIPSE, (5, 5))
        mask = cv2_module.morphologyEx(mask, cv2_module.MORPH_OPEN, kernel)
        mask = cv2_module.dilate(mask, kernel, iterations=1)

        def region_stats(x1: int, x2: int):
            roi = mask[:, x1:x2]
            ys, xs = (roi > 0).nonzero()
            count = int(len(xs))
            area = max(1, roi.shape[0] * roi.shape[1])
            frac = count / float(area)
            if count < max(80, int(area * 0.0020)):
                return None, None, 0.0
            weights = diff[:, x1:x2][ys, xs].astype('float64') + 1.0
            total = float(weights.sum()) or 1.0
            cx = (float((xs * weights).sum()) / total + x1) / float(w)
            cy = float((ys * weights).sum()) / total / float(h)
            conf = max(0.0, min(1.0, frac / 0.080))
            return cx, cy, conf

        cx, cy, conf = region_stats(0, w)
        # A hard scene cut changes most pixels and has no meaningful motion
        # centroid. The scene-cut path will reacquire the new speaker instead.
        overall_fraction = float((mask > 0).mean())
        if overall_fraction > 0.32:
            return None, None, 0.0, None, None, 0.0, 0.0
        # Keep a small centre gap so one central microphone cannot create two
        # simultaneous side speakers.
        left_x, _, left_conf = region_stats(0, int(w * 0.46))
        right_x, _, right_conf = region_stats(int(w * 0.54), w)
        return cx, cy, conf, left_x, right_x, left_conf, right_conf
    except Exception:
        return None, None, 0.0, None, None, 0.0, 0.0


def _smooth_face_track(
    raw_track: List[Tuple[float, float]],
    default_center: float = 0.5,
    scene_cuts: Optional[List[float]] = None,
    dimension_px: int = 1080,
    allow_virtual_center: bool = False,
) -> List[Tuple[float, float]]:
    """Scene-aware professional camera track with no artificial lag.

    This borrows the useful *behaviour* from OpenSource-Clipping's tracking
    controls without importing its Gemini pipeline: micro-jitter is ignored,
    hard speaker jumps are preserved as cuts, and smoothing never leaks across
    scene boundaries. Sustained body movement remains in the keyframe stream.
    """
    if not raw_track:
        return [(0.0, default_center)]

    lower_center = -SMART_REFRAME_VIRTUAL_CENTER_MARGIN if allow_virtual_center else 0.05
    upper_center = 1.0 + SMART_REFRAME_VIRTUAL_CENTER_MARGIN if allow_virtual_center else 0.95
    ordered = sorted(
        (max(0.0, float(t)), max(lower_center, min(upper_center, float(c))))
        for t, c in raw_track
    )
    if len(ordered) < 3:
        return ordered

    cut_times = sorted(float(x) for x in (scene_cuts or []) if float(x) >= 0.0)
    jitter_norm = OPENCLIP_TRACK_JITTER_PX / max(64.0, float(dimension_px))

    # Segment at real scene cuts and large speaker switches. This is critical:
    # a smoothing window must never interpolate through the empty space between
    # two people or across a hard camera cut.
    segments: List[List[Tuple[float, float]]] = []
    current: List[Tuple[float, float]] = []
    cut_index = 0
    for item in ordered:
        t, c = item
        crossed_scene = False
        while cut_index < len(cut_times) and cut_times[cut_index] <= t + 1e-6:
            if current and cut_times[cut_index] > current[-1][0] + 1e-6:
                crossed_scene = True
            cut_index += 1
        # Large detector jumps inside the SAME shot are often mic/profile false
        # reacquisitions, not a new speaker. Only a verified scene cut may split
        # the smoothing segment; sustained same-shot motion remains responsive and
        # the final camera keyframe stabilizer handles real fast reframes.
        if current and crossed_scene:
            segments.append(current)
            current = []
        current.append(item)
    if current:
        segments.append(current)

    def smooth_segment(segment: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        if len(segment) < 3:
            return segment
        times = [t for t, _ in segment]
        values = [c for _, c in segment]

        # A genuinely static speaker should produce a genuinely static camera.
        # Detector centres normally breathe by a few pixels even on a still head;
        # holding an entire low-span segment prevents that noise from becoming a
        # visible 2-6px camera drift.  The threshold is deliberately tiny, so any
        # real lean/body travel above a few pixels still passes through immediately.
        static_span = max(0.0060, jitter_norm * 6.0)
        if max(values) - min(values) <= static_span:
            held = float(statistics.median(values))
            held = max(lower_center, min(upper_center, held))
            return [(t, held) for t in times]

        denoised = list(values)

        # 5px-style micro jitter gate + isolated spike rejection. Small changes
        # are held only when neighbours agree it is noise; continuous motion is
        # intentionally preserved, so body-follow remains visible.
        for i in range(1, len(values) - 1):
            prev_v, cur_v, next_v = values[i - 1], values[i], values[i + 1]
            if abs(cur_v - prev_v) <= jitter_norm and abs(next_v - cur_v) <= jitter_norm:
                denoised[i] = (prev_v + cur_v + next_v) / 3.0
                continue
            lo = max(0, i - 2)
            hi = min(len(values), i + 3)
            neighbors = values[lo:i] + values[i + 1:hi]
            if len(neighbors) >= 2:
                med = sorted(neighbors)[len(neighbors) // 2]
                span = max(neighbors) - min(neighbors)
                if abs(values[i] - med) >= max(0.028, jitter_norm * 3.0) and span <= 0.055:
                    denoised[i] = med

        # Symmetric zero-phase pass: no temporal delay. Use up to the advertised
        # 12-frame stability window, but keep a compact local kernel so genuine
        # fast motion is not made sluggish.
        # True requested window: 3 means a 3-sample zero-phase kernel, not the
        # old accidental 5-sample kernel. This keeps response fast while still
        # suppressing one-frame shake.
        radius = max(1, min(4, (OPENCLIP_TRACK_SMOOTH_WINDOW - 1) // 2))
        stabilized: List[float] = []
        for i, c in enumerate(denoised):
            lo = max(0, i - radius)
            hi = min(len(denoised), i + radius + 1)
            local = denoised[lo:hi]
            distances = [abs((lo + j) - i) for j in range(len(local))]
            weights = [float(radius + 1 - min(radius, d)) for d in distances]
            total = sum(weights) or 1.0
            smooth = sum(v * w for v, w in zip(local, weights)) / total
            local_span = max(local) - min(local) if local else 0.0
            if local_span <= max(0.012, jitter_norm * 2.0):
                follow = smooth
            elif local_span <= 0.040:
                follow = c * 0.42 + smooth * 0.58
            elif local_span <= 0.085:
                follow = c * 0.70 + smooth * 0.30
            else:
                follow = c * 0.90 + smooth * 0.10
            stabilized.append(max(lower_center, min(upper_center, follow)))

        # Remove only a one-frame reversal/jerk; monotonic fast movement stays.
        guarded = list(stabilized)
        for i in range(1, len(stabilized) - 1):
            v_prev = stabilized[i] - stabilized[i - 1]
            v_next = stabilized[i + 1] - stabilized[i]
            if v_prev * v_next < 0.0 and abs(v_prev) >= 0.010 and abs(v_next) >= 0.010:
                mid = (stabilized[i - 1] + stabilized[i + 1]) * 0.5
                if abs(stabilized[i] - mid) >= 0.012:
                    guarded[i] = stabilized[i] * 0.35 + mid * 0.65
        return [(t, max(lower_center, min(upper_center, c))) for t, c in zip(times, guarded)]

    output: List[Tuple[float, float]] = []
    for segment in segments:
        smoothed = smooth_segment(segment)
        for t, c in smoothed:
            if output and abs(t - output[-1][0]) < 0.0005:
                output[-1] = (t, c)
            else:
                output.append((t, c))
    return output or [(0.0, default_center)]


def _merge_time_intervals(intervals: List[Tuple[float, float]], duration: float) -> List[Tuple[float, float]]:
    cleaned = sorted((max(0.0,float(a)), min(float(duration),float(b))) for a,b in intervals if float(b) > float(a))
    out: List[Tuple[float,float]] = []
    for a,b in cleaned:
        if out and a <= out[-1][1] + 0.20:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a,b))
    return [(a,b) for a,b in out if b-a >= SMART_REFRAME_DUAL_MIN_SECONDS - 1e-6]


def _speaker_switch_split_plan(
    samples: List[Tuple[float, str]],
    scene_cuts: List[float],
    clip_duration: float,
) -> Tuple[List[Tuple[float, float]], float, float, Tuple[float, float], Tuple[float, float], List[Tuple[float, float, str]]]:
    """Build verified >=4s split windows from alternating A/B camera shots.

    Speaker identity has already been established by appearance clustering.  The
    important reliability rule here is that a *shot*, not a dense detector run,
    owns the identity.  A profile/mic occlusion may yield only one or two face
    detections inside a four-second shot; requiring a 300ms detector run used to
    make genuine A/B podcasts silently miss split.  Hard scene cuts provide the
    temporal boundaries, and identity samples vote on each shot.
    """
    empty = ([], -1.0, -1.0, (-1.0, -1.0), (-1.0, -1.0), [])
    if not SMART_REFRAME_CROSS_SHOT_SPLIT or not samples:
        return empty
    duration = max(0.0, float(clip_duration))
    if duration < SMART_REFRAME_DUAL_MIN_SECONDS:
        return empty

    ordered = sorted(
        (max(0.0, min(duration, float(t))), str(side))
        for t, side in samples
        if side in {"left", "right"} and math.isfinite(float(t))
    )
    if len(ordered) < 4:
        return empty
    cuts = sorted({
        max(0.0, min(duration, float(c)))
        for c in scene_cuts
        if 0.06 < float(c) < duration - 0.06 and math.isfinite(float(c))
    })
    if not cuts:
        return empty

    boundaries = [0.0] + cuts + [duration]
    shots: List[Tuple[float, float, str, List[float], int, int]] = []
    cursor = 0
    for i in range(len(boundaries) - 1):
        shot_start, shot_end = boundaries[i], boundaries[i + 1]
        if shot_end - shot_start < 0.45:
            continue
        rows: List[Tuple[float, str]] = []
        # Monotonic cursor keeps this O(n) even on long podcasts.
        while cursor < len(ordered) and ordered[cursor][0] < shot_start - 0.04:
            cursor += 1
        j = cursor
        while j < len(ordered) and ordered[j][0] <= shot_end + 0.04:
            if ordered[j][0] >= shot_start - 0.04:
                rows.append(ordered[j])
            j += 1
        if not rows:
            continue
        left_n = sum(1 for _, side in rows if side == "left")
        right_n = len(rows) - left_n
        if left_n == right_n:
            continue
        label = "left" if left_n > right_n else "right"
        majority = max(left_n, right_n)
        minority = min(left_n, right_n)
        # Identity clustering already rejected duplicate speakers.  One profile
        # observation can therefore label a clean shot when the opposite identity
        # has no vote; mixed shots require a clearer majority.
        if majority < 1:
            continue
        if minority and majority < minority * 1.8:
            continue
        label_times = [t for t, side in rows if side == label]
        shots.append((shot_start, shot_end, label, label_times, left_n, right_n))

    if len(shots) < 2 or {shot[2] for shot in shots} != {"left", "right"}:
        return empty

    # Reference clips are full hard-cut shots, never just the detector span. This
    # is what lets a temporarily occluded/profile speaker still produce a moving
    # panel for the complete >=4s split window.
    refs: Dict[str, float] = {}
    ref_windows: Dict[str, Tuple[float, float]] = {}
    for target in ("left", "right"):
        candidates = [shot for shot in shots if shot[2] == target and shot[1] - shot[0] >= 0.90]
        if not candidates:
            return empty
        # Prefer a shot with more identity observations, then longer duration.
        best = max(candidates, key=lambda shot: (len(shot[3]), shot[1] - shot[0]))
        safe_start = best[0] + min(0.18, (best[1] - best[0]) * 0.08)
        safe_end = best[1] - min(0.18, (best[1] - best[0]) * 0.08)
        if safe_end - safe_start < 0.80:
            safe_start, safe_end = best[0], best[1]
        refs[target] = (safe_start + safe_end) * 0.5
        ref_windows[target] = (safe_start, safe_end)

    intervals: List[Tuple[float, float]] = []
    for prev, cur in zip(shots, shots[1:]):
        if prev[2] == cur[2]:
            continue
        # They must be adjacent across a real cut, not two labelled shots with an
        # unobserved scene between them.
        gap = max(0.0, cur[0] - prev[1])
        if gap > 0.30:
            continue
        switch_t = max(prev[1], cur[0])
        start_t = max(0.0, switch_t - 0.08)
        end_t = min(duration, start_t + 5.0)
        if end_t - start_t >= min(SMART_REFRAME_DUAL_MIN_SECONDS, max(0.0, duration - start_t)):
            intervals.append((start_t, end_t))

    # Keep cross-camera split as distinct editorial occurrences instead of
    # chain-merging rapid A/B cuts into a near-permanent 15-30 second split.
    # Each occurrence is >=4s (normally 5s), with breathing room before the next.
    selected_intervals: List[Tuple[float, float]] = []
    for a, b in sorted(intervals):
        a = max(0.0, float(a)); b = min(duration, max(a, float(b)))
        if b - a < SMART_REFRAME_DUAL_MIN_SECONDS - 1e-6:
            continue
        if selected_intervals and a < selected_intervals[-1][1] + 6.0:
            continue
        selected_intervals.append((a, min(b, a + 6.0)))
        if len(selected_intervals) >= 4:
            break
    intervals = selected_intervals
    if not intervals:
        return empty
    speaker_timeline = [(float(a), float(b), str(label)) for a, b, label, *_ in shots]
    return (
        intervals,
        float(refs["left"]),
        float(refs["right"]),
        ref_windows["left"],
        ref_windows["right"],
        speaker_timeline,
    )


def analyze_smart_reframe_plan(
    source_path: Path,
    start: float,
    end: float,
    cancel_event: threading.Event,
) -> SmartReframePlan:
    """
    Production-safe podcast reframing.

    Rules:
    - never move to an empty area when a face is temporarily missed;
    - reject tiny/unstable detections that are commonly pictures or background objects;
    - lock the current speaker and switch only after sustained mouth activity;
    - use a safe dual-speaker stack when both real faces are visible;
    - use a slow, bounded crop movement so FFmpeg never produces jumpy pans.
    """
    try:
        import cv2  # type: ignore
    except Exception as exc:
        return SmartReframePlan(
            mode="center",
            primary_track=[(0.0, 0.5)],
            secondary_track=[],
            note=f"center crop (OpenCV unavailable: {exc})",
        )

    capture = cv2.VideoCapture(str(source_path))
    if not capture.isOpened():
        return SmartReframePlan(
            mode="center",
            primary_track=[(0.0, 0.5), (max(0.1, end-start), 0.5)],
            secondary_track=[],
            note="center crop (video could not be opened)",
        )

    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    clip_duration = max(0.1, end - start)

    # Tracking does not need full 1080p/4K pixels. Analyse a compact copy while
    # keeping every coordinate normalized, so camera timing stays full-rate but
    # YuNet/optical-flow cost stays predictable on a VPS.
    analysis_scale = min(1.0, 640.0 / max(1.0, float(max(width, height))))
    analysis_width = max(2, int(round(width * analysis_scale)))
    analysis_height = max(2, int(round(height * analysis_scale)))
    analysis_width -= analysis_width % 2
    analysis_height -= analysis_height % 2

    try:
        detector, detector_kind = _create_face_detector(cv2)
    except Exception as exc:
        capture.release()
        return SmartReframePlan(
            mode="center",
            primary_track=[(0.0, 0.5), (clip_duration, 0.5)],
            secondary_track=[],
            note=f"center crop (face detector unavailable: {exc})",
        )
    upper_body_detector = _create_upper_body_detector(cv2)

    source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
    requested_analysis_fps = min(
        source_fps,
        max(12.0, min(30.0, 1.0 / SMART_REFRAME_SAMPLE_INTERVAL_SECONDS)),
    )
    frame_step = max(1, int(round(source_fps / max(1.0, requested_analysis_fps))))
    actual_analysis_fps = source_fps / frame_step
    desired_samples = int(clip_duration * actual_analysis_fps) + 1
    sample_count = min(SMART_REFRAME_MAX_SAMPLES, max(24, desired_samples))
    capture.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)

    raw_track: List[Tuple[float, float]] = []
    primary_y_raw: List[Tuple[float, float]] = []
    primary_face_heights: List[float] = []
    detected_frames = 0
    locked_center: Optional[float] = None
    locked_side: Optional[str] = None
    last_good_time = 0.0
    last_switch_time = -999.0
    pending_side: Optional[str] = None
    pending_count = 0
    previous_mouth: Dict[str, Optional[Any]] = {"left": None, "right": None}
    smoothed_activity: Dict[str, float] = {"left": 0.0, "right": 0.0}
    previous_scene_gray: Optional[Any] = None
    lost_face_samples = 0
    detector_miss_samples = 0
    last_face_detection_time = -999.0
    last_face_box: Optional[Tuple[float, float, float, float, float, float, float]] = None
    last_verified_center: Optional[float] = None
    last_verified_y: float = 0.40
    flow_confidence: float = 0.0
    # V7.4 independent frame-difference body fallback. This stays available even
    # when YuNet misses a clipped/profile face, so the camera no longer collapses
    # to a static geometric-centre crop.
    previous_motion_gray: Optional[Any] = None
    motion_fallback_center: Optional[float] = None
    motion_fallback_y: float = 0.40
    motion_fallback_track: List[Tuple[float, float]] = []
    motion_fallback_y_track: List[Tuple[float, float]] = []

    # Frame-to-frame Lucas-Kanade tracking follows the speaker/body on every
    # sampled source frame. Face detection only has to reacquire identity, so
    # side-profile misses do not freeze the camera or let the subject drift out.
    previous_flow_gray: Optional[Any] = None
    flow_points: Optional[Any] = None
    locked_y: float = 0.40
    flow_reseed_age = 0
    # Short-term upper-body appearance tracker. YuNet keeps identity safe; this
    # tracker is deliberately allowed to follow shoulders/torso between detector
    # samples so actual body movement becomes visible camera movement.
    subject_tracker = None
    tracker_center_x: Optional[float] = None
    tracker_center_y: Optional[float] = None
    tracker_last_good = -999.0
    detector_target_fps = SMART_REFRAME_DETECTOR_FPS if detector_kind == "yunet" else 5.0
    detector_stride = max(1, int(round(actual_analysis_fps / detector_target_fps)))
    initial_acquire_stride = max(1, detector_stride // 2)
    # Motion fallback is intentionally ~6 Hz. Adjacent compressed frames contain
    # codec shimmer everywhere; a ~160 ms baseline isolates real head/torso motion.
    motion_stride = max(2, int(round(actual_analysis_fps / 6.0)))

    # Adaptive reframe evidence.
    split_left_raw: List[Tuple[float, float]] = []
    split_right_raw: List[Tuple[float, float]] = []
    split_left_y_raw: List[Tuple[float, float]] = []
    split_right_y_raw: List[Tuple[float, float]] = []
    split_left_face_heights: List[float] = []
    split_right_face_heights: List[float] = []
    dual_evidence: List[Tuple[float, float]] = []
    dual_visibility_times: List[float] = []
    # Continuous one-face visibility is the most reliable signal for the
    # reference-style same-speaker stacked punch. It does not depend on noisy
    # mouth-motion classification.
    single_face_visibility_times: List[float] = []
    dominant_speaker_samples: List[Tuple[float, str]] = []
    speaker_side_samples: List[Tuple[float, str]] = []
    # V7.8 cross-shot identity evidence: time, face x/y, face height, HSV hue.
    # Unlike old left/right labels this still distinguishes speakers when both
    # camera angles place the active person in the centre of frame.
    speaker_identity_samples: List[Tuple[float, float, float, float, Tuple[float, ...]]] = []
    motion_history: List[float] = []
    scene_change_times: List[float] = []
    adaptive_velocity = 0.0
    adaptive_center = locked_center

    def side_of(face: Tuple[float, float, float, float, float, float, float]) -> str:
        return "left" if float(face[0]) < 0.5 else "right"

    def valid_faces(boxes: List[Tuple[float, float, float, float, float, float, float]]):
        # Tiny/background detections are often framed photos, posters, or décor.
        # Keep faces that are both absolutely large enough AND plausible relative
        # to the dominant on-camera person. This prevents camera tracks from
        # jumping to a photo/mic/empty set area while retaining a real second guest.
        if not boxes:
            return []
        largest_area = max(float(b[2]) for b in boxes)
        relative_floor = largest_area * 0.22
        good = []
        for b in boxes:
            _, cy, area, nx, ny, nw, nh = b
            aspect = (float(nw) * max(1.0, float(width))) / max(1e-6, float(nh) * max(1.0, float(height)))
            # Real on-camera heads occupy a plausible face rectangle and do not
            # live at the extreme bottom of the frame. These guards reject many
            # décor/poster false positives before they can ever steer the camera.
            touches_edge = float(nx) <= 0.012 or float(nx + nw) >= 0.988
            min_aspect = 0.22 if (detector_kind == "yunet" and touches_edge) else 0.38
            area_floor = SMART_REFRAME_MIN_FACE_AREA * (0.72 if touches_edge else 1.0)
            if (
                float(area) >= area_floor
                and float(area) >= relative_floor * (0.72 if touches_edge else 1.0)
                and float(nh) >= (0.034 if touches_edge else 0.040)
                and min_aspect <= aspect <= 1.40
                and float(cy) <= 0.86
                and float(ny) <= 0.82
            ):
                good.append(b)
        if not good and detector_kind == "yunet":
            # YuNet rows already passed confidence + landmark validation. Permit
            # one slightly-small foreground face as a reacquisition fallback, but
            # never do this for Haar because Haar false positives are exactly what
            # can lock onto microphones/photos/set decoration.
            largest = max(boxes, key=lambda b: float(b[2]))
            _, cy, area, _, ny, nw, nh = largest
            aspect = (float(nw) * max(1.0, float(width))) / max(1e-6, float(nh) * max(1.0, float(height)))
            if (
                float(area) >= SMART_REFRAME_MIN_FACE_AREA * 0.82
                and float(nh) >= 0.038
                and 0.40 <= aspect <= 1.36
                and float(cy) <= 0.84
                and float(ny) <= 0.80
            ):
                good = [largest]
        return sorted(good, key=lambda b: float(b[0]))

    try:
        for index in range(sample_count):
            ensure_not_cancelled(cancel_event)
            if index > 0:
                for _ in range(max(0, frame_step - 1)):
                    if not capture.grab():
                        break
            ok, frame = capture.read()
            relative_time = min(clip_duration, (index * frame_step) / max(1.0, source_fps))

            if not ok or frame is None:
                break

            # Work on the compact analysis frame. Normalized face coordinates are
            # identical to the source, so the final crop still targets the exact
            # full-resolution speaker position.
            analysis_frame = (
                cv2.resize(frame, (analysis_width, analysis_height), interpolation=cv2.INTER_AREA)
                if analysis_scale < 0.999
                else frame
            )

            # Update the appearance tracker on every analysed frame. Unlike face
            # detection, this box includes upper shoulders/torso, so seated leans and
            # body shifts affect composition instead of being flattened away.
            tracker_updated = False
            if subject_tracker is not None:
                try:
                    ok_track, track_box = subject_tracker.update(analysis_frame)
                    if ok_track:
                        tx, ty, tw, th = [float(v) for v in track_box]
                        tcx = (tx + tw * 0.5) / max(1.0, float(analysis_width))
                        tcy = (ty + th * 0.5) / max(1.0, float(analysis_height))
                        plausible = (
                            0.02 <= tcx <= 0.98 and 0.08 <= tcy <= 0.92
                            and tw >= analysis_width * 0.08 and th >= analysis_height * 0.10
                        )
                        if plausible and (locked_center is None or abs(tcx - float(locked_center)) <= SMART_REFRAME_BODY_TRACKER_MAX_DRIFT):
                            tracker_center_x = max(0.02, min(0.98, tcx))
                            tracker_center_y = max(0.08, min(0.92, tcy))
                            tracker_last_good = relative_time
                            tracker_updated = True
                except Exception:
                    subject_tracker = None

            # Scene-cut detection is essential for edited podcasts: after a hard cut,
            # old face coordinates are invalid and must never be carried into the new shot.
            flow_gray = cv2.cvtColor(analysis_frame, cv2.COLOR_BGR2GRAY)
            scene_gray = cv2.resize(flow_gray, (96, 54))
            scene_cut = False
            if previous_scene_gray is not None:
                scene_delta = float(cv2.absdiff(previous_scene_gray, scene_gray).mean()) / 255.0
                scene_cut = scene_delta >= 0.115
            previous_scene_gray = scene_gray

            # V7.4 body-motion evidence is independent of face acquisition. This
            # is the safety net for clipped/profile faces and directly fixes the
            # old behaviour where a detector miss meant a static centre frame.
            motion_cx = motion_cy = motion_left_x = motion_right_x = None
            motion_conf = motion_left_conf = motion_right_conf = 0.0
            motion_sample = (index % motion_stride == 0)
            if motion_sample and previous_motion_gray is not None and not scene_cut:
                (
                    motion_cx, motion_cy, motion_conf,
                    motion_left_x, motion_right_x, motion_left_conf, motion_right_conf,
                ) = _motion_subject_evidence(previous_motion_gray, flow_gray, cv2)
            if scene_cut:
                previous_motion_gray = None
                motion_fallback_center = None
            else:
                if motion_sample:
                    previous_motion_gray = flow_gray.copy()
                if motion_cx is not None and motion_conf >= 0.08:
                    if motion_fallback_center is None:
                        motion_fallback_center = float(motion_cx)
                        motion_fallback_y = float(motion_cy if motion_cy is not None else 0.40)
                    else:
                        delta = float(motion_cx) - float(motion_fallback_center)
                        # Real shot/speaker jumps snap; ordinary body movement follows
                        # quickly but with a tiny EMA to avoid compression shimmer.
                        if abs(delta) >= 0.22:
                            motion_fallback_center = float(motion_cx)
                        else:
                            motion_fallback_center = float(motion_fallback_center) * 0.34 + float(motion_cx) * 0.66
                        if motion_cy is not None:
                            motion_fallback_y = float(motion_fallback_y) * 0.42 + float(motion_cy) * 0.58
                    motion_fallback_track.append((relative_time, max(0.02, min(0.98, float(motion_fallback_center)))))
                    motion_fallback_y_track.append((relative_time, max(0.10, min(0.82, float(motion_fallback_y)))))
                    # Fallback speaker-side samples make alternating-camera split
                    # possible even when an edge face is temporarily not detected.
                    if motion_conf >= 0.16:
                        motion_side = "left" if float(motion_fallback_center) < 0.5 else "right"
                        speaker_side_samples.append((relative_time, motion_side))
                # Motion can assist BODY follow, but it is never sufficient to
                # invent a second speaker. Live split requires two independently
                # validated human faces so a hand/mic/chair cannot become panel B.

            # Optical-flow follow runs before face reacquisition. V5.7 keeps
            # flow points FACE-ANCHORED and validates them forward/backward. This
            # prevents a microphone/hand/background texture from stealing the
            # camera when the detector briefly loses a profile face.
            flow_updated = False
            flow_confidence = 0.0
            if (
                not scene_cut
                and previous_flow_gray is not None
                and flow_points is not None
                and locked_center is not None
                and last_face_box is not None
            ):
                try:
                    lk_args = dict(
                        winSize=(23, 23),
                        maxLevel=3,
                        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 18, 0.02),
                    )
                    new_points, status_fwd, _ = cv2.calcOpticalFlowPyrLK(
                        previous_flow_gray, flow_gray, flow_points, None, **lk_args
                    )
                    back_points = None
                    status_back = None
                    if new_points is not None and status_fwd is not None:
                        back_points, status_back, _ = cv2.calcOpticalFlowPyrLK(
                            flow_gray, previous_flow_gray, new_points, None, **lk_args
                        )

                    if (
                        new_points is not None
                        and status_fwd is not None
                        and back_points is not None
                        and status_back is not None
                    ):
                        old_flat = flow_points.reshape(-1, 2)
                        new_flat = new_points.reshape(-1, 2)
                        back_flat = back_points.reshape(-1, 2)
                        sf = status_fwd.reshape(-1)
                        sb = status_back.reshape(-1)

                        _, _, _, fx, fy, fw, fh = last_face_box
                        # Face/head + neck/upper-shoulder motion zone. Using a
                        # compact shape rather than a large rectangle prevents a
                        # nearby mic, gesturing hand, bottle, or background edge
                        # from gaining enough LK points to drag the crop.
                        head_x1 = max(0.0, (fx - fw * 0.18) * analysis_width)
                        head_x2 = min(float(analysis_width), (fx + fw * 1.18) * analysis_width)
                        head_y1 = max(0.0, (fy - fh * 0.14) * analysis_height)
                        head_y2 = min(float(analysis_height), (fy + fh * 1.10) * analysis_height)
                        shoulder_cx = (fx + fw * 0.50) * analysis_width
                        shoulder_cy = (fy + fh * 1.16) * analysis_height
                        shoulder_rx = max(6.0, fw * analysis_width * 0.72)
                        shoulder_ry = max(5.0, fh * analysis_height * 0.40)

                        pairs = []
                        for point_index, (old_pt, new_pt, back_pt, ok_f, ok_b) in enumerate(
                            zip(old_flat, new_flat, back_flat, sf, sb)
                        ):
                            if int(ok_f) != 1 or int(ok_b) != 1:
                                continue
                            ox, oy = float(old_pt[0]), float(old_pt[1])
                            in_head = head_x1 <= ox <= head_x2 and head_y1 <= oy <= head_y2
                            shoulder_metric = (
                                ((ox - shoulder_cx) / shoulder_rx) ** 2
                                + ((oy - shoulder_cy) / shoulder_ry) ** 2
                            )
                            if not (in_head or shoulder_metric <= 1.0):
                                continue
                            fb_dx = float(back_pt[0]) - ox
                            fb_dy = float(back_pt[1]) - oy
                            fb_error = (fb_dx * fb_dx + fb_dy * fb_dy) ** 0.5
                            if fb_error > SMART_REFRAME_FLOW_FB_ERROR_PX:
                                continue
                            dx = float(new_pt[0]) - ox
                            dy = float(new_pt[1]) - oy
                            pairs.append((dx, dy, float(new_pt[0]), float(new_pt[1]), point_index))

                        if len(pairs) >= 7:
                            # Robust translation estimate: median first, then reject
                            # points whose motion disagrees with the face/body pack.
                            dx_values = sorted(item[0] for item in pairs)
                            dy_values = sorted(item[1] for item in pairs)
                            med_dx = dx_values[len(dx_values) // 2]
                            med_dy = dy_values[len(dy_values) // 2]
                            residuals = sorted(
                                (((item[0] - med_dx) ** 2 + (item[1] - med_dy) ** 2) ** 0.5)
                                for item in pairs
                            )
                            med_residual = residuals[len(residuals) // 2] if residuals else 0.0
                            residual_limit = max(1.25, min(4.0, med_residual * 2.8 + 0.35))
                            inlier_pairs = [
                                item for item in pairs
                                if (((item[0] - med_dx) ** 2 + (item[1] - med_dy) ** 2) ** 0.5) <= residual_limit
                            ]
                            if len(inlier_pairs) < 6:
                                inlier_pairs = pairs
                            dx_values = sorted(item[0] for item in inlier_pairs)
                            dy_values = sorted(item[1] for item in inlier_pairs)
                            dx_px = dx_values[len(dx_values) // 2]
                            dy_px = dy_values[len(dy_values) // 2]
                            # Reject cut-like bursts. Normal head/body motion is
                            # much smaller between adjacent 30fps samples.
                            if abs(dx_px) <= analysis_width * 0.10 and abs(dy_px) <= analysis_height * 0.10:
                                age_since_face = max(0.0, relative_time - last_face_detection_time)
                                if age_since_face <= SMART_REFRAME_FLOW_MAX_MISS_SECONDS:
                                    # Follow the verified face/neck/shoulder pack at
                                    # essentially source speed. A tiny gain compensates
                                    # for the fact that the LK region is centred high on
                                    # the body and otherwise under-reports a seated lean.
                                    dx_norm = (dx_px / max(1.0, analysis_width)) * SMART_REFRAME_BODY_FOLLOW_GAIN
                                    dy_norm = (dy_px / max(1.0, analysis_height)) * SMART_REFRAME_BODY_FOLLOW_GAIN
                                    locked_center = max(0.05, min(0.95, locked_center + dx_norm))
                                    locked_y = max(0.08, min(0.92, locked_y + dy_norm))
                                    adaptive_center = locked_center
                                    cx0, cy0, area0, nx0, ny0, nw0, nh0 = last_face_box
                                    nx1 = max(0.0, min(1.0 - nw0, nx0 + dx_norm))
                                    ny1 = max(0.0, min(1.0 - nh0, ny0 + dy_norm))
                                    last_face_box = (
                                        max(0.0, min(1.0, cx0 + dx_norm)),
                                        max(0.0, min(1.0, cy0 + dy_norm)),
                                        area0, nx1, ny1, nw0, nh0,
                                    )
                                    # IMPORTANT: keep only the points that passed
                                    # FB consistency + face/body-zone + motion
                                    # consensus. Reusing all raw points was a source
                                    # of drift into mics/background on the next frame.
                                    keep_indices = [item[4] for item in inlier_pairs]
                                    flow_points = new_flat[keep_indices].astype("float32").reshape(-1, 1, 2)
                                    flow_confidence = min(1.0, len(inlier_pairs) / 28.0)
                                    flow_updated = True
                                else:
                                    # A stale flow track is more dangerous than a
                                    # frozen verified speaker crop. Force detector
                                    # reacquisition instead of drifting to furniture.
                                    locked_center = last_verified_center if last_verified_center is not None else locked_center
                                    locked_y = last_verified_y
                                    flow_points = None
                            else:
                                flow_points = None
                        else:
                            flow_points = None
                except Exception:
                    flow_points = None

            # Face detection is an identity/reacquisition step; optical flow does
            # the dense movement tracking. This is both faster and smoother than
            # running YuNet on every source frame.
            # Detector cadence is bounded. A temporary optical-flow miss must
            # NOT make Haar/YuNet run on all 30 frames/sec (that caused previous
            # timeout-class CPU spikes). Between detector samples we simply hold
            # the last verified face if flow is unavailable.
            need_detect = (
                scene_cut
                or index % detector_stride == 0
                or (locked_center is None and index % initial_acquire_stride == 0)
            )
            boxes = (
                valid_faces(_detect_face_boxes_for_reframe(analysis_frame, detector, detector_kind, cv2))
                if need_detect
                else []
            )
            if need_detect:
                if boxes:
                    detected_frames += 1
                    detector_miss_samples = 0
                    last_face_detection_time = relative_time
                else:
                    detector_miss_samples += 1
            if scene_cut:
                scene_change_times.append(relative_time)
                previous_mouth = {"left": None, "right": None}
                smoothed_activity = {"left": 0.0, "right": 0.0}
                pending_side = None
                pending_count = 0
                lost_face_samples = 0
                flow_points = None
                previous_flow_gray = None
                last_face_box = None
                last_verified_center = None
                subject_tracker = None
                tracker_center_x = None
                tracker_center_y = None
                tracker_last_good = -999.0
                if boxes:
                    largest_scene_face = max(boxes, key=lambda b: float(b[2]))
                    locked_center = _safe_face_center(largest_scene_face)
                    locked_y = float(largest_scene_face[1])
                    adaptive_center = locked_center
                    adaptive_velocity = 0.0
                    locked_side = side_of(largest_scene_face)
                    last_face_box = largest_scene_face
                    last_verified_center = locked_center
                    last_verified_y = locked_y
                    last_good_time = relative_time
                    last_switch_time = relative_time

            # Compute a stable lower-face motion score for each screen side.
            current_by_side: Dict[str, Tuple[float, float, float, float, float, float, float]] = {}
            side_candidates: Dict[str, List[Tuple[float, float, float, float, float, float, float]]] = {"left": [], "right": []}
            for face in boxes:
                side_candidates[side_of(face)].append(face)
            for side in ("left", "right"):
                candidates = side_candidates[side]
                if not candidates:
                    continue
                if locked_center is not None and locked_side == side:
                    # Speaker continuity beats a large static picture/background face.
                    # Follow the face nearest the last real speaker position.
                    current_by_side[side] = min(
                        candidates,
                        key=lambda b: (abs(_safe_face_center(b) - locked_center), -float(b[2])),
                    )
                else:
                    current_by_side[side] = max(candidates, key=lambda b: float(b[2]))

            # Keep per-speaker tracks even when only one camera angle is visible.
            # This is required for alternating-shot podcasts where A and B are never
            # present in the same source frame.
            if "left" in current_by_side:
                lf = current_by_side["left"]
                split_left_raw.append((relative_time, _safe_face_center(lf)))
                split_left_y_raw.append((relative_time, float(lf[1])))
                split_left_face_heights.append(float(lf[6]))
            if "right" in current_by_side:
                rf = current_by_side["right"]
                split_right_raw.append((relative_time, _safe_face_center(rf)))
                split_right_y_raw.append((relative_time, float(rf[1])))
                split_right_face_heights.append(float(rf[6]))

            # IMPORTANT: only refresh the mouth ROI when a detector sample actually
            # contains that face.  V5.6 cleared previous_mouth on the interleaved
            # optical-flow-only frames, so the next detector sample had no previous
            # ROI to compare against and simultaneous-speech evidence could stay at
            # zero.  Preserve the last real ROI between detector samples and decay
            # activity gently; this makes the >=4 s two-speaker split actually fire.
            for side in ("left", "right"):
                face = current_by_side.get(side)
                if face is not None:
                    roi = _mouth_roi_for_face(analysis_frame, face, cv2)
                    motion = _mouth_motion_score(previous_mouth.get(side), roi, cv2)
                    if roi is not None:
                        previous_mouth[side] = roi
                    # Near-live mouth activity: current detector sample dominates.
                    smoothed_activity[side] = smoothed_activity[side] * 0.24 + motion * 0.76
                else:
                    # No detector observation is NOT evidence that the mouth stopped.
                    # Hold most of the last score across one optical-flow frame while
                    # still letting stale activity disappear quickly on real misses.
                    decay = 0.92 if not need_detect else 0.58
                    smoothed_activity[side] *= decay
                    if need_detect:
                        previous_mouth[side] = None

            if "left" in current_by_side and "right" in current_by_side:
                left_center = _safe_face_center(current_by_side["left"])
                right_center = _safe_face_center(current_by_side["right"])
                left_sig = _speaker_visual_signature(analysis_frame, current_by_side["left"], cv2)
                right_sig = _speaker_visual_signature(analysis_frame, current_by_side["right"], cv2)
                pair_identity_distance = _speaker_signature_distance(left_sig, right_sig)
                # Reject near-identical duplicated portraits. Two independent face
                # detections alone are not enough when the same person is present
                # twice in a layout/reference shot.
                distinct_visible_speakers = pair_identity_distance >= 0.024
                if distinct_visible_speakers:
                    dual_visibility_times.append(relative_time)

                lm = float(smoothed_activity.get("left", 0.0))
                rm = float(smoothed_activity.get("right", 0.0))
                peak = max(lm, rm, 1e-6)
                balance = min(lm, rm) / peak

                # Open the split only when both real, visible faces have sustained
                # mouth activity. One loud/animated speaker + one quiet face is
                # intentionally NOT enough.
                dual_confidence = _simultaneous_speech_confidence(lm, rm)
                if distinct_visible_speakers and dual_confidence >= 0.26:
                    dual_evidence.append((relative_time, dual_confidence))

                if peak >= SMART_REFRAME_MOUTH_ACTIVITY_THRESHOLD * 0.72 and balance < 0.58:
                    dominant_speaker_samples.append((relative_time, "left" if lm >= rm else "right"))

            if len(current_by_side) == 1:
                single_face_visibility_times.append(relative_time)
                only_side = next(iter(current_by_side))
                if smoothed_activity.get(only_side, 0.0) >= SMART_REFRAME_MOUTH_ACTIVITY_THRESHOLD * 0.55:
                    dominant_speaker_samples.append((relative_time, only_side))

            if locked_center is None and boxes:
                # Start with the largest real face, not the geometric centre.
                first = max(boxes, key=lambda b: float(b[2]))
                locked_center = _safe_face_center(first)
                adaptive_center = locked_center
                adaptive_velocity = 0.0
                locked_side = side_of(first)
                last_face_box = first
                last_verified_center = locked_center
                last_verified_y = locked_y
                last_good_time = relative_time
                last_switch_time = relative_time

            if boxes and locked_center is not None:
                # V8.23 SPEAKER LOCK:
                # Mouth activity is only EVIDENCE for a speaker switch.
                # Never change the camera target from one mouth/detector sample.
                #
                # V8.22 could select the opposite face here and update locked_side
                # BEFORE the confirmation logic below ran. That allowed visible
                # left->right->left ping-pong inside one second.
                tracking_face = (
                    current_by_side.get(locked_side)
                    if locked_side in current_by_side
                    else None
                )

                if tracking_face is None:
                    # Bridge temporary profile/occlusion misses using identity
                    # continuity. Real hard scene cuts are already handled above.
                    expected_h = (
                        float(last_face_box[6])
                        if last_face_box is not None
                        else 0.0
                    )

                    def continuity_score(b):
                        center_cost = abs(
                            _safe_face_center(b) - locked_center
                        )
                        size_cost = (
                            abs(float(b[6]) - expected_h) * 0.55
                            if expected_h > 0
                            else 0.0
                        )
                        return center_cost + size_cost

                    tracking_face = min(
                        boxes,
                        key=continuity_score,
                    )

                target = max(0.06, min(0.94, _safe_face_center(tracking_face)))
                displacement = target - float(locked_center)
                same_identity = (locked_side is None or side_of(tracking_face) == locked_side)
                # V5.8: face detection owns IDENTITY, not every camera pixel. When
                # verified optical flow has just followed the head/neck/shoulders,
                # keep that body motion and apply only a gentle face-centre drift
                # correction. The old hard snap cancelled body-follow every 1-2
                # detector frames and made the camera look static. Hard reacquire is
                # still used after a real tracker miss, scene cut or large mismatch.
                if flow_updated and same_identity and abs(displacement) < 0.125:
                    corrected = float(locked_center) + displacement * SMART_REFRAME_FLOW_FACE_CORRECTION
                    adaptive_center = max(0.06, min(0.94, corrected))
                    face_y = float(tracking_face[1])
                    locked_y = max(0.08, min(0.92, float(locked_y) + (face_y - float(locked_y)) * SMART_REFRAME_FLOW_FACE_CORRECTION))
                else:
                    adaptive_center = target
                    locked_y = float(tracking_face[1])
                adaptive_velocity = displacement
                locked_center = adaptive_center
                locked_side = side_of(tracking_face)
                last_face_box = tracking_face
                last_verified_center = locked_center
                last_verified_y = locked_y
                last_good_time = relative_time
                lost_face_samples = 0
                motion_history.append(abs(displacement))

                other_side = "right" if locked_side == "left" else "left"
                current_score = smoothed_activity.get(locked_side or "left", 0.0)
                other_score = smoothed_activity.get(other_side, 0.0)
                other_face = current_by_side.get(other_side)
                hold_ok = relative_time - last_switch_time >= SMART_REFRAME_MIN_SPEAKER_HOLD_SECONDS
                switch_evidence = (
                    other_face is not None
                    and hold_ok
                    and other_score >= SMART_REFRAME_MOUTH_ACTIVITY_THRESHOLD * 0.70
                    and other_score >= max(
                        SMART_REFRAME_MOUTH_ACTIVITY_THRESHOLD * 0.70,
                        current_score * SMART_REFRAME_SWITCH_SCORE_RATIO,
                    )
                )

                if switch_evidence:
                    if pending_side == other_side:
                        pending_count += 1
                    else:
                        pending_side = other_side
                        pending_count = 1
                else:
                    pending_side = None
                    pending_count = 0

                if pending_side and pending_count >= SMART_REFRAME_SWITCH_CONFIRM_SAMPLES:
                    target_face = current_by_side.get(pending_side)
                    if target_face is not None:
                        target = _safe_face_center(target_face)
                        # Switch on this detector sample; do not add a pre-switch
                        # hold point because that creates visible speaker lag.
                        locked_center = target
                        locked_y = float(target_face[1])
                        adaptive_center = target
                        adaptive_velocity = 0.0
                        locked_side = pending_side
                        last_face_box = target_face
                        last_verified_center = locked_center
                        last_verified_y = locked_y
                        last_switch_time = relative_time
                        last_good_time = relative_time
                    pending_side = None
                    pending_count = 0

            # V6.6: do not crush genuine body-follow movement back into a tiny
            # detector corridor.  The face detector is an identity/safety anchor;
            # LK head+shoulder motion remains the camera target.  Only correct a
            # dangerous drift large enough to threaten the face crop.
            safety_face = current_by_side.get(locked_side) if locked_side else None
            if safety_face is None and boxes and locked_center is not None:
                safety_face = min(boxes, key=lambda b: abs(_safe_face_center(b) - float(locked_center)))
            if safety_face is not None and locked_center is not None:
                face_center_now = _safe_face_center(safety_face)
                face_w_now = max(0.01, float(safety_face[5]))
                max_body_offset = max(0.095, min(0.190, face_w_now * 0.95))
                drift = float(locked_center) - face_center_now
                if abs(drift) > max_body_offset:
                    excess = abs(drift) - max_body_offset
                    locked_center -= (1.0 if drift > 0 else -1.0) * excess * 0.72
                    locked_center = max(0.04, min(0.96, locked_center))
                adaptive_center = locked_center

            # Critical safety rule: on missed detections, hold the last valid speaker.
            # Never drift back to 0.5, which is what produced plant/empty-chair shots.
            if locked_center is None:
                center = 0.5
            else:
                # Never allow a stale tracker to wander into a microphone, vase,
                # hands or empty set. After the face-lock window expires, freeze
                # the last detector-verified speaker position until reacquired.
                if (
                    last_verified_center is not None
                    and relative_time - last_face_detection_time > SMART_REFRAME_FLOW_MAX_MISS_SECONDS
                ):
                    locked_center = last_verified_center
                    locked_y = last_verified_y
                    center = last_verified_center
                    flow_points = None
                else:
                    center = locked_center
            # Final camera composition is face-safe but body-responsive. The face
            # remains the dominant anchor; the verified upper-body tracker contributes
            # enough weight to make real leans/torso movement visibly move the frame.
            camera_center = float(center)
            detected_body_box = None
            if safety_face is not None and need_detect and index % max(1, detector_stride * 2) == 0:
                detected_body_box = _detect_upper_body_for_face(
                    analysis_frame, safety_face, upper_body_detector, cv2
                )
            body_target = None
            if (
                detected_body_box is not None
                and abs(float(detected_body_box[0]) - float(center)) <= SMART_REFRAME_BODY_TRACKER_MAX_DRIFT
            ):
                body_target = float(detected_body_box[0])
            elif (
                tracker_center_x is not None
                and relative_time - tracker_last_good <= 0.55
                and abs(float(tracker_center_x) - float(center)) <= SMART_REFRAME_BODY_TRACKER_MAX_DRIFT
            ):
                body_target = float(tracker_center_x)
            elif motion_fallback_center is not None and motion_conf >= 0.08:
                # Frame-difference body motion is less identity-specific than MIL,
                # so only accept it near the current speaker when a face exists.
                if safety_face is None or abs(float(motion_fallback_center) - float(center)) <= 0.24:
                    body_target = float(motion_fallback_center)

            if safety_face is not None:
                # Face is ALWAYS visually centred. Body movement contributes a
                # bounded relative offset, so the crop moves with a lean without
                # leaving the face stuck at the left/right edge.
                face_c = _safe_face_center(safety_face)
                camera_center = face_c
                if body_target is not None:
                    body_delta = max(-0.075, min(0.075, body_target - face_c))
                    camera_center = face_c + body_delta * 0.72
            elif body_target is not None:
                # No face at this sample: keep camera alive on verified body motion
                # rather than falling back to 0.5/static framing.
                camera_center = body_target
            camera_center = max(-SMART_REFRAME_VIRTUAL_CENTER_MARGIN, min(1.0 + SMART_REFRAME_VIRTUAL_CENTER_MARGIN, camera_center))
            raw_track.append((relative_time, camera_center))
            if locked_side in {"left", "right"}:
                speaker_side_samples.append((relative_time, locked_side))
            # Track vertical face position and face size too.  This is what lets
            # already-vertical TikTok/Shorts sources receive real reframing
            # instead of being silently passed through unchanged.
            chosen_face = current_by_side.get(locked_side) if locked_side else None
            if chosen_face is None and boxes:
                chosen_face = min(boxes, key=lambda b: abs(_safe_face_center(b) - center))
            if chosen_face is not None:
                # Cross-camera identity evidence combines face structure +
                # face/torso appearance, not screen side or one mean hue.
                try:
                    _sig = _speaker_visual_signature(analysis_frame, chosen_face, cv2)
                    if _sig:
                        speaker_identity_samples.append((
                            relative_time,
                            float(_safe_face_center(chosen_face)),
                            float(chosen_face[1]),
                            float(chosen_face[6]),
                            _sig,
                        ))
                except Exception:
                    pass
                # Keep the camera's verified upper-body Y motion instead of hard-snapping
                # back to the detector face centre on every detector sample.  A bounded
                # face-safety corridor prevents LK/body flow from ever walking far enough
                # to cut the head at the crop edge.  This preserves visible body-follow
                # movement while keeping the face protected and stable.
                face_y_now = float(chosen_face[1])
                if flow_updated:
                    max_body_y_offset = max(0.060, min(0.130, float(chosen_face[6]) * 0.55))
                    locked_y = max(
                        face_y_now - max_body_y_offset,
                        min(face_y_now + max_body_y_offset, float(locked_y)),
                    )
                else:
                    locked_y = face_y_now
                last_face_box = chosen_face
                last_verified_center = center
                last_verified_y = locked_y
                camera_y = float(locked_y)
                body_y_anchor = None
                if detected_body_box is not None:
                    body_y_anchor = max(0.10, min(0.82, float(detected_body_box[1]) - 0.10))
                elif tracker_center_y is not None and relative_time - tracker_last_good <= 0.55:
                    # Upper-body box centre naturally sits below the face. Shift it up
                    # before blending so the speaker's eyes remain in the upper third.
                    body_y_anchor = max(0.10, min(0.82, float(tracker_center_y) - 0.10))
                if body_y_anchor is not None:
                    wy = min(0.32, SMART_REFRAME_BODY_COMPOSITION_WEIGHT * 0.72)
                    camera_y = float(locked_y) * (1.0 - wy) + body_y_anchor * wy
                primary_y_raw.append((relative_time, max(0.08, min(0.88, camera_y))))
                primary_face_heights.append(float(chosen_face[6]))
            else:
                # Face may be briefly missed; keep body tracker composition alive
                # instead of snapping to the last detector Y.
                camera_y = float(locked_y)
                body_y_anchor = None
                if detected_body_box is not None:
                    body_y_anchor = max(0.10, min(0.82, float(detected_body_box[1]) - 0.10))
                elif tracker_center_y is not None and relative_time - tracker_last_good <= 0.55:
                    body_y_anchor = max(0.10, min(0.82, float(tracker_center_y) - 0.10))
                if body_y_anchor is not None:
                    wy = min(0.32, SMART_REFRAME_BODY_COMPOSITION_WEIGHT * 0.72)
                    camera_y = float(locked_y) * (1.0 - wy) + body_y_anchor * wy
                primary_y_raw.append((relative_time, max(0.08, min(0.88, camera_y))))

            # Re-anchor the upper-body tracker from every trustworthy detected face.
            # Production uses fast KCF from opencv-contrib.  If KCF is unavailable,
            # face-gated Lucas-Kanade + the upper-body detector continue tracking;
            # do NOT fall back to CPU-heavy MIL, which causes visible VPS lag.
            if chosen_face is not None and need_detect:
                try:
                    _, _, _, nx_t, ny_t, nw_t, nh_t = chosen_face
                    if detected_body_box is not None:
                        _, _, bx, by, bw, bh = detected_body_box
                    else:
                        bx = max(0.0, nx_t - nw_t * 0.38)
                        by = max(0.0, ny_t - nh_t * 0.16)
                        bw = min(1.0 - bx, nw_t * 1.76)
                        bh = min(1.0 - by, nh_t * 2.55)
                    if bw >= 0.08 and bh >= 0.12:
                        tr = _create_fast_subject_tracker(cv2)
                        if tr is not None:
                            tr.init(analysis_frame, (
                                int(round(bx * analysis_width)), int(round(by * analysis_height)),
                                int(round(bw * analysis_width)), int(round(bh * analysis_height)),
                            ))
                            subject_tracker = tr
                            tracker_center_x = bx + bw * 0.5
                            tracker_center_y = by + bh * 0.5
                            tracker_last_good = relative_time
                        else:
                            subject_tracker = None
                except Exception:
                    subject_tracker = None

            # Seed/reseed flow from an expanded face-to-upper-body region. This
            # makes body movement drive the camera, not background microphones,
            # lamps, framed photos, or empty set space.
            if chosen_face is not None and (need_detect or flow_points is None or flow_reseed_age >= 5):
                try:
                    _, _, _, nx, ny, nw, nh = chosen_face
                    # Face/hair plus a compact neck/upper-shoulder ellipse. This
                    # tracks genuine body motion while excluding the usual mic/hand
                    # zones that sit beside or below the speaker.
                    mask = cv2.zeros_like(flow_gray)
                    x1 = int(max(0, (nx - nw * 0.18) * analysis_width))
                    x2 = int(min(analysis_width, (nx + nw * 1.18) * analysis_width))
                    y1 = int(max(0, (ny - nh * 0.14) * analysis_height))
                    y2 = int(min(analysis_height, (ny + nh * 1.10) * analysis_height))
                    if x2 > x1 + 8 and y2 > y1 + 8:
                        cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)
                        shoulder_center = (
                            int(max(0, min(analysis_width - 1, (nx + nw * 0.50) * analysis_width))),
                            int(max(0, min(analysis_height - 1, (ny + nh * 1.16) * analysis_height))),
                        )
                        shoulder_axes = (
                            max(6, int(nw * analysis_width * 0.72)),
                            max(5, int(nh * analysis_height * 0.40)),
                        )
                        cv2.ellipse(mask, shoulder_center, shoulder_axes, 0, 0, 360, 255, -1)
                        seeded = cv2.goodFeaturesToTrack(
                            flow_gray,
                            mask=mask,
                            maxCorners=72,
                            qualityLevel=0.012,
                            minDistance=5,
                            blockSize=7,
                        )
                        if seeded is not None and len(seeded) >= 7:
                            flow_points = seeded
                            flow_reseed_age = 0
                except Exception:
                    pass
            flow_reseed_age += 1
            previous_flow_gray = flow_gray
    finally:
        capture.release()

    if detected_frames == 0 or locked_center is None:
        # V7.4: detector failure is no longer permission to produce a dead/static
        # centre crop. Promote measured body-motion evidence into the normal
        # planning path so split/keyframe generation still runs.
        if len(motion_fallback_track) >= 3:
            raw_track = list(motion_fallback_track)
            primary_y_raw = list(motion_fallback_y_track) if motion_fallback_y_track else [(0.0, 0.40), (clip_duration, 0.40)]
            locked_center = float(raw_track[-1][1])
            locked_y = float(primary_y_raw[-1][1]) if primary_y_raw else 0.40
        else:
            fallback_track = [(0.0, 0.5), (clip_duration, 0.5)]
            fallback_y = [(0.0, 0.40), (clip_duration, 0.40)]
            return SmartReframePlan(
                mode="single",
                primary_track=fallback_track,
                secondary_track=[],
                note=f"safe center fallback; no face/body evidence ({detector_kind})",
                single_speaker_emphasis_intervals=[],
                split_primary_track=fallback_track,
                split_secondary_track=fallback_track,
                split_primary_y_track=fallback_y,
                split_secondary_y_track=fallback_y,
                primary_y_track=fallback_y,
            )

    track = _smooth_face_track(
        raw_track,
        raw_track[0][1] if raw_track else locked_center,
        scene_cuts=scene_change_times,
        dimension_px=width,
        allow_virtual_center=True,
    )
    primary_y_track = _smooth_face_track(primary_y_raw, 0.40, scene_cuts=scene_change_times, dimension_px=height) if primary_y_raw else [(0.0, 0.40), (clip_duration, 0.40)]
    # Identity-based cross-shot split. Position is deliberately ignored because
    # A/B podcast cameras often centre both people. A conservative appearance
    # cluster plus real scene-cut timing is required before two speakers exist.
    # Cross-camera identity: prefer hard-cut SHOT aggregation.  It is more robust
    # than raw-frame k-means for profile/lighting changes, while still retaining
    # the older frame clustering as a fallback for unusual edits.
    identity_cluster_mode = "none"
    identity_clusters = _cluster_two_speaker_shots(
        speaker_identity_samples, scene_change_times
    )
    if identity_clusters is not None:
        identity_cluster_mode = "shot"
    else:
        identity_clusters = _cluster_two_speaker_signatures(speaker_identity_samples)
        if identity_clusters is not None:
            identity_cluster_mode = "frame"
    if identity_clusters is not None:
        try:
            _id_a, _id_b, _between, _within = identity_clusters
            speaker_side_samples = [(r[0], "left") for r in _id_a] + [(r[0], "right") for r in _id_b]
            # Replace side-derived tracks for cross-shot sources when both camera
            # angles centre their subject, otherwise both identities can live near
            # x=0.5 and the legacy left/right buckets collapse into one person.
            _id_a_x = [(r[0], r[1]) for r in _id_a]
            _id_b_x = [(r[0], r[1]) for r in _id_b]
            _id_a_y = [(r[0], r[2]) for r in _id_a]
            _id_b_y = [(r[0], r[2]) for r in _id_b]
            if _id_a_x and _id_b_x:
                # V8.22: keep these identity clusters for normal speaker/shot
                # understanding only.  SAME-FRAME split crops must retain the actual
                # left/right observations from frames where both people coexist.
                # Replacing split tracks with cross-shot identity tracks can make one
                # panel jump to a different camera/timestamp geometry.
                pass
        except Exception as _identity_exc:
            logger.debug("V8 appearance identity split clustering skipped: %s", _identity_exc)

    left_track = _smooth_face_track(split_left_raw, 0.30, scene_cuts=scene_change_times, dimension_px=width, allow_virtual_center=True) if split_left_raw else []
    right_track = _smooth_face_track(split_right_raw, 0.70, scene_cuts=scene_change_times, dimension_px=width, allow_virtual_center=True) if split_right_raw else []
    left_y_track = _smooth_face_track(split_left_y_raw, OPENCLIP_SPLIT_V_ALIGN, scene_cuts=scene_change_times, dimension_px=height) if split_left_y_raw else []
    right_y_track = _smooth_face_track(split_right_y_raw, OPENCLIP_SPLIT_V_ALIGN, scene_cuts=scene_change_times, dimension_px=height) if split_right_y_raw else []

    # V8.22 SPLIT DIRECTOR: only SAME-TIMESTAMP, SAME-SOURCE-FRAME splits.
    # No cross-camera/reference footage is permitted.  The two panels are cropped
    # from the exact same source frame at t, so neither panel can be ahead/behind.
    dual_intervals = _split_director_select_intervals(
        dual_evidence,
        dual_visibility_times,
        scene_change_times,
        clip_duration,
    )

    # Explicitly disable the old A/B cross-shot compositor.  A camera-cut podcast
    # where only one person exists in the current frame remains normal full-frame
    # reframe; fabricating a split from historical/future footage is editorially wrong.
    cross_intervals: List[Tuple[float, float]] = []
    left_ref_time = -1.0
    right_ref_time = -1.0
    left_ref_window = (-1.0, -1.0)
    right_ref_window = (-1.0, -1.0)
    cross_speaker_timeline: List[Tuple[float, float, str]] = []

    single_emphasis_intervals: List[Tuple[float, float]] = []
    dynamic_ok = bool(dual_intervals and left_track and right_track)

    avg_motion = (
        sum(motion_history) / len(motion_history)
        if motion_history else 0.0
    )

    return SmartReframePlan(
        mode=("dynamic" if dynamic_ok else "single"),
        primary_track=track,
        secondary_track=[],
        note=(
            "v7.4 face-centred virtual camera + independent body-motion fallback; source-FPS per-frame crop keyframes; edge-safe virtual canvas; YuNet landmark validation; scene-aware same-timeline split-director; "
            f"motion={avg_motion:.3f}; detector_faces={detected_frames}; "
            + (f"split-director windows={len(dual_intervals)} same-timeline-only=1 identity={identity_cluster_mode}; " if dynamic_ok else f"split-director windows=0 same-timeline-only=1 identity={identity_cluster_mode}; ")
            + f"({detector_kind}, {detected_frames}/{sample_count} face samples @ {actual_analysis_fps:.1f}fps sequential)"
        ),
        dual_speaker_intervals=dual_intervals,
        cross_scene_split_intervals=cross_intervals,
        split_primary_reference_time=left_ref_time,
        split_secondary_reference_time=right_ref_time,
        split_primary_reference_window=left_ref_window,
        split_secondary_reference_window=right_ref_window,
        cross_scene_speaker_intervals=cross_speaker_timeline,
        single_speaker_emphasis_intervals=single_emphasis_intervals,
        split_primary_track=left_track,
        split_secondary_track=right_track,
        split_primary_y_track=left_y_track,
        split_secondary_y_track=right_y_track,
        split_primary_face_h=(float(statistics.median(split_left_face_heights)) if split_left_face_heights else 0.0),
        split_secondary_face_h=(float(statistics.median(split_right_face_heights)) if split_right_face_heights else 0.0),
        primary_y_track=primary_y_track,
        primary_face_h=(float(statistics.median(primary_face_heights)) if primary_face_heights else 0.0),
    )


def _build_forced_face_center_tracks(
    source_path: Path,
    clip_start: float,
    clip_end: float,
    cancel_event: threading.Event,
    fallback_x_track: Optional[List[Tuple[float, float]]] = None,
    fallback_y_track: Optional[List[Tuple[float, float]]] = None,
) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]], int]:
    """Dense final-rescue camera track that follows the *detected face centre*.

    This is intentionally different from the normal composition tracker.  It is
    only used after PASS 3 reports repeated off-centre framing.  The first render
    may blend face + upper-body motion for a more natural composition; the rescue
    render instead makes the verified face centre the camera target on every
    sampled frame.  The virtual side canvas in the renderer then supplies enough
    room to centre speakers that are already clipped by the source edge.

    Returns (x_track, y_track, detected_sample_count).  If no reliable face track
    can be built, the supplied normal tracks are returned unchanged.
    """
    try:
        import cv2  # type: ignore
    except Exception:
        return list(fallback_x_track or []), list(fallback_y_track or []), 0

    duration = max(0.05, float(clip_end) - float(clip_start))
    cap = cv2.VideoCapture(str(source_path))
    if not cap.isOpened():
        return list(fallback_x_track or []), list(fallback_y_track or []), 0

    try:
        source_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 25.0
        frame_count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        source_duration = frame_count / source_fps if frame_count > 0 else 0.0
        start = max(0.0, min(float(clip_start), max(0.0, source_duration - 0.02))) if source_duration > 0 else max(0.0, float(clip_start))
        end = min(float(clip_end), source_duration) if source_duration > 0 else float(clip_end)
        duration = max(0.05, end - start)
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)

        try:
            detector, detector_kind = _create_face_detector(cv2)
        except Exception:
            detector, detector_kind = None, "dlib_rescue"

        # 8 Hz is dense enough for visible camera motion while keeping the rescue
        # pass practical on 4K VPS inputs. Commands are still interpolated and
        # emitted at the native output frame rate by _write_reframe_sendcmd().
        sample_hz = min(10.0, max(6.0, float(SMART_REFRAME_DETECTOR_FPS) * 0.50))
        frame_step = max(1, int(round(source_fps / sample_hz)))
        max_samples = max(4, min(3600, int(duration * sample_hz) + 4))

        x_raw: List[Tuple[float, float]] = []
        y_raw: List[Tuple[float, float]] = []
        detected = 0
        previous_center: Optional[float] = None
        previous_scene_gray: Optional[Any] = None
        last_face_time = -999.0
        last_face_x: Optional[float] = None
        last_face_y: Optional[float] = None
        last_detected_face_time: Optional[float] = None
        last_detected_face_x: Optional[float] = None
        last_detected_face_y: Optional[float] = None
        face_velocity_x = 0.0
        face_velocity_y = 0.0
        # High-resolution dlib edge/profile rescue is intentionally expensive.
        # Throttle it to ~2 Hz and bridge intervening frames with the verified
        # velocity/body track; running HOG on every 6-10 Hz rescue sample causes
        # multi-minute VPS stalls that look like camera lag.
        last_edge_rescue_attempt = -999.0
        edge_rescue_interval = 0.48

        def fallback_at(track: Optional[List[Tuple[float, float]]], t: float, default: float) -> float:
            return float(_track_value_at(track or [], t, default))

        for index in range(max_samples):
            ensure_not_cancelled(cancel_event)
            if index > 0:
                for _ in range(max(0, frame_step - 1)):
                    if not cap.grab():
                        break
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            t = min(duration, (index * frame_step) / max(1.0, source_fps))

            # Scene cuts reset continuity so a new camera angle can immediately
            # acquire the new speaker rather than staying loyal to the old side.
            gray_small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (96, 54))
            scene_cut = False
            if previous_scene_gray is not None:
                scene_cut = (float(cv2.absdiff(previous_scene_gray, gray_small).mean()) / 255.0) >= 0.115
            previous_scene_gray = gray_small
            if scene_cut:
                previous_center = None
                last_edge_rescue_attempt = -999.0

            boxes: List[Tuple[float, float, float, float, float, float, float]] = []
            if detector is not None:
                try:
                    boxes = _detect_face_boxes_for_reframe(frame, detector, detector_kind, cv2)
                except Exception:
                    boxes = []
            if not boxes:
                should_try_edge = (
                    scene_cut
                    or last_face_x is None
                    or t - last_edge_rescue_attempt >= edge_rescue_interval
                )
                if should_try_edge:
                    last_edge_rescue_attempt = t
                    try:
                        boxes = _dlib_edge_face_boxes(frame, cv2)
                    except Exception:
                        boxes = []

            # Reject tiny/background décor detections. The rescue is deliberately
            # conservative: it should centre a real foreground speaker, not a photo.
            plausible = []
            for box in boxes:
                cx, cy, area, nx, ny, nw, nh = box
                aspect = (float(nw) * max(1.0, float(frame.shape[1]))) / max(1e-6, float(nh) * max(1.0, float(frame.shape[0])))
                if (
                    float(area) >= max(0.0015, SMART_REFRAME_MIN_FACE_AREA * 0.55)
                    and float(nh) >= 0.032
                    and 0.25 <= aspect <= 1.55
                    and float(cy) <= 0.86
                    and float(ny) <= 0.84
                ):
                    plausible.append(box)

            chosen = None
            expected_center = fallback_at(
                fallback_x_track, t, previous_center if previous_center is not None else 0.5
            )
            if plausible:
                # The normal tracker already selected the active speaker. Use that
                # as the identity selector, then use the verified face itself for
                # exact centring. This prevents the final pass sticking to speaker A
                # after the live tracker has correctly switched to speaker B.
                if previous_center is None or scene_cut or abs(expected_center - float(previous_center or expected_center)) >= 0.10:
                    chosen = min(
                        plausible,
                        key=lambda b: abs(float(b[0]) - expected_center) - min(0.10, float(b[2]) * 1.4),
                    )
                else:
                    chosen = min(
                        plausible,
                        key=lambda b: (
                            abs(float(b[0]) - expected_center) * 0.72
                            + abs(float(b[0]) - float(previous_center)) * 0.28
                            - min(0.10, float(b[2]) * 1.4)
                        ),
                    )

            if chosen is not None:
                fx = _safe_face_center(chosen)
                fy = max(0.08, min(0.82, float(chosen[1])))
                # Preserve hard camera cuts, but lightly suppress one-frame detector
                # wobble. This follows genuine head/body movement with no long lag.
                if previous_center is not None and not scene_cut and abs(fx - previous_center) < 0.16:
                    fx = previous_center * 0.20 + fx * 0.80
                previous_center = fx
                if last_detected_face_time is not None and last_detected_face_x is not None:
                    dt_face = max(1e-3, t - last_detected_face_time)
                    if dt_face <= 0.60 and not scene_cut:
                        instant_vx = (fx - last_detected_face_x) / dt_face
                        instant_vy = (fy - float(last_detected_face_y or fy)) / dt_face
                        face_velocity_x = max(-0.45, min(0.45, face_velocity_x * 0.35 + instant_vx * 0.65))
                        face_velocity_y = max(-0.35, min(0.35, face_velocity_y * 0.35 + instant_vy * 0.65))
                    else:
                        face_velocity_x = 0.0
                        face_velocity_y = 0.0
                last_detected_face_time, last_detected_face_x, last_detected_face_y = t, fx, fy
                last_face_x, last_face_y, last_face_time = fx, fy, t
                detected += 1
            elif last_face_x is not None and t - last_face_time <= 0.70:
                miss_age = max(0.0, t - last_face_time)
                predict_horizon = min(SMART_REFRAME_NO_FACE_PREDICT_SECONDS, miss_age)
                decay = max(0.0, 1.0 - miss_age / max(0.05, SMART_REFRAME_NO_FACE_PREDICT_SECONDS))
                fx = float(last_face_x) + face_velocity_x * predict_horizon * decay
                fy = float(last_face_y or 0.40) + face_velocity_y * predict_horizon * decay
                # Short predictive bridge only; never drift into furniture/background.
                fx = max(float(last_face_x) - 0.035, min(float(last_face_x) + 0.035, fx))
                fy = max(float(last_face_y or 0.40) - 0.035, min(float(last_face_y or 0.40) + 0.035, fy))
            else:
                fx = fallback_at(fallback_x_track, t, previous_center if previous_center is not None else 0.5)
                fy = fallback_at(fallback_y_track, t, last_face_y if last_face_y is not None else 0.40)
                previous_center = fx

            x_raw.append((t, max(-SMART_REFRAME_VIRTUAL_CENTER_MARGIN, min(1.0 + SMART_REFRAME_VIRTUAL_CENTER_MARGIN, float(fx)))))
            y_raw.append((t, max(0.08, min(0.88, float(fy)))))

        if x_raw and x_raw[-1][0] < duration - 0.01:
            x_raw.append((duration, x_raw[-1][1]))
            y_raw.append((duration, y_raw[-1][1] if y_raw else 0.40))

        if detected < 3 or len(x_raw) < 3:
            return list(fallback_x_track or x_raw), list(fallback_y_track or y_raw), detected

        # Small zero-phase smoothing only. The rescue must remain responsive.
        x_track = _smooth_face_track(x_raw, x_raw[0][1], dimension_px=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1080), allow_virtual_center=True)
        y_track = _smooth_face_track(y_raw, y_raw[0][1], dimension_px=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1920))
        return x_track, y_track, detected
    finally:
        cap.release()

def _fuse_verified_face_and_body_tracks(
    face_x_track: List[Tuple[float, float]],
    face_y_track: List[Tuple[float, float]],
    body_x_track: List[Tuple[float, float]],
    body_y_track: List[Tuple[float, float]],
    duration: float,
    width: int = 1080,
    height: int = 1920,
) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
    """Face = identity/centre anchor; face-gated body = small camera residual."""
    if not face_x_track:
        return list(body_x_track or []), list(body_y_track or [])
    times = sorted({
        max(0.0, min(float(duration), float(t)))
        for t, _ in (list(face_x_track) + list(body_x_track) + list(face_y_track) + list(body_y_track))
    } | {0.0, max(0.0, float(duration))})
    fused_x: List[Tuple[float, float]] = []
    fused_y: List[Tuple[float, float]] = []
    for t in times:
        fx = _track_value_at(face_x_track, t, 0.5)
        fy = _track_value_at(face_y_track, t, 0.40)
        bx = _track_value_at(body_x_track, t, fx)
        by = _track_value_at(body_y_track, t, fy)
        dx, dy = bx - fx, by - fy
        # When the head centre was reconstructed outside the source edge, body
        # boxes necessarily sit inward. Do not let that body residual drag the
        # virtual head centre back toward the source and recreate edge clipping.
        if fx < 0.02 or fx > 0.98:
            dx = 0.0
        elif abs(dx) > 0.12:
            dx = 0.0
        if abs(dy) > 0.16:
            dy = 0.0
        if abs(dx) < 0.0038:
            dx = 0.0
        if abs(dy) < 0.0045:
            dy = 0.0
        # On a 16:9 -> 9:16 crop 0.012 source-width is roughly a 4-5%
        # portrait-frame offset: visible body-follow while the face remains in
        # the requested ~45-55% horizontal centre band.
        dx = max(-0.018, min(0.018, dx))
        dy = max(-0.032, min(0.032, dy))
        fused_x.append((t, max(-SMART_REFRAME_VIRTUAL_CENTER_MARGIN, min(1.0 + SMART_REFRAME_VIRTUAL_CENTER_MARGIN, fx + dx))))
        fused_y.append((t, max(0.06, min(0.92, fy + dy))))
    return (
        _smooth_face_track(fused_x, fused_x[0][1], dimension_px=max(2, int(width)), allow_virtual_center=True),
        _smooth_face_track(fused_y, fused_y[0][1], dimension_px=max(2, int(height))),
    )


def analyze_face_track_with_opencv(
    source_path: Path,
    start: float,
    end: float,
    cancel_event: threading.Event,
) -> Tuple[List[Tuple[float, float]], str]:
    plan = analyze_smart_reframe_plan(
        source_path,
        start,
        end,
        cancel_event,
    )
    return plan.primary_track, plan.note


def _face_center_to_crop_x(
    center_norm: float,
    width: int,
    crop_width: int,
) -> int:
    x = int(round(center_norm * width - crop_width / 2.0))
    x = max(0, min(max(0, width - crop_width), x))
    return x - (x % 2)


def _compress_crop_track_for_ffmpeg(
    track: List[Tuple[float, float]],
    width: int,
    crop_width: int,
    max_points: int = 64,
) -> List[Tuple[float, int]]:
    """
    Convert normalized face-center keyframes to a compact crop-x track.

    FFmpeg's expression evaluator has a practical recursion/nesting limit.
    A 60-second clip sampled every ~0.35s can produce 170+ nested `if()`
    calls, which fails with `Missing ')' or too many args`.  Keep the visual
    tracking responsive while bounding the expression depth to a safe level.
    """
    if not track:
        return []

    raw: List[Tuple[float, int]] = []
    for time_value, center_value in sorted(track, key=lambda item: item[0]):
        t = max(0.0, float(time_value))
        x = _face_center_to_crop_x(
            float(center_value),
            width,
            crop_width,
        )
        # Replace duplicate timestamps rather than creating zero-length
        # interpolation segments.
        if raw and abs(t - raw[-1][0]) < 0.001:
            raw[-1] = (t, x)
        else:
            raw.append((t, x))

    if len(raw) <= 2:
        return raw

    # Remove redundant middle points where crop-x is unchanged. Keep both
    # ends of a flat run so movement still starts at the correct time.
    compact: List[Tuple[float, int]] = [raw[0]]
    for index in range(1, len(raw) - 1):
        previous_x = raw[index - 1][1]
        current_x = raw[index][1]
        next_x = raw[index + 1][1]
        if previous_x == current_x == next_x:
            continue
        compact.append(raw[index])
    compact.append(raw[-1])

    if len(compact) <= max_points:
        return compact

    # Evenly resample by timeline index, always preserving the first/last
    # keyframes.  64 nested branches stays comfortably below the parser
    # failure threshold seen around ~90-100 branches on common FFmpeg builds.
    selected: List[Tuple[float, int]] = []
    last_index = len(compact) - 1
    for slot in range(max_points):
        index = int(round(slot * last_index / (max_points - 1)))
        point = compact[index]
        if not selected or point != selected[-1]:
            selected.append(point)

    if selected[-1] != compact[-1]:
        selected[-1] = compact[-1]
    return selected



def build_face_crop_axis_expression(
    track: List[Tuple[float, float]],
    dimension: int,
    crop_size: int,
    anchor: float = 0.5,
) -> str:
    if dimension <= crop_size:
        return "0"
    anchor = max(0.20, min(0.80, float(anchor)))
    mapped = []
    for t, center in track:
        pos = float(center) * dimension - crop_size * anchor
        pos = max(0.0, min(float(dimension - crop_size), pos))
        mapped_center = (pos + crop_size / 2.0) / dimension
        mapped.append((float(t), mapped_center))
    return build_face_crop_x_expression(mapped, dimension, crop_size)

def build_face_crop_x_expression(
    track: List[Tuple[float, float]],
    width: int,
    crop_width: int,
) -> str:
    """
    Build an Opus-style crop-x expression.

    Same-speaker motion can interpolate smoothly. Large speaker changes never
    pan slowly across the full time gap: the old crop is held, followed by a
    very fast reframe immediately before the next speaker keyframe.
    """
    if width <= crop_width:
        return "0"

    if not track:
        return str(_face_center_to_crop_x(0.5, width, crop_width))

    points = _compress_crop_track_for_ffmpeg(
        track,
        width,
        crop_width,
        max_points=64,
    )

    if len(points) == 1:
        return str(points[0][1])

    expression = str(points[-1][1])
    max_x = max(0, width - crop_width)
    jump_threshold_px = max(
        72,
        int(round(width * SMART_REFRAME_SPEAKER_SWITCH_DISTANCE * 0.72)),
    )

    for index in range(len(points) - 2, -1, -1):
        t0, x0 = points[index]
        t1, x1 = points[index + 1]
        duration = max(0.001, t1 - t0)
        distance = abs(x1 - x0)

        if distance >= jump_threshold_px:
            switch_seconds = min(
                SMART_REFRAME_FAST_SWITCH_SECONDS,
                max(0.06, duration * 0.45),
            )
            switch_start = max(t0, t1 - switch_seconds)
            interpolation = (
                f"{x0}+({x1}-{x0})*"
                f"max(0,min(1,(t-{switch_start:.3f})/{switch_seconds:.3f}))"
            )
        else:
            # Cubic smoothstep: natural ease-in/ease-out without sluggish lag.
            u_expr = f"max(0,min(1,(t-{t0:.3f})/{duration:.3f}))"
            interpolation = (
                f"{x0}+({x1}-{x0})*"
                f"(({u_expr})*({u_expr})*(3-2*({u_expr})))"
            )

        expression = (
            f"if(lt(t,{t1:.3f}),"
            f"max(0,min({max_x},{interpolation})),"
            f"{expression})"
        )

    return expression


def generate_hook_overlay(
    hook_text: str,
    output_path: Path,
) -> Path:
    """
    Create the screenshot-style hook:
    white rounded rectangle, large bold black centered text, top third.
    """
    from PIL import Image, ImageDraw, ImageFont  # type: ignore

    width, height = 1080, 330
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    font_candidates = [
        "/usr/share/fonts/truetype/lato/Lato-Heavy.ttf",
        "/usr/share/fonts/truetype/lato/Lato-Black.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]
    font = None
    for candidate in font_candidates:
        if Path(candidate).exists():
            font = ImageFont.truetype(candidate, 62)
            break
    if font is None:
        font = ImageFont.load_default()

    hook_text = _clipper_clean_text(hook_text, 64)
    hook_text = _censor_risky_text(hook_text)
    hook_text = re.sub(r"(?:^|\s)(?:>>+|O+H+|UH+|UM+|OKAY|OK)(?:\s|$)", " ", hook_text, flags=re.I)
    hook_text = re.sub(r"\s+", " ", hook_text).strip(" -–—:,.!? ").upper()
    hook_text = " ".join(hook_text.split()[:10])
    words = hook_text.split()
    lines: List[str] = []
    current = ""

    for word in words:
        trial = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= 900 or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    lines = lines[:2]

    line_height = 72
    box_height = max(132, len(lines) * line_height + 46)
    left = 70
    right = width - 70
    top = 24
    bottom = min(height - 20, top + box_height)

    # Soft shadow.
    draw.rounded_rectangle(
        (left + 7, top + 8, right + 7, bottom + 8),
        radius=28,
        fill=(0, 0, 0, 95),
    )

    # White card matching the supplied reference style.
    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=28,
        fill=(255, 255, 255, 245),
    )

    y = top + 24
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_width = bbox[2] - bbox[0]
        x = (width - text_width) / 2
        draw.text(
            (x, y),
            line,
            font=font,
            fill=(0, 0, 0, 255),
            stroke_width=0,
        )
        y += line_height

    image.save(output_path)
    return output_path


CAPTION_TEMPLATES: Dict[str, Dict[str, str]] = {
    "channel_reference": {
        "label": "🎯 Channel Reference",
        "font": "Lato Heavy",
        "primary": "&H00FFFFFF",
        "highlight": "&H003232DC",
        "highlight2": "&H003232DC",
        "outline": "&H00000000",
        "back": "&H00000000",
        "fontsize": "94",
        "outline_size": "8",
        "shadow": "2",
        "margin_v": "315",
        "group_size": "3",
        "mode": "single",
    },
    "none": {
        "label": "🚫 Remove Captions",
        "font": "Lato Heavy",
        "primary": "&H00FFFFFF",
        "highlight": "&H00FFFFFF",
        "highlight2": "&H00FFFFFF",
        "outline": "&H00000000",
        "back": "&H00000000",
        "fontsize": "1",
        "outline_size": "0",
        "shadow": "0",
        "margin_v": "0",
        "group_size": "3",
        "mode": "single",
    },
    # Closest match to the supplied Opus Pro reference clips:
    # heavy uppercase, white words, active green/yellow word, thick black edge.
    "reference_green": {
        "label": "🟩 Opus Reference Green",
        "font": "Lato Heavy",
        "primary": "&H00FFFFFF",
        "highlight": "&H0000FF00",
        "highlight2": "&H0000D7FF",
        "outline": "&H00000000",
        "back": "&H00000000",
        "fontsize": "72",
        "outline_size": "6",
        "shadow": "2",
        "margin_v": "340",
        "group_size": "3",
        "mode": "mixed",
    },
    "reference_yellow": {
        "label": "🟨 Opus Reference Yellow",
        "font": "Lato Heavy",
        "primary": "&H00FFFFFF",
        "highlight": "&H0000D7FF",
        "highlight2": "&H0000FF00",
        "outline": "&H00000000",
        "back": "&H00000000",
        "fontsize": "72",
        "outline_size": "6",
        "shadow": "2",
        "margin_v": "340",
        "group_size": "3",
        "mode": "mixed",
    },
    "viral_mix": {
        "label": "🔥 Opus Viral Green + Yellow",
        "font": "Lato Heavy",
        "primary": "&H00FFFFFF",
        "highlight": "&H0000FF00",
        "highlight2": "&H0000D7FF",
        "outline": "&H00000000",
        "back": "&H00000000",
        "fontsize": "72",
        "outline_size": "6",
        "shadow": "2",
        "margin_v": "340",
        "group_size": "3",
        "mode": "mixed",
    },
    "opus_green": {
        "label": "🟢 Bold Green",
        "font": "Lato Heavy",
        "primary": "&H00FFFFFF",
        "highlight": "&H0046C800",
        "highlight2": "&H0000BEFF",
        "outline": "&H00000000",
        "back": "&H00000000",
        "fontsize": "88",
        "outline_size": "7",
        "shadow": "2",
        "margin_v": "300",
        "group_size": "3",
        "mode": "single",
    },
    "yellow_pop": {
        "label": "🟡 Yellow Pop",
        "font": "Lato Heavy",
        "primary": "&H00FFFFFF",
        "highlight": "&H0000BEFF",
        "highlight2": "&H0046C800",
        "outline": "&H00000000",
        "back": "&H00000000",
        "fontsize": "82",
        "outline_size": "7",
        "shadow": "2",
        "margin_v": "340",
        "group_size": "3",
        "mode": "single",
    },
    "clean_white": {
        "label": "⚪ Clean White",
        "font": "Lato Heavy",
        "primary": "&H00FFFFFF",
        "highlight": "&H00FFFFFF",
        "highlight2": "&H00FFFFFF",
        "outline": "&H00000000",
        "back": "&H00000000",
        "fontsize": "88",
        "outline_size": "7",
        "shadow": "2",
        "margin_v": "300",
        "group_size": "3",
        "mode": "single",
    },
    "blue_punch": {
        "label": "🔵 Blue Punch",
        "font": "Lato Heavy",
        "primary": "&H00FFFFFF",
        "highlight": "&H00E66E2D",
        "highlight2": "&H0046C800",
        "outline": "&H00000000",
        "back": "&H00000000",
        "fontsize": "82",
        "outline_size": "7",
        "shadow": "2",
        "margin_v": "340",
        "group_size": "3",
        "mode": "single",
    },
    "red_impact": {
        "label": "🔴 Red Impact",
        "font": "Lato Heavy",
        "primary": "&H00FFFFFF",
        "highlight": "&H003232DC",
        "highlight2": "&H0000BEFF",
        "outline": "&H00000000",
        "back": "&H00000000",
        "fontsize": "82",
        "outline_size": "7",
        "shadow": "2",
        "margin_v": "340",
        "group_size": "3",
        "mode": "single",
    },
    "creator_white_red": {
        "label": "⚪ Creator White + Red",
        "font": "Lato Heavy", "primary": "&H00FFFFFF",
        "highlight": "&H003232DC", "highlight2": "&H0000BEFF",
        "outline": "&H00000000", "back": "&H00000000",
        "fontsize": "105", "outline_size": "8", "shadow": "2",
        "margin_v": "315", "group_size": "3", "mode": "mixed",
    },
    "creator_white_green": {
        "label": "🟢 Creator White + Green",
        "font": "Lato Heavy", "primary": "&H00FFFFFF",
        "highlight": "&H0046C800", "highlight2": "&H00FFFFFF",
        "outline": "&H00000000", "back": "&H00000000",
        "fontsize": "105", "outline_size": "8", "shadow": "2",
        "margin_v": "315", "group_size": "3", "mode": "mixed",
    },
    "creator_clean_large": {
        "label": "✨ Creator Clean Large",
        "font": "Lato Heavy", "primary": "&H00FFFFFF",
        "highlight": "&H00FFFFFF", "highlight2": "&H00FFFFFF",
        "outline": "&H00000000", "back": "&H00000000",
        "fontsize": "110", "outline_size": "8", "shadow": "2",
        "margin_v": "315", "group_size": "3", "mode": "single",
    },
}

def auto_clipper_caption_template_menu(
    job_id: str,
    duration_choice: str,
) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    keys = list(CAPTION_TEMPLATES.keys())

    for index in range(0, len(keys), 2):
        row: List[InlineKeyboardButton] = []
        for key in keys[index:index + 2]:
            row.append(
                InlineKeyboardButton(
                    CAPTION_TEMPLATES[key]["label"],
                    callback_data=(
                        f"clip:{job_id}:{duration_choice}:{key}"
                    ),
                )
            )
        rows.append(row)

    rows.append(
        [
            InlineKeyboardButton(
                "⬅️ Back to Duration",
                callback_data=f"clipback:{job_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def _approximate_word_timings_from_paragraphs(
    paragraphs: List["TranscriptParagraph"],
    clip_start: float,
    clip_end: float,
) -> List[ClipperWordTiming]:
    words_out: List[ClipperWordTiming] = []

    for paragraph in paragraphs:
        if paragraph.end <= clip_start or paragraph.start >= clip_end:
            continue

        relative_start = max(0.0, paragraph.start - clip_start)
        relative_end = min(
            clip_end - clip_start,
            paragraph.end - clip_start,
        )
        words = paragraph.text.split()
        if not words or relative_end <= relative_start:
            continue

        duration = max(0.1, relative_end - relative_start)
        per_word = duration / len(words)

        for index, word in enumerate(words):
            start_value = relative_start + index * per_word
            end_value = min(
                relative_end,
                relative_start + (index + 1) * per_word,
            )
            words_out.append(
                ClipperWordTiming(
                    start=start_value,
                    end=max(start_value + 0.04, end_value),
                    text=word,
                )
            )

    return words_out


def transcribe_clip_word_timings(
    source_path: Path,
    clip_start: float,
    clip_end: float,
    paragraphs: List["TranscriptParagraph"],
    cancel_event: threading.Event,
) -> Tuple[List[ClipperWordTiming], List[Path], str]:
    """
    Run word-timestamp Whisper only on the selected clip.

    This avoids retranscribing the full source video while giving the Auto
    Clipper captions real word-level timestamps, which removes the visible
    caption delay caused by paragraph-level timing approximation.
    """
    ensure_not_cancelled(cancel_event)
    cleanup: List[Path] = []
    duration = max(0.1, clip_end - clip_start)

    audio_path = DOWNLOAD_DIR / (
        f"clipper_words_{uuid.uuid4().hex[:10]}.wav"
    )
    cleanup.append(audio_path)

    try:
        run_process(
            [
                "ffmpeg", "-y",
                "-hide_banner", "-loglevel", "warning",
                "-ss", f"{clip_start:.3f}",
                "-t", f"{duration:.3f}",
                "-i", str(source_path),
                "-vn",
                "-ac", "1",
                "-ar", "16000",
                "-c:a", "pcm_s16le",
                str(audio_path),
            ],
            cancel_event=cancel_event,
            error_prefix="Auto Clipper caption audio extraction failed",
            timeout_seconds=AUTO_CLIPPER_WORD_AUDIO_TIMEOUT_SECONDS,
        )

        model = get_whisper_model()
        kwargs: Dict[str, Any] = {
            "task": "transcribe",
            "language": WHISPER_LANGUAGE,
            "beam_size": 5,
            "temperature": 0.0,
            "vad_filter": True,
            "vad_parameters": {
                "min_silence_duration_ms": 250,
                "speech_pad_ms": 100,
            },
            "word_timestamps": True,
            "compression_ratio_threshold": 2.4,
            "log_prob_threshold": -1.0,
            "no_speech_threshold": 0.5,
            "initial_prompt": (
                "Transcribe exactly what is spoken. Preserve names, acronyms, "
                "numbers and punctuation. Do not paraphrase or summarize."
            ),
        }
        if "distil" in WHISPER_MODEL_NAME.lower():
            kwargs["condition_on_previous_text"] = False
        else:
            kwargs["condition_on_previous_text"] = True

        started_at = time.monotonic()
        segments, _ = model.transcribe(
            str(audio_path),
            **kwargs,
        )

        word_rows: List[ClipperWordTiming] = []
        for segment in segments:
            ensure_not_cancelled(cancel_event)
            if (
                time.monotonic() - started_at
                > AUTO_CLIPPER_WORD_TRANSCRIBE_TIMEOUT_SECONDS
            ):
                raise RuntimeError(
                    "Word-level caption transcription timed out"
                )

            for word in getattr(segment, "words", None) or []:
                ensure_not_cancelled(cancel_event)
                value = normalize_transcript_text(
                    str(getattr(word, "word", "") or "")
                )
                if not value:
                    continue

                start_value = max(
                    0.0,
                    float(getattr(word, "start", 0.0) or 0.0),
                )
                end_value = max(
                    start_value + 0.04,
                    float(
                        getattr(word, "end", start_value + 0.04)
                        or start_value + 0.04
                    ),
                )
                if start_value > duration + 0.5:
                    continue
                word_rows.append(
                    ClipperWordTiming(
                        start=min(duration, start_value),
                        end=min(duration, end_value),
                        text=value,
                    )
                )

        word_rows = _clean_clipper_word_timings(word_rows)
        if word_rows:
            return word_rows, cleanup, "exact Whisper word timestamps"

    except JobCancelled:
        raise
    except Exception as exc:
        logger.warning(
            "Exact Auto Clipper word timestamps unavailable; "
            "using paragraph timing fallback: %s",
            exc,
        )

    fallback = _approximate_word_timings_from_paragraphs(
        paragraphs,
        clip_start,
        clip_end,
    )
    return fallback, cleanup, "paragraph timing fallback"


def _caption_group_bounds(
    words: List[ClipperWordTiming],
    active_index: int,
    group_size: int = 5,
) -> Tuple[int, int]:
    if not words:
        return 0, 0

    start = (active_index // group_size) * group_size
    end = min(len(words), start + group_size)

    if len(words) - end == 1 and start > 0:
        end = len(words)
        start = max(0, end - group_size)

    return start, end


def _caption_group_emoji(
    words: List[ClipperWordTiming],
) -> str:
    """
    Use emojis only for strong context. Generic question words no longer
    generate random emojis.
    """
    tokens = {
        _transcript_token_key(word.text)
        for word in words
        if _transcript_token_key(word.text)
    }
    rules = [
        ({"death", "died", "murder", "killed", "dead"}, "⚠️"),
        ({"shocking", "shock", "crazy", "insane", "unbelievable"}, "😱"),
        ({"secret", "truth", "reveal", "revealed", "exposed"}, "👀"),
        ({"money", "million", "billion", "dollar", "pound", "price"}, "💰"),
        ({"love", "heart", "relationship", "married", "wedding"}, "❤️"),
        ({"laugh", "funny", "joke", "joking", "hilarious"}, "😂"),
        ({"angry", "hate", "fight", "argument", "furious"}, "😤"),
        ({"win", "won", "success", "best", "greatest", "viral"}, "🔥"),
        ({"warning", "danger", "risk", "careful"}, "⚠️"),
        ({"police", "court", "arrested", "crime"}, "🚨"),
    ]
    for keywords, emoji in rules:
        if tokens.intersection(keywords):
            return emoji
    return ""




def _clean_clipper_word_timings(
    words: List[ClipperWordTiming],
) -> List[ClipperWordTiming]:
    output: List[ClipperWordTiming] = []
    previous_normalized = ""

    for word in words:
        value = normalize_transcript_text(str(word.text or "")).strip()
        if not value:
            continue

        normalized = _transcript_token_key(value)
        start_value = max(0.0, float(word.start))
        end_value = max(start_value + 0.04, float(word.end))

        if output:
            previous = output[-1]
            if (
                normalized
                and normalized == previous_normalized
                and start_value <= previous.end + 0.18
            ):
                previous.end = max(previous.end, end_value)
                continue

            if start_value < previous.start:
                start_value = previous.start
            if end_value <= start_value:
                end_value = start_value + 0.06

        output.append(
            ClipperWordTiming(
                start=start_value,
                end=end_value,
                text=value,
            )
        )
        previous_normalized = normalized

        # Remove short duplicated phrases only when the repeated copy begins
        # almost immediately after/inside the first copy (typical Whisper loop).
        for phrase_len in range(min(8, len(output) // 2), 1, -1):
            first = output[-2 * phrase_len:-phrase_len]
            second = output[-phrase_len:]
            first_keys = [_transcript_token_key(item.text) for item in first]
            second_keys = [_transcript_token_key(item.text) for item in second]
            if (
                first_keys == second_keys
                and all(first_keys)
                and second[0].start <= first[-1].end + 0.28
            ):
                del output[-phrase_len:]
                previous_normalized = (
                    _transcript_token_key(output[-1].text) if output else ""
                )
                break

    return output


def _caption_active_color(
    template: Dict[str, str],
    word_value: str,
    active_index: int,
) -> str:
    primary_highlight = template.get("highlight", "&H0000FF00")
    secondary_highlight = template.get("highlight2", primary_highlight)
    if template.get("mode") != "mixed":
        return primary_highlight

    token = _transcript_token_key(word_value)
    yellow_words = {
        "never", "worst", "best", "huge", "money", "million", "billion",
        "death", "dead", "lie", "lied", "truth", "secret", "crazy",
        "shocking", "warning", "danger", "risk", "accountability",
        "absolutely", "enough", "passed", "failed", "problem",
    }
    if token in yellow_words or active_index % 3 == 2:
        return secondary_highlight
    return primary_highlight




PROFANITY_WORDS = {
    # Strong profanity / explicit insults
    "fuck", "fucking", "fucked", "fucker", "fuckers", "motherfucker",
    "motherfuckers", "motherfucking", "shit", "shitty", "bullshit",
    "bitch", "bitches", "asshole", "bastard", "dick", "cock", "pussy",
    "cunt", "slut", "whore", "nigger", "nigga", "retard", "retarded",
    "damn", "goddamn",
    # Explicit sexual content
    "porn", "porno", "pornography", "sex", "sexual", "sexting", "nude", "nudes",
    "naked", "orgasm", "cum", "semen", "blowjob", "handjob", "anal",
    "rape", "raped", "raping", "rapist", "molest", "molested", "molesting",
    # Drugs / intoxication terms the user wants conservatively filtered
    "cocaine", "heroin", "meth", "methamphetamine", "crack", "fentanyl",
    "overdose", "overdosed", "weed", "marijuana", "cannabis", "ecstasy", "mdma",
    # Self-harm / severe violence
    "suicide", "suicidal", "selfharm", "self-harm", "murder", "murdered", "murdering",
    "beheading", "beheaded", "decapitated", "kill", "killed", "killing", "killer",
    "shoot", "shooting", "shot", "gun", "guns", "weapon", "weapons", "blood", "bloody",
}

# Common transcript spellings / euphemised spellings.  Match audio ASR output,
# not just captions, so bleep and visible masking use the same decision.
RISK_WORD_ALIASES = {
    "fck": "fuck", "fuk": "fuck", "fukk": "fuck", "fuuck": "fuck", "fuuuck": "fuck",
    "fk": "fuck", "fword": "fuck", "shhit": "shit", "shyt": "shit", "sht": "shit",
    "btch": "bitch", "bich": "bitch", "biatch": "bitch", "sx": "sex", "prn": "porn",
    "coke": "cocaine", "methamphetamines": "methamphetamine", "suicde": "suicide",
    "unalive": "suicide", "unalived": "killed", "selfharm": "selfharm",
}

RISK_PREFIXES = (
    "fuck", "motherfuck", "shit", "bitch", "asshol", "porn", "sexual",
    "rape", "molest", "cocaine", "heroin", "meth", "fentanyl", "overdos",
    "suicid", "murder", "behead", "decapitat", "kill", "shoot",
)


def _profanity_key(value: str) -> str:
    raw = str(value or "").lower().translate(str.maketrans({
        "@": "a", "4": "a", "3": "e", "1": "i", "!": "i",
        "0": "o", "$": "s", "5": "s", "7": "t",
    }))
    key = re.sub(r"[^a-z0-9]", "", raw)
    # ASR/social spellings often stretch characters. Collapse 2+ repeated
    # characters for matching, while aliases catch common intentional spellings.
    alias_key = RISK_WORD_ALIASES.get(key)
    if alias_key:
        return alias_key
    collapsed = re.sub(r"(.)\1+", r"\1", key)
    if collapsed != key:
        collapsed_alias = RISK_WORD_ALIASES.get(collapsed)
        if collapsed_alias:
            return collapsed_alias
        # Only use collapsed form if it maps to a known risk family.
        if collapsed in PROFANITY_WORDS or any(collapsed.startswith(p) for p in RISK_PREFIXES):
            return collapsed
    return RISK_WORD_ALIASES.get(key, key)


def _is_profanity(value: str) -> bool:
    key = _profanity_key(value)
    if key in PROFANITY_WORDS:
        return True
    return any(key.startswith(prefix) for prefix in RISK_PREFIXES)


def _censor_caption_word(value: str) -> str:
    raw = str(value or "")
    if not _is_profanity(raw):
        return raw
    key = _profanity_key(raw)
    if len(key) <= 2:
        return "***"
    if len(key) == 3:
        return key[0] + "**"
    # Keep recognition while removing the explicit spelling.
    return key[0] + ("*" * max(3, len(key) - 2)) + key[-1]


def _censor_risky_text(value: str) -> str:
    """Mask risky tokens in generated hook text without changing caption styling."""
    return re.sub(
        r"\b[\w@!$*#.-]+\b",
        lambda m: _censor_caption_word(m.group(0)) if _is_profanity(m.group(0)) else m.group(0),
        str(value or ""),
    )


def _caption_phrase_groups(words: List[ClipperWordTiming], max_words: int = 3) -> List[Tuple[int, int]]:
    """Stable phrase chunks so caption words do not jump between groups."""
    groups: List[Tuple[int, int]] = []
    start = 0
    for i, word in enumerate(words):
        count = i - start + 1
        gap = 0.0
        if i + 1 < len(words):
            gap = max(0.0, words[i + 1].start - word.end)
        punctuation_break = bool(re.search(r"[.!?,;:]$", word.text.strip()))
        if count >= max_words or gap >= 0.28 or punctuation_break:
            groups.append((start, i + 1))
            start = i + 1
    if start < len(words):
        groups.append((start, len(words)))
    return groups


def _profanity_intervals(words: List[ClipperWordTiming]) -> List[Tuple[float, float]]:
    """Return clip-local bleep windows using the exact word timestamps."""
    intervals: List[Tuple[float, float]] = []
    for word in words:
        if not _is_profanity(word.text):
            continue
        # Slightly wider than the ASR token to prevent consonants leaking at edges.
        start = max(0.0, float(word.start) - 0.060)
        end = max(start + 0.10, float(word.end) + 0.070)
        if intervals and start <= intervals[-1][1] + 0.06:
            intervals[-1] = (intervals[-1][0], max(intervals[-1][1], end))
        else:
            intervals.append((start, end))
    return intervals

def generate_animated_captions(
    word_timings: List[ClipperWordTiming],
    output_path: Path,
    template_key: str = "reference_green",
) -> Path:
    """Stable 2–3 word bold captions with exact active-word highlighting."""
    template = CAPTION_TEMPLATES.get(template_key, CAPTION_TEMPLATES["reference_green"])
    word_timings = _clean_clipper_word_timings(word_timings)
    events: List[str] = []
    primary = template["primary"]
    group_size = max(2, min(3, int(template.get("group_size", "3"))))
    # V8.18 reference style: heavy uppercase words with a thick black outline.
    # All words stay bold; the active green/yellow word gets a short size pop
    # and returns to the base size on the next word.  A tiny 45 ms visual lead
    # compensates for Whisper/render perception without retiming the audio.
    try:
        normal_fs = max(68, int(round(float(template["fontsize"]))))
    except Exception:
        normal_fs = 72
    active_fs = max(normal_fs + 22, int(round(normal_fs * 1.34)))
    caption_lead = 0.060

    for group_start, group_end in _caption_phrase_groups(word_timings, group_size):
        group = word_timings[group_start:group_end]
        if not group:
            continue
        phrase_start = max(0.0, group[0].start)
        phrase_end = max(phrase_start + 0.12, group[-1].end)

        for local_active, active_word in enumerate(group):
            event_start = max(0.0, max(phrase_start, active_word.start) - caption_lead)
            next_start = group[local_active + 1].start if local_active + 1 < len(group) else phrase_end
            event_end = max(event_start + 0.06, next_start - caption_lead)
            parts: List[str] = []
            for local_index, word in enumerate(group):
                global_index = group_start + local_index
                is_active = local_index == local_active
                color = _caption_active_color(template, word.text, global_index) if is_active else primary
                size_tag = rf"\fs{active_fs}" if is_active else rf"\fs{normal_fs}"
                # Reference uses a heavy face for every word; colour/size is the
                # animation, not a bold/non-bold toggle.
                weight = r"\b1"
                shown = _censor_caption_word(word.text).upper()
                parts.append(r"{\c" + color + weight + size_tag + r"}" + _clipper_ass_escape(shown))
            line = " ".join(parts)
            # 1080x1920 safe-title area: keep large captions inside the visible frame.
            # Two-word groups plus fixed margins prevent long phrases from clipping.
            visible_phrase = " ".join(_censor_caption_word(w.text) for w in group)
            if len(parts) == 2 and len(visible_phrase) > 12:
                line = parts[0] + r"\N" + parts[1]
            elif len(parts) == 1 and len(visible_phrase) > 11:
                # Preserve caption height/size while squeezing only the rare very
                # long single token horizontally into the safe-title area.
                line = r"{\fscx82}" + line
            events.append(
                f"Dialogue: 0,{_ass_time(event_start)},{_ass_time(event_end)},Caption,,0,0,0,,{line}"
            )

    shadow = template.get("shadow", "2")
    # Match the supplied caption reference: compact normal words, while the
    # currently highlighted green/yellow word pops larger and bold, then returns
    # immediately to the normal size on the next word.
    caption_fontsize = str(normal_fs)
    try:
        caption_outline = str(max(6.0, float(template["outline_size"]))).rstrip("0").rstrip(".")
    except Exception:
        caption_outline = "6"
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{template["font"]},{caption_fontsize},{template["primary"]},{template["highlight"]},{template["outline"]},{template["back"]},-1,0,0,0,100,100,0,0,1,{caption_outline},{shadow},2,115,115,{template["margin_v"]},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    output_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return output_path


def _clipper_crop_geometry(
    width: int,
    height: int,
    center_x_norm: float,
) -> Tuple[int, int, int, int]:
    width = max(2, width)
    height = max(2, height)

    target_ratio = 9.0 / 16.0
    source_ratio = width / height

    if source_ratio > target_ratio:
        crop_h = height - (height % 2)
        crop_w = int(round(crop_h * target_ratio))
        crop_w -= crop_w % 2
        desired_x = int(round(center_x_norm * width - crop_w / 2))
        x = max(0, min(width - crop_w, desired_x))
        x -= x % 2
        y = 0
    else:
        crop_w = width - (width % 2)
        crop_h = int(round(crop_w / target_ratio))
        crop_h -= crop_h % 2
        crop_h = min(crop_h, height - (height % 2))
        x = 0
        y = max(0, (height - crop_h) // 2)
        y -= y % 2

    return crop_w, crop_h, x, y


def build_auto_zoom_keyframes(
    paragraphs: List["TranscriptParagraph"],
    clip_start: float,
    clip_end: float,
    face_track: List[Tuple[float, float]],
) -> List[float]:
    """
    Generate subtle edit keyframes from sentence/thought boundaries and
    meaningful face-position changes.
    """
    duration = max(0.1, clip_end - clip_start)
    values: List[float] = []

    last_value = -999.0
    for paragraph in paragraphs:
        if paragraph.start <= clip_start or paragraph.start >= clip_end:
            continue
        relative = paragraph.start - clip_start
        if relative - last_value >= 3.8:
            values.append(relative)
            last_value = relative

    previous_center = None
    for time_value, center_value in face_track:
        if previous_center is not None and abs(
            center_value - previous_center
        ) >= 0.032:
            if all(abs(time_value - old) >= 2.0 for old in values):
                values.append(time_value)
        previous_center = center_value

    return sorted(
        value
        for value in values
        if 0.65 < value < duration - 0.45
    )[:10]


def _clipper_zoom_expression(
    fps: float,
    duration: float,
    keyframe_times: Optional[List[float]] = None,
) -> str:
    """Nearly static framing: avoids visible zoom pumping and motion sickness."""
    return "1.012"



def maybe_ai_upscale_clipper_source(
    source_path: Path,
    clip_start: float,
    clip_end: float,
    cancel_event: threading.Event,
) -> Tuple[Path, float, List[Path], str]:
    """
    AI-upscale only the selected short clip, never the entire YouTube source.

    To keep the VPS responsive, AI is attempted only when:
    - source height is below 1080p
    - the clip is within AUTO_CLIPPER_AI_UPSCALE_MAX_SECONDS

    Longer clips use high-quality Lanczos in the final render.
    """
    cleanup: List[Path] = []
    media = probe_media(source_path)
    height = int(media.get("height") or 0)
    duration = max(0.1, clip_end - clip_start)

    if (
        not AUTO_CLIPPER_AI_UPSCALE_LOW_RES
        or height >= 1080
        or duration > AUTO_CLIPPER_AI_UPSCALE_MAX_SECONDS
    ):
        return source_path, clip_start, cleanup, "native-source local render (no AI/4K upscale)"

    trimmed = DOWNLOAD_DIR / (
        f"clipper_ai_input_{uuid.uuid4().hex[:10]}.mp4"
    )
    run_process(
        [
            "ffmpeg", "-y",
            "-hide_banner", "-loglevel", "warning",
            "-ss", f"{clip_start:.3f}",
            "-t", f"{duration:.3f}",
            "-i", str(source_path),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "18",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(trimmed),
        ],
        cancel_event=cancel_event,
        error_prefix="Auto Clipper AI pre-trim failed",
        timeout_seconds=min(
            AUTO_CLIPPER_FFMPEG_TIMEOUT_SECONDS,
            max(120, int(duration * 8 + 90)),
        ),
    )
    cleanup.append(trimmed)

    enhanced, used_ai, note = ai_enhance_video(
        trimmed,
        cancel_event,
    )
    cleanup.append(enhanced)
    return (
        enhanced,
        0.0,
        cleanup,
        note if used_ai else f"AI fallback: {note}",
    )



def _track_value_at(
    track: List[Tuple[float, float]],
    time_value: float,
    default_value: float,
) -> float:
    """Linear offline interpolation, defensively ignoring malformed track rows."""
    clean: List[Tuple[float, float]] = []
    for item in track or []:
        try:
            if not isinstance(item, (tuple, list)) or len(item) < 2:
                continue
            t = float(item[0])
            value = float(item[1])
            if not (math.isfinite(t) and math.isfinite(value)):
                continue
            clean.append((t, value))
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
    if not clean:
        return float(default_value)
    clean.sort(key=lambda pair: pair[0])
    if time_value <= clean[0][0]:
        return clean[0][1]
    if time_value >= clean[-1][0]:
        return clean[-1][1]
    lo, hi = 0, len(clean) - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if clean[mid][0] <= time_value:
            lo = mid
        else:
            hi = mid
    t0, v0 = clean[lo]
    t1, v1 = clean[hi]
    if t1 <= t0 + 1e-9:
        return v1
    a = max(0.0, min(1.0, (time_value - t0) / (t1 - t0)))
    return v0 + (v1 - v0) * a


def _crop_axis_position(center_norm: float, dimension: int, crop_size: int, anchor: float) -> int:
    if dimension <= crop_size:
        return 0
    pos = float(center_norm) * dimension - crop_size * float(anchor)
    pos = max(0.0, min(float(dimension - crop_size), pos))
    value = int(round(pos))
    return max(0, min(dimension - crop_size, value - (value % 2)))


def _write_reframe_sendcmd(
    output_path: Path,
    plan: SmartReframePlan,
    width: int,
    height: int,
    duration: float,
    single_size: Tuple[int, int],
    split_primary_size: Tuple[int, int],
    split_secondary_size: Tuple[int, int],
    canvas_width: Optional[int] = None,
    canvas_height: Optional[int] = None,
    pad_x: int = 0,
    pad_y: int = 0,
    output_fps: Optional[float] = None,
) -> Path:
    """Write x/y camera commands on every output frame; stationary frames are compacted."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    single_w, single_h = single_size
    effective_width = max(width, int(canvas_width or width))
    effective_height = max(height, int(canvas_height or height))
    pad_x = max(0, int(pad_x))
    pad_y = max(0, int(pad_y))
    def map_x(center: float) -> float:
        if effective_width == width and pad_x == 0:
            return max(0.0, min(1.0, float(center)))
        return max(0.0, min(1.0, (pad_x + float(center) * width) / max(1.0, float(effective_width))))
    def map_y(center: float) -> float:
        if effective_height == height and pad_y == 0:
            return max(0.0, min(1.0, float(center)))
        return max(0.0, min(1.0, (pad_y + float(center) * height) / max(1.0, float(effective_height))))
    split_top_w, split_top_h = split_primary_size
    split_bottom_w, split_bottom_h = split_secondary_size
    single_x_track = plan.primary_track or [(0.0, 0.5), (duration, 0.5)]
    single_y_track = plan.primary_y_track or [(0.0, 0.40), (duration, 0.40)]
    left_x_track = plan.split_primary_track or single_x_track
    right_x_track = plan.split_secondary_track or single_x_track
    left_y_track = plan.split_primary_y_track or single_y_track
    right_y_track = plan.split_secondary_y_track or single_y_track
    # Preserve the source cadence instead of forcing 25fps podcasts to 30fps.
    # Forced 25->30 conversion creates a 5-frame duplicate cadence that looks like
    # tracking/video lag even when camera coordinates themselves are smooth.
    fps_value = float(output_fps or LOCAL_EDITOR_OUTPUT_FPS)
    if not math.isfinite(fps_value) or fps_value < 1.0 or fps_value > 240.0:
        fps_value = float(LOCAL_EDITOR_OUTPUT_FPS)
    frame_count = max(1, int(round(duration * fps_value))) + 1
    frame_times = [min(duration, frame_index / float(fps_value)) for frame_index in range(frame_count)]

    raw_values: Dict[Tuple[str, str], List[int]] = {
        ("crop@single", "x"): [], ("crop@single", "y"): [],
        ("crop@dual_top", "x"): [], ("crop@dual_top", "y"): [],
        ("crop@dual_bottom", "x"): [], ("crop@dual_bottom", "y"): [],
    }
    for t in frame_times:
        raw_values[("crop@single", "x")].append(_crop_axis_position(map_x(_track_value_at(single_x_track, t, 0.5)), effective_width, single_w, 0.50))
        raw_values[("crop@single", "y")].append(_crop_axis_position(map_y(_track_value_at(single_y_track, t, 0.40)), effective_height, single_h, 0.36))
        raw_values[("crop@dual_top", "x")].append(_crop_axis_position(map_x(_track_value_at(left_x_track, t, 0.30)), effective_width, split_top_w, 0.50))
        raw_values[("crop@dual_top", "y")].append(_crop_axis_position(map_y(_track_value_at(left_y_track, t, OPENCLIP_SPLIT_V_ALIGN)), effective_height, split_top_h, 0.36))
        raw_values[("crop@dual_bottom", "x")].append(_crop_axis_position(map_x(_track_value_at(right_x_track, t, 0.70)), effective_width, split_bottom_w, 0.50))
        raw_values[("crop@dual_bottom", "y")].append(_crop_axis_position(map_y(_track_value_at(right_y_track, t, OPENCLIP_SPLIT_V_ALIGN)), effective_height, split_bottom_h, 0.36))

    def _stable_pixels(values: List[int], axis_extent: int, axis: str) -> List[int]:
        """CapCut-style keyframes: hard cuts snap; body motion eases without lag.

        The editor must never animate a crop THROUGH a podcast camera cut. Large
        persistent jumps are segmented first and snap on the exact frame. Inside a
        shot, a tiny zero-phase filter removes detector chatter; Y requires more
        sustained travel than X so ordinary head bob does not bounce the frame.
        """
        if len(values) < 3:
            return [int(v) - (int(v) % 2) for v in values]
        vals = [float(v) for v in values]

        # Detect persistent step changes (scene/speaker cuts), not one-frame spikes.
        jump_threshold = max(90.0, axis_extent * (0.070 if axis == "x" else 0.055))
        cut_starts: List[int] = [0]
        for i in range(2, len(vals) - 2):
            prev = vals[max(0, i-3):i]
            nxt = vals[i:min(len(vals), i+4)]
            if len(prev) < 2 or len(nxt) < 3:
                continue
            pmed = float(sorted(prev)[len(prev)//2])
            nmed = float(sorted(nxt)[len(nxt)//2])
            nspread = max(nxt) - min(nxt)
            if abs(nmed - pmed) >= jump_threshold and nspread <= jump_threshold * 0.55:
                if i - cut_starts[-1] >= 3:
                    cut_starts.append(i)
        cut_starts.append(len(vals))

        def stabilize_segment(seg: List[float]) -> List[int]:
            if len(seg) <= 2:
                return [int(round(v)) - (int(round(v)) % 2) for v in seg]

            # V8.24 CAMERA MODEL
            #
            # Camera does NOT chase the speaker continuously.
            # It stays planted while the tracked body remains inside a safe zone.
            # Once genuine movement exits that zone, the crop starts moving on the
            # SAME frame and eases towards the new composition target.
            #
            # This behaves like sparse editor keyframes rather than continuous
            # detector-follow smoothing.

            # Remove only isolated one-frame detector spikes.
            med: List[float] = []
            for i in range(len(seg)):
                lo = max(0, i - 1)
                hi = min(len(seg), i + 2)
                local = sorted(seg[lo:hi])
                med.append(float(local[len(local)//2]))

            if axis == "x":
                # ~3.0% of source width.
                # Speaker can naturally move inside this box without camera motion.
                safe_zone = max(18.0, axis_extent * 0.030)

                # Once the body moves this much outside the locked composition,
                # camera must respond immediately.
                trigger_zone = max(24.0, axis_extent * 0.040)

                # Responsive ease. Higher than old laggy filters, but not a snap.
                follow_fast = 0.46
                follow_slow = 0.30
            else:
                # Y should be more stable than X. Ordinary talking/head bob should
                # never create vertical camera bouncing.
                safe_zone = max(18.0, axis_extent * 0.024)
                trigger_zone = max(28.0, axis_extent * 0.038)

                follow_fast = 0.40
                follow_slow = 0.25

            out: List[float] = [med[0]]
            camera = float(med[0])
            moving = False

            for i in range(1, len(med)):
                target = float(med[i])
                delta = target - camera
                distance = abs(delta)

                # Offline one-frame look-ahead does not introduce rendered delay.
                # It helps distinguish a real movement beginning NOW from a single
                # bad detector sample.
                future = float(med[min(len(med)-1, i+1)])
                future_distance = abs(future - camera)

                if not moving:
                    # Speaker is comfortably inside framing: CAMERA HARD HOLD.
                    if (
                        distance <= safe_zone
                        and future_distance <= trigger_zone
                    ):
                        out.append(camera)
                        continue

                    # Genuine movement has crossed the composition boundary.
                    # Start on this exact frame, not after a long smoothing window.
                    moving = True

                # While travelling, use distance-aware easing:
                # large body movement catches up quickly; final settling is softer.
                if distance >= trigger_zone * 2.0:
                    alpha = follow_fast
                else:
                    alpha = follow_slow

                camera += delta * alpha

                # Once the tracked body has returned close to the target and remains
                # there, lock again. This prevents endless floating/drift.
                remain = abs(target - camera)
                future_remain = abs(future - camera)

                if (
                    remain <= safe_zone * 0.42
                    and future_remain <= safe_zone * 0.70
                ):
                    camera = target
                    moving = False

                out.append(camera)

            # Remove any residual single-frame velocity twitch but preserve genuine
            # monotonic camera travel.
            refined = list(out)

            for i in range(1, len(out) - 1):
                v1 = out[i] - out[i-1]
                v2 = out[i+1] - out[i]

                if v1 * v2 < 0.0:
                    # Direction reversal lasting one frame = detector wobble.
                    if abs(v1) < safe_zone * 0.55 and abs(v2) < safe_zone * 0.55:
                        refined[i] = (out[i-1] + out[i+1]) * 0.5

            ints = [int(round(v)) for v in refined]
            return [v - (v % 2) for v in ints]

        result: List[int]=[]
        for a,b in zip(cut_starts,cut_starts[1:]):
            if b<=a: continue
            result.extend(stabilize_segment(vals[a:b]))
        return result[:len(values)]

    stable_values: Dict[Tuple[str, str], List[int]] = {}
    for key, vals in raw_values.items():
        axis_extent = effective_width if key[1] == "x" else effective_height
        stable_values[key] = _stable_pixels(vals, axis_extent, key[1])

    previous: Dict[Tuple[str, str], int] = {}
    lines: List[str] = []
    def emit(t: float, target: str, axis: str, value: int, force: bool = False) -> None:
        key = (target, axis)
        value = int(value) - (int(value) % 2)
        if not force and previous.get(key) == value:
            return
        previous[key] = value
        lines.append(f"{t:.6f} {target} {axis} {value};")

    for frame_index, t in enumerate(frame_times):
        force = frame_index == 0
        for target in ("crop@single", "crop@dual_top", "crop@dual_bottom"):
            emit(t, target, "x", stable_values[(target, "x")][frame_index], force)
            emit(t, target, "y", stable_values[(target, "y")][frame_index], force)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path




def _estimate_split_human_motion_anchor(
    source_path: Path,
    center_time: float,
    window_start: float,
    window_end: float,
    cancel_event: Optional[threading.Event] = None,
) -> Optional[Tuple[float, float]]:
    """Estimate the moving human's horizontal anchor without trusting face boxes.

    Podcast microphones/chairs/backgrounds are mostly static while the real speaker
    moves.  This guard samples a short stable-shot window, accumulates optical
    frame-difference energy only in the upper 62% of the image, and returns the
    dominant horizontal motion centroid plus a confidence score.  It is used only
    as a *validator/corrector* when a face track points far away from a strongly
    moving edge subject; it never invents a second speaker by itself.
    """
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return None
    event = cancel_event or threading.Event()
    cap = cv2.VideoCapture(str(source_path))
    if not cap.isOpened():
        return None
    try:
        a = max(0.0, float(window_start))
        b = max(a + 0.05, float(window_end))
        c = max(a + 0.18, min(b - 0.18, float(center_time))) if b - a >= 0.36 else (a + b) * 0.5
        radius = min(0.42, max(0.16, (b - a) * 0.12))
        times = [max(a, min(b, c + dt)) for dt in (-radius, -radius * 0.5, 0.0, radius * 0.5, radius)]
        frames = []
        for t in times:
            ensure_not_cancelled(event)
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None or getattr(frame, "size", 0) <= 0:
                continue
            h, w = frame.shape[:2]
            if h < 32 or w < 32:
                continue
            roi = frame[:max(32, int(round(h * 0.62))), :]
            target_w = 320
            target_h = max(32, int(round(roi.shape[0] * target_w / max(1, roi.shape[1]))))
            small = cv2.resize(roi, (target_w, target_h), interpolation=cv2.INTER_AREA)
            frames.append(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
        if len(frames) < 3:
            return None
        acc = np.zeros_like(frames[0], dtype=np.float32)
        for prev, cur in zip(frames, frames[1:]):
            acc += cv2.absdiff(prev, cur).astype(np.float32)
        acc = cv2.GaussianBlur(acc, (7, 7), 0)
        threshold = float(np.percentile(acc, 84.0))
        weights = np.maximum(acc - threshold, 0.0)
        total = float(weights.sum())
        if total <= 1e-3:
            return None
        # Six wide bins make the guard insensitive to individual moving fingers.
        bin_energy = []
        width = weights.shape[1]
        for i in range(6):
            x0 = int(round(width * i / 6.0)); x1 = int(round(width * (i + 1) / 6.0))
            bin_energy.append(float(weights[:, x0:x1].sum()))
        sorted_bins = sorted(bin_energy, reverse=True)
        peak = sorted_bins[0] if sorted_bins else 0.0
        runner_up = sorted_bins[1] if len(sorted_bins) > 1 else 0.0
        dominance = peak / max(1e-6, runner_up)
        peak_fraction = peak / max(1e-6, total)
        if dominance < 1.18 and peak_fraction < 0.28:
            return None
        xs = np.arange(width, dtype=np.float32)[None, :]
        cx = float((weights * xs).sum() / total / max(1, width))
        confidence = min(1.0, max(0.0, peak_fraction * 1.8 + max(0.0, dominance - 1.0) * 0.28))
        if not math.isfinite(cx) or not (0.0 <= cx <= 1.0):
            return None
        return cx, confidence
    except JobCancelled:
        raise
    except Exception:
        return None
    finally:
        cap.release()


def _build_split_human_motion_track(
    source_path: Path,
    start_time: float,
    duration: float,
    cancel_event: Optional[threading.Event] = None,
) -> Tuple[List[Tuple[float, float]], List[float]]:
    """Fast sequential moving-human X track for split-panel validation.

    Reads a stable reference shot once at ~5 Hz, so it is cheap even on 4K VPS
    sources. Static microphones/backgrounds contribute little temporal energy;
    genuine head/body movement produces a coherent horizontal motion centroid.
    """
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return [], []
    event = cancel_event or threading.Event()
    cap = cv2.VideoCapture(str(source_path))
    if not cap.isOpened():
        return [], []
    track: List[Tuple[float, float]] = []
    confs: List[float] = []
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 25.0
        sample_hz = 5.0
        step = max(1, int(round(fps / sample_hz)))
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(start_time)) * 1000.0)
        previous = None
        sample_index = 0
        max_samples = max(4, min(36, int(math.ceil(float(duration) * sample_hz)) + 2))
        while sample_index < max_samples:
            ensure_not_cancelled(event)
            if sample_index > 0:
                for _ in range(max(0, step - 1)):
                    if not cap.grab():
                        break
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            t_local = min(float(duration), sample_index * step / max(1.0, fps))
            h, w = frame.shape[:2]
            if h < 32 or w < 32:
                break
            roi = frame[:max(32, int(round(h * 0.62))), :]
            target_w = 320
            target_h = max(32, int(round(roi.shape[0] * target_w / max(1, roi.shape[1]))))
            gray = cv2.cvtColor(
                cv2.resize(roi, (target_w, target_h), interpolation=cv2.INTER_AREA),
                cv2.COLOR_BGR2GRAY,
            )
            if previous is not None:
                diff = cv2.absdiff(previous, gray).astype(np.float32)
                diff = cv2.GaussianBlur(diff, (7, 7), 0)
                threshold = float(np.percentile(diff, 84.0))
                weights = np.maximum(diff - threshold, 0.0)
                total = float(weights.sum())
                if total > 1e-3:
                    bins = []
                    for i in range(6):
                        x0 = int(round(target_w * i / 6.0)); x1 = int(round(target_w * (i + 1) / 6.0))
                        bins.append(float(weights[:, x0:x1].sum()))
                    ordered = sorted(bins, reverse=True)
                    peak = ordered[0] if ordered else 0.0
                    runner = ordered[1] if len(ordered) > 1 else 0.0
                    dominance = peak / max(1e-6, runner)
                    peak_fraction = peak / max(1e-6, total)
                    if dominance >= 1.12 or peak_fraction >= 0.27:
                        xs = np.arange(target_w, dtype=np.float32)[None, :]
                        cx = float((weights * xs).sum() / total / target_w)
                        confidence = min(1.0, peak_fraction * 1.8 + max(0.0, dominance - 1.0) * 0.28)
                        if math.isfinite(cx) and 0.0 <= cx <= 1.0:
                            track.append((t_local, cx)); confs.append(float(confidence))
            previous = gray
            sample_index += 1
            if t_local >= float(duration) - 0.04:
                break
        if len(track) >= 2:
            # suppress one-off hand/background spikes while retaining real leans
            track = _smooth_face_track(track, track[0][1], dimension_px=target_w, allow_virtual_center=False)
        return track, confs
    except JobCancelled:
        raise
    except Exception:
        return [], []
    finally:
        cap.release()

def _render_split_panel_live_fallback(
    source_path: Path,
    start_time: float,
    ref_duration: float,
    center_x: float,
    center_y: float,
    face_h: float,
    output_path: Path,
    cancel_event: Optional[threading.Event] = None,
) -> Optional[Path]:
    """Last-resort LIVE split panel renderer that does not depend on OpenCV/sendcmd.

    The input remains real moving video.  A blurred fill canvas is created and the
    real source is scaled/translated so the verified speaker anchor lands near the
    visual centre of the 1080x960 panel.  This path is intentionally codec-agnostic
    and FFmpeg-only; it exists so an already verified A/B conversation cannot die
    merely because the high-detail tracked panel renderer hit a VPS/codec/filter
    edge case.  Audio is never copied into reference panels.
    """
    event = cancel_event or threading.Event()
    try:
        ensure_not_cancelled(event)
        media = probe_media(source_path)
        w = max(2, int(media.get("width") or 0))
        h = max(2, int(media.get("height") or 0))
        fps = float(media.get("fps") or 0.0) or 25.0
        if w < 2 or h < 2 or ref_duration < 0.40:
            return None

        cx = float(center_x if math.isfinite(float(center_x)) else 0.5)
        cy = float(center_y if math.isfinite(float(center_y)) else 0.40)
        # Preserve virtual/off-source edge centres but stop pathological values.
        cx = max(-0.40, min(1.40, cx))
        cy = max(0.02, min(0.98, cy))
        fh = max(0.0, min(1.0, float(face_h or 0.0)))

        # Match the normal split composition: face ~34% of half-panel height,
        # otherwise default to a relaxed upper-body crop.
        crop_h = int(round(h * (fh / 0.34 if fh > 0.0 else 0.62)))
        crop_h = max(int(h * 0.38), min(int(h * 0.96), crop_h))
        crop_h = max(2, crop_h - crop_h % 2)
        scale = 960.0 / max(2.0, float(crop_h))
        fg_w = max(2, int(round(w * scale))); fg_w += fg_w % 2
        fg_h = max(2, int(round(h * scale))); fg_h += fg_h % 2

        # Place verified head anchor at x=50%, y~=36% just like the tracked path.
        overlay_x = int(round(540.0 - cx * fg_w))
        overlay_y = int(round(960.0 * 0.36 - cy * fg_h))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.unlink(missing_ok=True)
        filter_complex = (
            "[0:v]setpts=PTS-STARTPTS,split=2[fb_bg][fb_fg];"
            "[fb_bg]scale=1080:960:force_original_aspect_ratio=increase,"
            "crop=1080:960,gblur=sigma=24[fb_bg2];"
            f"[fb_fg]scale={fg_w}:{fg_h}:flags=lanczos[fb_fg2];"
            f"[fb_bg2][fb_fg2]overlay={overlay_x}:{overlay_y}:eof_action=pass,"
            "setsar=1[vref]"
        )
        run_process(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                "-ss", f"{max(0.0, float(start_time)):.6f}",
                "-t", f"{float(ref_duration):.6f}",
                "-i", str(source_path),
                "-filter_complex", filter_complex,
                "-map", "[vref]", "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                "-vsync", "0",
                "-t", f"{float(ref_duration):.6f}",
                str(output_path),
            ],
            cancel_event=event,
            error_prefix="Emergency live split panel render failed",
            timeout_seconds=max(120, int(ref_duration * 24 + 90)),
        )
        if not output_path.exists() or output_path.stat().st_size <= 4096:
            output_path.unlink(missing_ok=True)
            return None
        rendered = probe_media(output_path)
        got = float(rendered.get("duration") or 0.0)
        if got and got < min(0.65, float(ref_duration) * 0.60):
            output_path.unlink(missing_ok=True)
            return None
        return output_path
    except JobCancelled:
        output_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        logger.warning("Emergency live split panel fallback failed: %s", exc)
        output_path.unlink(missing_ok=True)
        return None


def _render_split_panel_bounded_fallback(
    source_path: Path,
    source_time: float,
    ref_duration: float,
    center_x: float,
    center_y: float,
    output_path: Path,
    cancel_event: Optional[threading.Event] = None,
) -> Optional[Path]:
    """Ultra-safe live A/B panel fallback with bounded intermediate dimensions.

    Unlike the older emergency renderer, this never scales a full 4K/portrait
    foreground according to a tiny detected face. The foreground is bounded to
    roughly panel resolution, then translated over a blurred live background so
    even edge/off-source face anchors can be placed near panel centre. This keeps
    memory predictable on VPS hosts while preserving real motion and source FPS.
    """
    event = cancel_event or threading.Event()
    try:
        ensure_not_cancelled(event)
        media = probe_media(source_path)
        w = max(2, int(media.get("width") or 0))
        h = max(2, int(media.get("height") or 0))
        fps = float(media.get("fps") or 0.0) or 25.0
        total = float(media.get("duration") or 0.0)
        if w < 2 or h < 2:
            return None
        duration = max(0.80, min(6.0, float(ref_duration or 4.8)))
        start = max(0.0, float(source_time or 0.0) - duration * 0.50)
        if total > 0.0:
            duration = min(duration, max(0.0, total))
            start = min(start, max(0.0, total - duration))
            duration = min(duration, max(0.0, total - start))
        if duration < 0.70:
            return None

        cx = max(-0.40, min(1.40, float(center_x if math.isfinite(float(center_x)) else 0.5)))
        cy = max(0.02, min(0.98, float(center_y if math.isfinite(float(center_y)) else 0.38)))

        # Bound the live foreground around final panel resolution. Portrait/tall
        # shots use panel width; landscape shots use a modest panel-height scale.
        if h >= w * 1.12:
            fg_w = 1080
            fg_h = max(2, int(round(h * (1080.0 / w))))
        else:
            fg_h = 1080
            fg_w = max(2, int(round(w * (1080.0 / h))))
        fg_w += fg_w % 2
        fg_h += fg_h % 2
        # Absolute safety cap for pathological panoramas/tall sources.
        if fg_w > 2400 or fg_h > 2400:
            shrink = min(2400.0 / fg_w, 2400.0 / fg_h)
            fg_w = max(2, int(fg_w * shrink)); fg_w += fg_w % 2
            fg_h = max(2, int(fg_h * shrink)); fg_h += fg_h % 2

        overlay_x = int(round(540.0 - cx * fg_w))
        overlay_y = int(round(960.0 * 0.36 - cy * fg_h))
        # Keep at least a sliver of real foreground on-canvas even if a noisy
        # virtual anchor is extreme; the blurred live background fills the rest.
        overlay_x = max(-fg_w + 96, min(984, overlay_x))
        overlay_y = max(-fg_h + 96, min(864, overlay_y))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.unlink(missing_ok=True)
        filter_complex = (
            "[0:v]setpts=PTS-STARTPTS,split=2[bbg][bfg];"
            "[bbg]scale=1080:960:force_original_aspect_ratio=increase,"
            "crop=1080:960,gblur=sigma=18[bbg2];"
            f"[bfg]scale={fg_w}:{fg_h}:flags=lanczos[bfg2];"
            f"[bbg2][bfg2]overlay={overlay_x}:{overlay_y}:shortest=1:eof_action=pass,"
            "setsar=1,format=yuv420p[vref]"
        )
        run_process(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                "-ss", f"{start:.6f}", "-t", f"{duration:.6f}",
                "-i", str(source_path),
                "-filter_complex", filter_complex,
                "-map", "[vref]", "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                "-vsync", "0", "-t", f"{duration:.6f}",
                str(output_path),
            ],
            cancel_event=event,
            error_prefix="Bounded live split panel render failed",
            timeout_seconds=max(90, int(duration * 18 + 60)),
        )
        if not output_path.exists() or output_path.stat().st_size <= 4096:
            output_path.unlink(missing_ok=True)
            return None
        rendered = probe_media(output_path)
        got = float(rendered.get("duration") or 0.0)
        if got and got < min(0.65, duration * 0.60):
            output_path.unlink(missing_ok=True)
            return None
        return output_path
    except JobCancelled:
        output_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        logger.warning("Bounded live split panel fallback failed: %s", exc)
        output_path.unlink(missing_ok=True)
        return None


def _legacy_unused_freeze_rendered_split_panel(
    panel_path: Path,
    duration: float = 5.0,
    cancel_event: Optional[threading.Event] = None,
) -> Optional[Path]:
    """Freeze an already centred split panel so split camera/lips never fake motion.

    Latest editor requirement is a stable 50/50 split: both speakers stay centred
    and the split itself must not pan/track. Freezing the verified portrait avoids
    unrelated reference lip motion looking out of sync with the original audio.
    """
    event = cancel_event or threading.Event()
    try:
        ensure_not_cancelled(event)
        media = probe_media(panel_path)
        fps = float(media.get("fps") or 0.0) or 25.0
        got_duration = float(media.get("duration") or 0.0)
        sample_t = max(0.0, min(max(0.0, got_duration - 0.08), got_duration * 0.50))
        frame_path = panel_path.with_name(panel_path.stem + "_freeze.jpg")
        tmp_path = panel_path.with_name(panel_path.stem + "_frozen.mp4")
        frame_path.unlink(missing_ok=True); tmp_path.unlink(missing_ok=True)
        run_process([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
            "-ss", f"{sample_t:.6f}", "-i", str(panel_path),
            "-frames:v", "1", "-q:v", "2", str(frame_path),
        ], cancel_event=event, error_prefix="Split freeze frame extraction failed", timeout_seconds=90)
        if not frame_path.exists() or frame_path.stat().st_size < 1024:
            return None
        run_process([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
            "-loop", "1", "-framerate", f"{fps:.6f}", "-i", str(frame_path),
            "-t", f"{max(4.0, float(duration)):.6f}", "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-vsync", "cfr",
            str(tmp_path),
        ], cancel_event=event, error_prefix="Frozen split panel render failed", timeout_seconds=120)
        if not tmp_path.exists() or tmp_path.stat().st_size < 4096:
            return None
        tmp_path.replace(panel_path)
        frame_path.unlink(missing_ok=True)
        return panel_path
    except JobCancelled:
        raise
    except Exception as exc:
        logger.warning("Split freeze failed: %s", exc)
        return None


def _audio_window_mean_db(
    source_path: Path,
    start_time: float,
    duration: float,
) -> Optional[float]:
    """Return mean volume for one source interval without changing the media."""
    try:
        proc = subprocess.run([
            "ffmpeg", "-hide_banner", "-nostats", "-v", "info",
            "-ss", f"{max(0.0, float(start_time)):.3f}",
            "-t", f"{max(0.25, float(duration)):.3f}",
            "-i", str(source_path), "-vn", "-af", "volumedetect", "-f", "null", "-",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=90)
        match = re.findall(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", proc.stderr or "")
        return float(match[-1]) if match else None
    except Exception:
        return None


def _retime_cross_splits_for_audible_dialogue(
    source_path: Path,
    source_start: float,
    clip_duration: float,
    intervals: List[Tuple[float, float]],
) -> List[Tuple[float, float]]:
    """Place cross-camera splits on strong original dialogue, never silence.

    A user can perceive a correctly mapped source track as "muted during split"
    when the visual split happens to be scheduled over a naturally quiet patch.
    We therefore keep occurrence count/order but jointly choose audible 5s windows
    with at least seven seconds between starts. The audio itself is never boosted,
    mixed or replaced.
    """
    if not intervals or clip_duration < 4.2:
        return intervals
    count = min(len(intervals), 8)
    win = 5.0 if clip_duration >= 5.0 else max(4.0, clip_duration)
    last_start = max(0.0, clip_duration - win - 0.20)
    candidates: List[Tuple[float, float]] = []
    t = 0.5
    while t <= last_start + 1e-6:
        db = _audio_window_mean_db(source_path, source_start + t, win)
        if db is not None and math.isfinite(db):
            candidates.append((round(t, 3), float(db)))
        t += 0.5
    if not candidates:
        return intervals
    levels = sorted(db for _, db in candidates)
    p80 = levels[min(len(levels)-1, int(round((len(levels)-1) * 0.80)))]
    good_floor = p80 - 12.0
    originals = [max(0.0, min(last_start, float(a))) for a, _ in intervals[:count]]

    # Prefer genuinely audible windows. If the source is uniformly quiet, retain
    # all candidates so we still find the strongest relative sections.
    good = [(t,db) for t,db in candidates if db >= good_floor]
    if len(good) < count:
        good = candidates

    # Dynamic programming: choose chronological non-overlapping occurrences with
    # strong volume and a small penalty for moving too far from the original plan.
    from functools import lru_cache
    times = [row[0] for row in good]
    next_index: List[int] = []
    for i, (ti, _) in enumerate(good):
        j = i + 1
        while j < len(good) and good[j][0] < ti + 7.0:
            j += 1
        next_index.append(j)

    @lru_cache(maxsize=None)
    def solve(i: int, chosen: int):
        if chosen >= count:
            return (0.0, ())
        if i >= len(good):
            return (-1e9, ())
        # Skip candidate.
        best_score, best_rows = solve(i + 1, chosen)
        # Take candidate as occurrence `chosen`.
        ti, dbi = good[i]
        future_score, future_rows = solve(next_index[i], chosen + 1)
        if future_score > -1e8:
            proximity_penalty = 0.08 * abs(ti - originals[chosen])
            take_score = dbi - proximity_penalty + future_score
            if take_score > best_score:
                best_score = take_score
                best_rows = (ti,) + future_rows
        return best_score, best_rows

    _, chosen_times = solve(0, 0)
    if len(chosen_times) != count:
        # Conservative fallback: keep planned windows rather than making overlaps.
        return [(max(0.0,float(a)), min(clip_duration,float(a)+win)) for a,_ in intervals[:count]]
    return [(float(ti), min(clip_duration, float(ti) + win)) for ti in chosen_times]


def _audio_window_pcm(
    source_path: Path,
    start_time: float,
    duration: float,
    sample_rate: int = 8000,
) -> Optional[Any]:
    """Decode a tiny mono PCM window for objective sync/gain QA."""
    try:
        import numpy as np  # type: ignore
        proc = subprocess.run([
            "ffmpeg", "-hide_banner", "-nostats", "-v", "error",
            "-ss", f"{max(0.0, float(start_time)):.3f}",
            "-t", f"{max(0.40, float(duration)):.3f}",
            "-i", str(source_path), "-vn", "-ac", "1", "-ar", str(int(sample_rate)),
            "-f", "s16le", "-",
        ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=90)
        if proc.returncode != 0 or not proc.stdout:
            return None
        return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception:
        return None


def _audio_sync_metrics(source_pcm: Any, output_pcm: Any, sample_rate: int = 8000) -> Optional[Tuple[float, float, float]]:
    """Return (lag_seconds, gain_delta_db, envelope_correlation)."""
    try:
        import numpy as np  # type: ignore
        n = min(len(source_pcm), len(output_pcm))
        if n < int(sample_rate * 0.40):
            return None
        x = np.asarray(source_pcm[:n], dtype=np.float32)
        y = np.asarray(output_pcm[:n], dtype=np.float32)
        xr = float(np.sqrt(np.mean(x * x) + 1e-12))
        yr = float(np.sqrt(np.mean(y * y) + 1e-12))
        src_db = 20.0 * math.log10(max(1e-9, xr))
        out_db = 20.0 * math.log10(max(1e-9, yr))
        gain_delta = out_db - src_db
        if src_db < -52.0:
            return (0.0, gain_delta, 1.0)

        # 100 Hz absolute-amplitude envelope keeps speech timing while ignoring
        # AAC phase/codec differences. Correlation is tiny and cheap (~250 points).
        block = max(1, int(sample_rate // 100))
        m = n // block
        if m < 40:
            return None
        xe = np.mean(np.abs(x[:m*block].reshape(m, block)), axis=1)
        ye = np.mean(np.abs(y[:m*block].reshape(m, block)), axis=1)
        xe = xe - float(np.mean(xe)); ye = ye - float(np.mean(ye))
        xn = float(np.linalg.norm(xe)); yn = float(np.linalg.norm(ye))
        if xn < 1e-8 or yn < 1e-8:
            return (0.0, gain_delta, 1.0)
        max_lag = min(50, max(1, m // 4))  # +/-0.50s
        best_corr = -2.0; best_lag = 0
        for lag in range(-max_lag, max_lag + 1):
            if lag < 0:
                xa = xe[-lag:]; ya = ye[:m + lag]
            elif lag > 0:
                xa = xe[:m - lag]; ya = ye[lag:]
            else:
                xa = xe; ya = ye
            if len(xa) < 20:
                continue
            denom = float(np.linalg.norm(xa) * np.linalg.norm(ya))
            corr = float(np.dot(xa, ya) / max(1e-9, denom))
            if corr > best_corr:
                best_corr = corr; best_lag = lag
        return (float(best_lag) / 100.0, gain_delta, best_corr)
    except Exception:
        return None


def _verify_split_audio_matches_source(
    source_path: Path,
    output_path: Path,
    source_start: float,
    intervals: List[Tuple[float, float]],
) -> Tuple[bool, List[str]]:
    """Verify split audio has same timing AND gain as original input-0 audio."""
    notes: List[str] = []
    for index, (a, b) in enumerate(intervals[:12], 1):
        event_len = max(0.5, float(b) - float(a))
        # Avoid exact transition edge; check a clean 2.4s interior window.
        sample_start = float(a) + min(0.35, event_len * 0.10)
        dur = min(2.4, max(0.65, event_len - 0.55))
        src_pcm = _audio_window_pcm(source_path, source_start + sample_start, dur)
        out_pcm = _audio_window_pcm(output_path, sample_start, dur)
        metrics = _audio_sync_metrics(src_pcm, out_pcm) if src_pcm is not None and out_pcm is not None else None
        if metrics is None:
            notes.append(f"split#{index} audio sync measurement unavailable")
            continue
        lag_s, delta_db, corr = metrics
        notes.append(
            f"split#{index} audio lag={lag_s:+.3f}s gain={delta_db:+.2f}dB corr={corr:.3f}"
        )
        if abs(lag_s) > 0.080:
            return False, notes
        if abs(delta_db) > 1.00:
            return False, notes
        if corr < 0.72:
            return False, notes
    return True, notes



def _build_static_reaction_panel(
    source_path: Path,
    source_time: float,
    output_path: Path,
    source_window: Optional[Tuple[float, float]] = None,
    cancel_event: Optional[threading.Event] = None,
    expected_x: Optional[float] = None,
    duration: float = 5.0,
) -> Optional[Path]:
    """Build one centred, silent listener portrait from a verified A/B shot.

    Cross-camera podcasts do not contain both speakers at the same timestamp.
    Therefore the non-visible speaker must NOT be represented by unrelated moving
    lips.  This helper scans only that identity's stable shot window, finds a real
    face with YuNet/dlib, centres it in a 9:8 panel, and loops that single reaction
    frame.  It never uses microphone/body motion as the primary anchor.
    """
    event = cancel_event or threading.Event()
    still_path = output_path.with_suffix('.reaction.jpg')
    try:
        import cv2  # type: ignore
        ensure_not_cancelled(event)
        cap = cv2.VideoCapture(str(source_path))
        if not cap.isOpened():
            return None
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 25.0
            count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
            source_duration = count / fps if count > 0 else 0.0
            if source_window is not None:
                a = max(0.0, float(source_window[0]))
                b = float(source_window[1])
                if source_duration > 0:
                    b = min(source_duration, b)
            else:
                a = max(0.0, float(source_time) - 1.8)
                b = float(source_time) + 1.8
                if source_duration > 0:
                    b = min(source_duration, b)
            if b <= a + 0.10:
                a = max(0.0, float(source_time) - 0.5)
                b = float(source_time) + 0.5

            try:
                detector, detector_kind = _create_face_detector(cv2)
            except Exception:
                detector, detector_kind = None, 'dlib_reaction'

            # Start at requested reference time, then fan out through the verified
            # stable identity window. This survives one blink/profile miss without
            # wandering into a different camera shot.
            centre = max(a, min(b, float(source_time)))
            times: List[float] = [centre]
            step = 0.40
            for k in range(1, 4):
                for sign in (-1.0, 1.0):
                    t = centre + sign * k * step
                    if a <= t <= b and all(abs(t-old) > 0.04 for old in times):
                        times.append(t)
                if len(times) >= 14:
                    break

            best_frame = None
            best_box = None
            best_score = -1e9
            for t in times:
                ensure_not_cancelled(event)
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                ok, frame = cap.read()
                if not ok or frame is None or getattr(frame, 'size', 0) <= 0:
                    continue
                h, w = frame.shape[:2]
                # Detect on a bounded proxy. Full 4K/portrait dlib scans can take
                # many seconds per frame on a VPS and previously made split setup
                # look hung. Normalised face boxes map back to the full frame.
                proxy = frame
                max_dim = max(h, w)
                if max_dim > 900:
                    scale = 900.0 / float(max_dim)
                    proxy = cv2.resize(
                        frame,
                        (max(2, int(round(w * scale))), max(2, int(round(h * scale)))),
                        interpolation=cv2.INTER_AREA,
                    )
                ph, pw = proxy.shape[:2]
                boxes: List[Tuple[float, float, float, float, float, float, float]] = []
                if detector is not None:
                    try:
                        boxes = _detect_face_boxes_for_reframe(proxy, detector, detector_kind, cv2)
                    except Exception:
                        boxes = []
                # dlib reflected-edge rescue is deliberately face-specific and is
                # much safer than using moving microphones/hands as an anchor.
                try:
                    dlib_boxes = _dlib_edge_face_boxes(frame, cv2)
                except Exception:
                    dlib_boxes = []
                if dlib_boxes:
                    boxes = list(boxes) + list(dlib_boxes)

                plausible = []
                for box in boxes:
                    cx, cy, area, nx, ny, nw, nh = box
                    px_aspect = (float(nw) * pw) / max(1e-6, float(nh) * ph)
                    if (
                        float(area) >= 0.0018
                        and float(nh) >= 0.040
                        and float(cy) <= 0.52
                        and 0.28 <= px_aspect <= 1.60
                    ):
                        plausible.append(box)
                for box in plausible:
                    cx, cy, area, nx, ny, nw, nh = box
                    # Face size dominates. Expected identity X is only a weak tie
                    # breaker because the parent track itself may be the thing we
                    # are rescuing.
                    x_penalty = abs(float(cx) - float(expected_x)) if expected_x is not None else 0.0
                    score = min(float(area), 0.055) * 7.0 + min(float(nh), 0.25) * 1.5 - x_penalty * 0.18 - abs(float(cy)-0.34)*0.18
                    if score > best_score:
                        best_score = score
                        best_frame = frame.copy()
                        best_box = tuple(float(v) for v in box)
            if best_frame is None or best_box is None:
                return None
        finally:
            cap.release()

        frame = best_frame
        h, w = frame.shape[:2]
        cx, cy, area, nx, ny, nw, nh = best_box
        target_face_fraction = 0.34
        crop_h = int(round(h * (float(nh) / target_face_fraction)))
        crop_h = max(int(h * 0.40), min(int(h * 0.94), crop_h))
        crop_h = max(2, crop_h - (crop_h % 2))
        crop_w = int(round(crop_h * 1.125))
        crop_w = max(2, crop_w - (crop_w % 2))
        if crop_w > int(w * 1.35):
            crop_w = int(w * 1.35); crop_w -= crop_w % 2

        face_px_x = float(cx) * w
        face_px_y = float(cy) * h
        desired_x = face_px_x - crop_w * 0.50
        desired_y = face_px_y - crop_h * 0.36

        # If an edge-clipped face cannot be centred with a physical crop, extend
        # ONLY the missing horizontal area with a blurred canvas. Never mirror the
        # person/microphone: reflection creates duplicate faces and a fake look.
        # Y remains entirely inside genuine source pixels.
        desired_y = max(0.0, min(max(0.0, float(h - crop_h)), desired_y))
        sx0 = max(0.0, desired_x)
        sx1 = min(float(w), desired_x + float(crop_w))
        sy0 = max(0.0, desired_y)
        sy1 = min(float(h), desired_y + float(crop_h))
        if sx1 <= sx0 + 1.0 or sy1 <= sy0 + 1.0:
            return None

        # Background extension is deliberately soft/defocused and only visible
        # where real source pixels do not exist. The foreground itself is never
        # blurred, so the speaker remains crisp.
        panel = cv2.resize(frame, (1080, 960), interpolation=cv2.INTER_AREA)
        panel = cv2.GaussianBlur(panel, (0, 0), sigmaX=26.0, sigmaY=26.0)

        ox0 = int(round((sx0 - desired_x) / max(1.0, float(crop_w)) * 1080.0))
        ox1 = int(round((sx1 - desired_x) / max(1.0, float(crop_w)) * 1080.0))
        oy0 = int(round((sy0 - desired_y) / max(1.0, float(crop_h)) * 960.0))
        oy1 = int(round((sy1 - desired_y) / max(1.0, float(crop_h)) * 960.0))
        ox0 = max(0, min(1079, ox0)); ox1 = max(ox0 + 1, min(1080, ox1))
        oy0 = max(0, min(959, oy0)); oy1 = max(oy0 + 1, min(960, oy1))

        patch = frame[
            int(round(sy0)):int(round(sy1)),
            int(round(sx0)):int(round(sx1)),
        ]
        if patch is None or getattr(patch, 'size', 0) <= 0:
            return None
        patch = cv2.resize(
            patch, (ox1 - ox0, oy1 - oy0), interpolation=cv2.INTER_LANCZOS4
        )
        panel[oy0:oy1, ox0:ox1] = patch
        still_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(still_path), panel, [int(cv2.IMWRITE_JPEG_QUALITY), 94]):
            return None

        output_path.unlink(missing_ok=True)
        run_process([
            'ffmpeg','-y','-hide_banner','-loglevel','warning',
            '-loop','1','-framerate',f'{max(1.0,min(60.0,fps)):.6f}','-i',str(still_path),
            '-t',f'{max(4.0,float(duration)):.6f}','-an',
            '-c:v','libx264','-preset','veryfast','-crf','18','-pix_fmt','yuv420p',
            '-movflags','+faststart','-vsync','cfr',str(output_path),
        ], cancel_event=event, error_prefix='Static reaction panel render failed', timeout_seconds=120)
        if not output_path.exists() or output_path.stat().st_size <= 4096:
            return None
        # Final face-centre proof on the actual encoded panel.
        measured = _measure_split_panel_face_center(output_path)
        if measured is not None and not (0.43 <= float(measured) <= 0.57):
            logger.warning('Static reaction face centre %.3f outside target band', measured)
            return None
        return output_path
    except JobCancelled:
        raise
    except Exception as exc:
        logger.warning('Static reaction panel build failed: %s', exc)
        output_path.unlink(missing_ok=True)
        return None
    finally:
        try:
            still_path.unlink(missing_ok=True)
        except Exception:
            pass

def _extract_split_reference_panel(
    source_path: Path,
    source_time: float,
    center_x: float,
    center_y: float,
    face_h: float,
    output_path: Path,
    source_window: Optional[Tuple[float, float]] = None,
    cancel_event: Optional[threading.Event] = None,
    source_x_track: Optional[List[Tuple[float, float]]] = None,
    source_y_track: Optional[List[Tuple[float, float]]] = None,
    body_x_track: Optional[List[Tuple[float, float]]] = None,
    body_y_track: Optional[List[Tuple[float, float]]] = None,
) -> Optional[Path]:
    """Create a MOVING, independently tracked 9:8 cross-camera speaker panel.

    The reference is restricted to a stable run belonging to one appearance
    identity, then re-analysed locally so X/Y crop coordinates follow that
    speaker inside the panel.  It is video (looped only if the split outlasts the
    stable source run), never a frozen PNG, and carries no audio.
    """
    try:
        # Do not gate split-panel generation on OpenCV's codec support. yt-dlp can
        # legitimately deliver AV1/VP9/10-bit files that FFmpeg decodes correctly
        # while cv2.VideoCapture on a headless VPS refuses to open them. V8.2 used
        # that OpenCV open as a hard precondition, so the planner could verify two
        # speakers and then fail both moving panels before FFmpeg was even tried.
        # ffprobe/probe_media is the source of truth for timing/geometry here.
        event = cancel_event or threading.Event()
        source_media = probe_media(source_path)
        fps = float(source_media.get("fps") or 0.0) or 25.0
        source_duration = float(source_media.get("duration") or 0.0)
        w = int(source_media.get("width") or 0)
        h = int(source_media.get("height") or 0)
        if w < 2 or h < 2:
            logger.warning(
                "Cross-camera split reference has invalid source geometry: %sx%s", w, h
            )
            return None

        valid_window = False
        window_start, window_end = 0.0, source_duration
        if source_window is not None:
            try:
                window_start = max(0.0, float(source_window[0]))
                window_end = float(source_window[1])
                if source_duration > 0:
                    window_end = min(source_duration, window_end)
                valid_window = window_end - window_start >= 0.80
            except Exception:
                valid_window = False
        if valid_window:
            ref_duration = min(7.5, window_end - window_start)
            start_time = max(window_start, float(source_time) - ref_duration * 0.50)
            start_time = min(start_time, max(window_start, window_end - ref_duration))
        else:
            ref_duration = 6.2
            start_time = max(0.0, float(source_time) - 1.0)
            if source_duration > ref_duration:
                start_time = min(start_time, max(0.0, source_duration - ref_duration))
        if source_duration > 0:
            ref_duration = min(ref_duration, max(0.0, source_duration - start_time))
        if ref_duration < 0.80:
            return None

        # V8.20: score the verified identity run with ONE sequential FFmpeg pass
        # and choose the liveliest continuous sub-window.  Older builds selected
        # a midpoint reference even when that speaker barely moved, which looked
        # exactly like a frozen TOP/BOTTOM panel.
        def _gray_motion_series(path_value: Path, abs_start: float, seconds: float, sample_fps: float = 2.0):
            try:
                sw, sh = 144, 112
                proc = subprocess.run([
                    'ffmpeg','-hide_banner','-loglevel','error',
                    '-ss',f'{max(0.0,float(abs_start)):.6f}','-t',f'{max(0.25,float(seconds)):.6f}',
                    '-i',str(path_value),'-an','-vf',f'fps={sample_fps:.3f},scale={sw}:{sh}:flags=area,format=gray',
                    '-f','rawvideo','-pix_fmt','gray','-'
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=max(30, int(seconds*4+20)))
                raw = proc.stdout or b''
                frame_bytes = sw*sh
                count = len(raw)//frame_bytes
                if count < 2:
                    return []
                import numpy as np  # type: ignore
                arr = np.frombuffer(raw[:count*frame_bytes], dtype=np.uint8).reshape(count, sh, sw)
                vals = []
                for kk in range(1, count):
                    vals.append(float(np.abs(arr[kk].astype(np.int16)-arr[kk-1].astype(np.int16)).mean()))
                return vals
            except Exception:
                return []

        def _motion_value(vals):
            clean = sorted(float(v) for v in (vals or []) if math.isfinite(float(v)))
            if not clean:
                return 0.0
            med = clean[len(clean)//2]
            q3 = clean[min(len(clean)-1, int(round((len(clean)-1)*0.75)))]
            return float(med*0.72 + q3*0.28)

        if valid_window and window_end - window_start > ref_duration + 0.20:
            run_seconds = max(0.25, window_end-window_start)
            series = _gray_motion_series(source_path, window_start, run_seconds, 2.0)
            if series:
                # Each diff spans 0.5s at 2fps. Evaluate continuous windows and
                # keep them wholly inside the already-verified identity run.
                diffs_per_window = max(2, int(round(ref_duration*2.0))-1)
                best_score = -1.0
                best_index = 0
                max_i = max(0, len(series)-diffs_per_window)
                for ii in range(max_i+1):
                    score = _motion_value(series[ii:ii+diffs_per_window])
                    if score > best_score:
                        best_score = score; best_index = ii
                best_start = window_start + best_index/2.0
                best_start = max(window_start, min(max(window_start, window_end-ref_duration), best_start))
                # Preserve the planner choice if it is already lively enough;
                # otherwise move to the strongest same-identity section.
                orig_index = max(0, int(round((start_time-window_start)*2.0)))
                original_score = _motion_value(series[orig_index:orig_index+diffs_per_window])
                if best_score > original_score + 0.10 or original_score < 0.70:
                    logger.info('Split live-reference motion select %.3f -> %.3f (%.3f -> %.3f)', start_time, best_start, original_score, best_score)
                    start_time = best_start

        # V8.3 reliability rule: DO NOT run a second full face/body analysis here.
        # The parent planner has already verified both speaker identities and built
        # the crop tracks. Re-detecting the same short shot was the fragile step
        # that could return None after A/B verification (profile/mic/codec/VPS load).
        # Slice the already-verified identity track and blend the parent body-follow
        # residual. This keeps the panel genuinely moving while making panel build
        # deterministic and FFmpeg-first.
        local_x = [(0.0, float(center_x)), (ref_duration, float(center_x))]
        local_y = [(0.0, float(center_y)), (ref_duration, float(center_y))]
        local_face_h = float(face_h or 0.0)

        def _slice_abs_track(
            track_value: Optional[List[Tuple[float, float]]],
            default_value: float,
        ) -> List[Tuple[float, float]]:
            clean: List[Tuple[float, float]] = []
            for row in track_value or []:
                try:
                    t_abs = float(row[0]); value = float(row[1])
                except Exception:
                    continue
                if not (math.isfinite(t_abs) and math.isfinite(value)):
                    continue
                if start_time - 0.35 <= t_abs <= start_time + ref_duration + 0.35:
                    clean.append((max(0.0, min(ref_duration, t_abs - start_time)), value))
            # Always pin exact endpoints from the full absolute track so sparse
            # identity detections still interpolate smoothly through the whole run.
            if track_value:
                clean.append((0.0, _track_value_at(track_value, start_time, default_value)))
                clean.append((ref_duration, _track_value_at(track_value, start_time + ref_duration, default_value)))
            if not clean:
                return [(0.0, default_value), (ref_duration, default_value)]
            clean.sort(key=lambda pair: pair[0])
            dedup: List[Tuple[float, float]] = []
            for row in clean:
                if dedup and abs(row[0] - dedup[-1][0]) < 1e-4:
                    dedup[-1] = row
                else:
                    dedup.append(row)
            return dedup

        face_x = _slice_abs_track(source_x_track, float(center_x))
        face_y = _slice_abs_track(source_y_track, float(center_y))
        body_x = _slice_abs_track(body_x_track, float(center_x)) if body_x_track else []
        body_y = _slice_abs_track(body_y_track, float(center_y)) if body_y_track else []

        # Independent moving-human guard. One sequential ~5 Hz pass over the
        # already-verified stable shot rejects static microphone/background false
        # anchors without adding expensive random seeks or a second full detector.
        motion_guard, motion_guard_conf = _build_split_human_motion_track(
            source_path, start_time, ref_duration, event
        )
        if motion_guard:
            gmed = float(statistics.median([v for _, v in motion_guard]))
            fmed = float(statistics.median([v for _, v in face_x])) if face_x else float(center_x)
            gconf = float(statistics.median(motion_guard_conf)) if motion_guard_conf else 0.0
            # Face identity is the primary anchor. Never let optical-motion
            # energy (microphone/hand/background movement) replace an existing
            # verified face track. Motion rescue is allowed only when the parent
            # identity track is genuinely unavailable.
            parent_face_available = bool(source_x_track and len(source_x_track) >= 2)
            replace_face_x = (
                not parent_face_available
                and abs(gmed - fmed) >= 0.18
                and (gmed <= 0.28 or gmed >= 0.72 or gconf >= 0.62)
            )
            if replace_face_x:
                motion_guard = [(0.0, motion_guard[0][1]), *motion_guard, (ref_duration, motion_guard[-1][1])]
                face_x = _smooth_face_track(
                    motion_guard, gmed, dimension_px=w, allow_virtual_center=True
                )
                fymed = float(statistics.median([v for _, v in face_y])) if face_y else float(center_y)
                if fymed > 0.43 or fymed < 0.06:
                    face_y = [(0.0, 0.28), (ref_duration, 0.28)]
                local_face_h = 0.0
                logger.info(
                    "Split human-motion guard corrected face anchor %.3f -> %.3f (conf=%.2f)",
                    fmed, gmed, gconf,
                )

        # Lightweight high-resolution face rescue for the selected stable shot.
        # This is deliberately NOT another full reframe analysis.  The shot has
        # already been assigned to one verified identity; we only sample the
        # largest real face at ~5 Hz so an edge-clipped/profile head is centred
        # accurately inside its split half.  It also prevents a bad parent sample
        # (mic/hand/background) from poisoning the reference crop.
        sampled_face_x: List[Tuple[float, float]] = []
        sampled_face_y: List[Tuple[float, float]] = []
        sampled_face_h: List[float] = []
        # The parent A/B planner already supplies verified identity tracks in
        # production. Re-running OpenCV/dlib on every high-resolution reference
        # shot was both redundant and a major VPS failure/timeout surface. Only
        # rescue when no usable parent identity track exists.
        parent_face_track_ok = bool(source_x_track and len(source_x_track) >= 2)
        if not parent_face_track_ok:
            try:
                import cv2  # type: ignore
                cap_ref = cv2.VideoCapture(str(source_path))
                if cap_ref.isOpened():
                    # Adaptive sparse scan: profile faces can disappear on one exact
                    # timestamp, so probe at 0.5 s steps until four real-face anchors
                    # are found. The hard cap keeps this cheap on a VPS while avoiding
                    # an unlucky fixed grid (the supplied B-camera misses at 35.5 s
                    # but detects cleanly at 35.0/36.0 s).
                    rescue_detector = rescue_kind = None
                    try:
                        rescue_detector, rescue_kind = _create_face_detector(cv2)
                    except Exception:
                        rescue_detector = rescue_kind = None
                    probe_step = 0.50
                    max_probes = min(14, max(5, int(math.ceil(ref_duration / probe_step)) + 1))
                    for probe_index in range(max_probes):
                        if len(sampled_face_x) >= 4:
                            break
                        t_local = min(max(0.0, ref_duration - 0.04), probe_index * probe_step)
                        ensure_not_cancelled(event)
                        cap_ref.set(cv2.CAP_PROP_POS_MSEC, (start_time + t_local) * 1000.0)
                        ok_ref, frame_ref = cap_ref.read()
                        if not ok_ref or frame_ref is None:
                            if t_local >= ref_duration - 0.05:
                                break
                            continue

                        boxes_ref = []
                        # YuNet rows are landmark-validated and safe. Never allow the
                        # Haar emergency detector to steer split references: on the
                        # supplied podcast it repeatedly identifies the microphone/
                        # clothing near x~=0.39 as a face. Dlib edge/profile rescue is
                        # the safe fallback when YuNet is unavailable or misses.
                        if rescue_detector is not None and rescue_kind == "yunet":
                            try:
                                boxes_ref = _detect_face_boxes_for_reframe(
                                    frame_ref, rescue_detector, rescue_kind, cv2
                                )
                            except Exception:
                                boxes_ref = []
                        if not boxes_ref:
                            try:
                                boxes_ref = _dlib_edge_face_boxes(frame_ref, cv2)
                            except Exception:
                                boxes_ref = []
                        if boxes_ref:
                            chosen_ref = max(boxes_ref, key=lambda row: float(row[2]))
                            sampled_face_x.append((float(t_local), float(chosen_ref[0])))
                            sampled_face_y.append((float(t_local), float(chosen_ref[1])))
                            sampled_face_h.append(float(chosen_ref[6]))
                        if t_local >= ref_duration - 0.05:
                            break
                    cap_ref.release()
            except Exception as face_rescue_exc:
                logger.debug("Split reference face-rescue skipped: %s", face_rescue_exc)
        if len(sampled_face_x) >= 2:
            # Pin endpoints so interpolation remains defined across brief misses.
            sx0 = _track_value_at(sampled_face_x, 0.0, sampled_face_x[0][1])
            sy0 = _track_value_at(sampled_face_y, 0.0, sampled_face_y[0][1])
            sx1 = _track_value_at(sampled_face_x, ref_duration, sampled_face_x[-1][1])
            sy1 = _track_value_at(sampled_face_y, ref_duration, sampled_face_y[-1][1])
            sampled_face_x.extend([(0.0, sx0), (ref_duration, sx1)])
            sampled_face_y.extend([(0.0, sy0), (ref_duration, sy1)])
            face_x = _smooth_face_track(
                sampled_face_x, sx0, dimension_px=w, allow_virtual_center=True
            )
            face_y = _smooth_face_track(
                sampled_face_y, sy0, dimension_px=h
            )
            if sampled_face_h:
                local_face_h = float(statistics.median(sampled_face_h))

        # Face remains the identity/centering anchor. Parent upper-body camera
        # motion contributes only a bounded residual, preserving genuine leans
        # without allowing microphones/background texture to steer the panel.
        fused_x: List[Tuple[float, float]] = []
        fused_y: List[Tuple[float, float]] = []
        fusion_times = sorted({float(t) for t, _ in face_x} | {float(t) for t, _ in body_x})
        if not fusion_times:
            fusion_times = [0.0, ref_duration]
        for t_local in fusion_times:
            fx = _track_value_at(face_x, t_local, float(center_x))
            fy = _track_value_at(face_y, t_local, float(center_y))
            bx = _track_value_at(body_x, t_local, fx) if body_x else fx
            by = _track_value_at(body_y, t_local, fy) if body_y else fy
            raw_dx = bx - fx
            raw_dy = by - fy
            # Only trust body residual while it remains face-gated. A stale body
            # tracker on a microphone/empty area must never drag the face away.
            dx = max(-0.075, min(0.075, raw_dx)) if abs(raw_dx) <= 0.18 else 0.0
            dy = max(-0.100, min(0.100, raw_dy)) if abs(raw_dy) <= 0.22 else 0.0
            fused_x.append((t_local, fx + dx * 0.68))
            fused_y.append((t_local, fy + dy * 0.52))
        local_x = _smooth_face_track(
            fused_x, float(center_x), dimension_px=w, allow_virtual_center=True
        ) if fused_x else local_x
        local_y = _smooth_face_track(
            fused_y, float(center_y), dimension_px=h
        ) if fused_y else local_y

        # V8.14 split rule: VIDEO remains live, CAMERA remains static. Pick one
        # robust face/body-centred crop from the verified reference run and hold
        # those crop coordinates for the entire panel. Do NOT freeze frames.
        # This preserves natural lips/hands/body motion without split-camera jitter.
        static_x_values = [float(v) for _, v in (local_x or []) if math.isfinite(float(v))]
        static_y_values = [float(v) for _, v in (local_y or []) if math.isfinite(float(v))]
        static_x = float(statistics.median(static_x_values)) if static_x_values else float(center_x)
        static_y = float(statistics.median(static_y_values)) if static_y_values else float(center_y)
        local_x = [(0.0, static_x), (ref_duration, static_x)]
        local_y = [(0.0, static_y), (ref_duration, static_y)]

        local_face_h = max(0.0, min(1.0, local_face_h))
        # Reference layout is an upper-body podcast composition, not a tight face
        # close-up. Keeping the detector face at ~34% of the half-panel height
        # matches the supplied 50/50 reference and leaves room for shoulders/hands.
        crop_h = int(round(h * (local_face_h / 0.34 if local_face_h > 0.0 else 0.62)))
        crop_h = max(int(h * 0.38), min(int(h * 0.96), crop_h))
        crop_w = int(round(crop_h * 1.125))
        crop_w = max(2, min(w, crop_w)); crop_w -= crop_w % 2
        crop_h = max(2, min(h, crop_h)); crop_h -= crop_h % 2
        local_centers = [float(c) for _, c in (local_x or [])] or [float(center_x)]
        min_local = min(local_centers); max_local = max(local_centers)
        required_left = max(0.0, crop_w * 0.50 - min_local * w)
        required_right = max(0.0, crop_w * 0.50 - (1.0 - max_local) * w)
        pad_x = int(round(max(w * 0.14, required_left, required_right) + w * 0.035))
        pad_x = min(int(round(w * SMART_REFRAME_VIRTUAL_CANVAS_MAX_PAD)), max(2, pad_x))
        pad_x -= pad_x % 2
        pw = w + pad_x * 2

        # No blurred top/bottom extension in split mode either. The static Y crop
        # is clamped to genuine source pixels; only horizontal edge padding may be
        # used when a clipped profile cannot physically be centred otherwise.
        pad_y = 0
        ph = h

        # Render the moving panel with the SAME FFmpeg stack used by delivery.
        # OpenCV VideoWriter/mp4v is not reliable on headless VPS builds and used
        # to fail silently, which meant cross-camera split intervals existed in
        # the planner but the final video quietly fell back to single-camera.
        # sendcmd now applies the locally tracked X/Y crop on every source frame.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command_path = output_path.with_suffix(".camera.cmd")
        render_fps = float(fps if 1.0 <= fps <= 120.0 else 25.0)
        frame_count = max(2, int(round(ref_duration * render_fps)) + 1)
        previous_xy: Dict[str, int] = {}
        command_lines: List[str] = []

        def emit_ref(t_value: float, axis: str, value: int, force: bool = False) -> None:
            value = int(value) - (int(value) % 2)
            if not force and previous_xy.get(axis) == value:
                return
            previous_xy[axis] = value
            command_lines.append(f"{t_value:.6f} crop@refpanel {axis} {value};")

        x0 = y0 = 0
        for frame_index in range(frame_count):
            t_local = min(ref_duration, frame_index / max(1.0, render_fps))
            cx_norm = float(_track_value_at(local_x, t_local, float(center_x)))
            cy_norm = float(_track_value_at(local_y, t_local, float(center_y)))
            # Virtual face centres are intentional for edge-clipped speakers.
            # Do not clamp cx_norm to [0,1]; the reflected side canvas exists so
            # an off-source head centre can still land at panel centre.
            cx = float(pad_x) + cx_norm * float(w)
            cy = float(pad_y) + max(0.0, min(1.0, cy_norm)) * float(h)
            x1 = int(round(cx - crop_w * 0.50))
            y1 = int(round(cy - crop_h * 0.36))
            x1 = max(0, min(pw - crop_w, x1)); x1 -= x1 % 2
            y1 = max(0, min(ph - crop_h, y1)); y1 -= y1 % 2
            if frame_index == 0:
                x0, y0 = x1, y1
            emit_ref(t_local, "x", x1, frame_index == 0)
            emit_ref(t_local, "y", y1, frame_index == 0)
        command_path.write_text("\n".join(command_lines) + "\n", encoding="utf-8")
        cmd_path = str(command_path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        filter_complex = (
            "[0:v]setpts=PTS-STARTPTS,split=2[refbg0][reffg0];"
            f"[refbg0]scale={pw}:{ph}:force_original_aspect_ratio=increase,"
            f"crop={pw}:{ph},gblur=sigma=24[refbg1];"
            f"[refbg1][reffg0]overlay={pad_x}:{pad_y},sendcmd=f='{cmd_path}',"
            f"crop@refpanel={crop_w}:{crop_h}:x={x0}:y={y0},"
            "scale=1080:960:flags=lanczos,setsar=1[vref]"
        )
        tracked_error: Optional[Exception] = None
        try:
            run_process(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                    "-ss", f"{start_time:.6f}", "-t", f"{ref_duration:.6f}",
                    "-i", str(source_path),
                    "-filter_complex", filter_complex,
                    "-map", "[vref]", "-an",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "10",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    "-t", f"{ref_duration:.6f}",
                    "-vsync", "0",
                    str(output_path),
                ],
                cancel_event=event,
                error_prefix="Cross-camera moving split panel render failed",
                timeout_seconds=max(90, int(ref_duration * 18 + 60)),
            )
        except JobCancelled:
            raise
        except Exception as exc:
            tracked_error = exc
            logger.warning(
                "Tracked split panel failed; retrying deterministic live FFmpeg fallback: %s",
                exc,
            )
        finally:
            try:
                command_path.unlink(missing_ok=True)
            except Exception:
                pass

        if tracked_error is not None or not output_path.exists() or output_path.stat().st_size <= 4096:
            output_path.unlink(missing_ok=True)
            emergency = _render_split_panel_live_fallback(
                source_path, start_time, ref_duration,
                _track_value_at(local_x, ref_duration * 0.50, float(center_x)),
                _track_value_at(local_y, ref_duration * 0.50, float(center_y)),
                local_face_h, output_path, event,
            )
            if emergency is None:
                bounded = _render_split_panel_bounded_fallback(
                    source_path, start_time + ref_duration * 0.50, ref_duration,
                    _track_value_at(local_x, ref_duration * 0.50, float(center_x)),
                    _track_value_at(local_y, ref_duration * 0.50, float(center_y)),
                    output_path, event,
                )
                return bounded
            return emergency
        try:
            rendered = probe_media(output_path)
            rendered_duration = float(rendered.get("duration") or 0.0)
            if rendered_duration < min(0.75, ref_duration * 0.70):
                output_path.unlink(missing_ok=True)
                retried = _render_split_panel_live_fallback(
                    source_path, start_time, ref_duration,
                    _track_value_at(local_x, ref_duration * 0.50, float(center_x)),
                    _track_value_at(local_y, ref_duration * 0.50, float(center_y)),
                    local_face_h, output_path, event,
                )
                if retried is not None:
                    return retried
                return _render_split_panel_bounded_fallback(
                    source_path, start_time + ref_duration * 0.50, ref_duration,
                    _track_value_at(local_x, ref_duration * 0.50, float(center_x)),
                    _track_value_at(local_y, ref_duration * 0.50, float(center_y)),
                    output_path, event,
                )
            encoded_motion = _motion_value(_gray_motion_series(output_path, 0.0, min(ref_duration, rendered_duration), 3.0))
            if encoded_motion < 0.45:
                logger.warning('Split panel rejected as visually static after encode (motion %.3f): %s', encoded_motion, output_path)
                output_path.unlink(missing_ok=True)
                return None
        except Exception:
            pass
        return output_path
    except JobCancelled:
        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    except Exception as exc:
        logger.warning(
            "Tracked cross-camera panel preparation failed before/after encode; "
            "trying FFmpeg-only live fallback: %s", exc
        )
        try:
            fallback_start = max(0.0, float(locals().get("start_time", max(0.0, float(source_time) - 1.6))))
            fallback_duration = float(locals().get("ref_duration", 4.8))
            if fallback_duration < 0.80:
                fallback_duration = 4.0
            emergency = _render_split_panel_live_fallback(
                source_path, fallback_start, fallback_duration,
                float(center_x), float(center_y), float(face_h or 0.0),
                output_path, cancel_event,
            )
            if emergency is not None:
                return emergency
            return _render_split_panel_bounded_fallback(
                source_path, fallback_start + fallback_duration * 0.50,
                fallback_duration, float(center_x), float(center_y),
                output_path, cancel_event,
            )
        except JobCancelled:
            raise
        except Exception as fallback_exc:
            logger.warning("Final FFmpeg-only split panel fallback failed: %s", fallback_exc)
            return None

def _build_smart_layout_filter(
    plan: SmartReframePlan,
    width: int,
    height: int,
    fps: float,
    duration: float,
    command_file: Optional[Path] = None,
    safe_framing: bool = False,
    split_enabled: bool = True,
    split_reference_inputs: Optional[Tuple[int, int]] = None,
) -> Tuple[str, List[Tuple[float, float]]]:
    """Build stable portrait framing with optional real two-speaker 50/50 split."""
    width = max(2, width)
    height = max(2, height)
    # Native-cadence render: 25fps stays 25, 29.97 stays 29.97, 50/59.94
    # stays high-frame-rate (capped at 60). This removes duplicate-frame judder.
    render_fps = float(fps or LOCAL_EDITOR_OUTPUT_FPS)
    if not math.isfinite(render_fps) or render_fps < 1.0 or render_fps > 240.0:
        render_fps = float(LOCAL_EDITOR_OUTPUT_FPS)
    if width > height and height >= 2160 and width >= 3000:
        output_h = height - (height % 2)
        output_w = max(2, int(round(output_h * 9.0 / 16.0)))
        output_w -= output_w % 2
    else:
        output_w, output_h = 1080, 1920
    panel_h = output_h // 2
    panel_h -= panel_h % 2

    def single_geometry() -> Tuple[int, int]:
        source_ratio = width / max(1, height)
        if source_ratio <= 0.72:
            # A source that is already approximately 9:16 must NOT be aggressively
            # re-cropped to 70-88% width. That old zoom made edge-profile speakers
            # disappear behind the microphone. Keep the genuine full portrait and
            # use the virtual padded canvas to re-centre it instead.
            if 0.52 <= source_ratio <= 0.62:
                crop_h = height - (height % 2)
                crop_w = max(2, int(round(crop_h * (9.0 / 16.0)))); crop_w -= crop_w % 2
                if crop_w > width:
                    crop_w = width - (width % 2)
                    crop_h = max(2, int(round(crop_w / (9.0 / 16.0)))); crop_h -= crop_h % 2
            else:
                centers = [max(0.05, min(0.95, float(c))) for _, c in (plan.primary_track or [(0.0, 0.5)])]
                edge_distances = sorted(min(c, 1.0 - c) for c in centers) if centers else [0.5]
                percentile_index = min(len(edge_distances) - 1, max(0, int(len(edge_distances) * 0.10)))
                robust_edge = edge_distances[percentile_index]
                crop_fraction = max(0.70, min(0.88, robust_edge * 2.0 * 0.985))
                crop_w = max(2, int(round(width * crop_fraction))); crop_w -= crop_w % 2
                crop_h = max(2, int(round(crop_w / (9.0 / 16.0)))); crop_h -= crop_h % 2
                if crop_h > height:
                    crop_h = height - (height % 2)
                    crop_w = max(2, int(round(crop_h * (9.0 / 16.0)))); crop_w -= crop_w % 2
        else:
            avg = sum(c for _, c in plan.primary_track) / len(plan.primary_track) if plan.primary_track else 0.5
            crop_w, crop_h, _, _ = _clipper_crop_geometry(width, height, avg)
            # Full-height landscape crops have y=0 forever. Reserve vertical
            # travel headroom so per-frame head/body Y keyframes actually render.
            max_vertical_h = int(round(height * (1.0 - SMART_REFRAME_VERTICAL_TRAVEL_FRACTION)))
            max_vertical_h = max(2, max_vertical_h - (max_vertical_h % 2))
            if crop_h > max_vertical_h and max_vertical_h >= 2:
                crop_h = max_vertical_h
                crop_w = max(2, int(round(crop_h * (9.0 / 16.0))))
                crop_w -= crop_w % 2
                if crop_w > width:
                    crop_w = width - (width % 2)
                    crop_h = max(2, int(round(crop_w / (9.0 / 16.0))))
                    crop_h -= crop_h % 2
        return crop_w, crop_h

    fallback_face_h = float(plan.primary_face_h or 0.0)
    face_heights = [max(0.0, min(1.0, float(v))) for v in (
        plan.split_primary_face_h or fallback_face_h,
        plan.split_secondary_face_h or fallback_face_h,
    ) if float(v or 0.0) > 0.0]
    # Keep both speakers at comparable upper-body scale. A ~34% detector-face
    # height in each 960px half closely matches the supplied podcast reference
    # and avoids forehead/chin clipping caused by the old 42% tight zoom.
    target_face_fraction = 0.34

    def split_geometry(face_h_value: float) -> Tuple[int, int]:
        target_ratio = 1.125
        face_h_norm = max(0.0, min(1.0, float(face_h_value or 0.0)))
        desired_h_norm = face_h_norm / target_face_fraction if face_h_norm > 0.0 else 0.62
        desired_h_norm = max(0.38, min(0.96, desired_h_norm))
        ch = max(2, int(round(height * desired_h_norm))); ch -= ch % 2
        cw = max(2, int(round(ch * target_ratio))); cw -= cw % 2
        if cw > width:
            cw = width - (width % 2); ch = max(2, int(round(cw / target_ratio))); ch -= ch % 2
        if ch > height:
            ch = height - (height % 2); cw = max(2, int(round(ch * target_ratio))); cw -= cw % 2
            if cw > width:
                cw = width - (width % 2); ch = max(2, int(round(cw / target_ratio))); ch -= ch % 2
        return cw, ch

    crop_w, crop_h = single_geometry()
    top_w, top_h = split_geometry(float(plan.split_primary_face_h or fallback_face_h))
    bot_w, bot_h = split_geometry(float(plan.split_secondary_face_h or fallback_face_h))

    # Edge-safe virtual camera canvas. Size it from the ACTUAL tracked head
    # centres and crop geometry instead of a fixed 34% pad. A clipped profile can
    # have its reconstructed head centre slightly outside 0..1; fixed padding
    # then clamps crop x=0 and leaves the face visibly off-centre. Dynamic padding
    # guarantees enough virtual room to put that reconstructed centre at 50%.
    x_values = [float(c) for _, c in (plan.primary_track or [])]
    x_values += [float(c) for _, c in (plan.split_primary_track or [])]
    x_values += [float(c) for _, c in (plan.split_secondary_track or [])]
    min_center = min(x_values) if x_values else 0.5
    max_center = max(x_values) if x_values else 0.5
    largest_half_crop = max(crop_w, top_w, bot_w) * 0.5
    required_left = max(0.0, largest_half_crop - min_center * width)
    required_right = max(0.0, largest_half_crop - (1.0 - max_center) * width)
    baseline_pad = width * 0.14
    safety_pad = width * 0.035
    pad_x = int(round(max(baseline_pad, required_left, required_right) + safety_pad))
    pad_x = min(int(round(width * SMART_REFRAME_VIRTUAL_CANVAS_MAX_PAD)), max(2, pad_x))
    pad_x -= pad_x % 2
    canvas_w = width + pad_x * 2
    canvas_w -= canvas_w % 2
    def map_x(c: float) -> float:
        return max(0.0, min(1.0, (pad_x + float(c) * width) / max(1.0, float(canvas_w))))

    def expand(cw: int, ch: int, factor: float) -> Tuple[int, int]:
        if factor <= 1.0: return cw, ch
        ratio = cw / max(1.0, float(ch))
        nh = min(height, max(ch, int(round(ch * factor)))); nh -= nh % 2
        nw = max(2, int(round(nh * ratio))); nw -= nw % 2
        if nw > width:
            nw = width - (width % 2); nh = max(2, int(round(nw / max(1e-6, ratio)))); nh -= nh % 2
        if nh > height:
            nh = height - (height % 2); nw = max(2, int(round(nh * ratio))); nw -= nw % 2
        return max(2, nw), max(2, nh)
    if safe_framing:
        crop_w, crop_h = expand(crop_w, crop_h, 1.18)
        top_w, top_h = expand(top_w, top_h, 1.16)
        bot_w, bot_h = expand(bot_w, bot_h, 1.16)

    # Recompute virtual padding AFTER final crop sizes are known. This guarantees
    # safe-framing cannot enlarge a crop beyond the side/top room that was planned.
    x_values = [float(c) for _, c in (plan.primary_track or [])]
    x_values += [float(c) for _, c in (plan.split_primary_track or [])]
    x_values += [float(c) for _, c in (plan.split_secondary_track or [])]
    min_center = min(x_values) if x_values else 0.5
    max_center = max(x_values) if x_values else 0.5
    largest_half_crop = max(crop_w, top_w, bot_w) * 0.5
    required_left = max(0.0, largest_half_crop - min_center * width)
    required_right = max(0.0, largest_half_crop - (1.0 - max_center) * width)
    pad_x = int(round(max(width * 0.14, required_left, required_right) + width * 0.035))
    pad_x = min(int(round(width * SMART_REFRAME_VIRTUAL_CANVAS_MAX_PAD)), max(2, pad_x))
    pad_x -= pad_x % 2
    canvas_w = width + pad_x * 2
    canvas_w -= canvas_w % 2
    def map_x(c: float) -> float:
        return max(0.0, min(1.0, (pad_x + float(c) * width) / max(1.0, float(canvas_w))))

    # V8.14: never create a blurred virtual TOP/BOTTOM canvas. Vertical camera
    # movement is a real-source crop only: Y keyframes clamp inside the genuine
    # source frame. This removes the visible blur band that appeared whenever a
    # speaker leaned up/down. Horizontal virtual padding is retained only when an
    # edge-clipped face physically cannot be centred from source pixels alone.
    pad_y = 0
    canvas_h = height
    def map_y(c: float) -> float:
        return max(0.0, min(1.0, float(c)))

    sx0 = _crop_axis_position(map_x(_track_value_at(plan.primary_track, 0.0, 0.5)), canvas_w, crop_w, 0.50)
    sy0 = _crop_axis_position(map_y(_track_value_at(plan.primary_y_track, 0.0, 0.40)), canvas_h, crop_h, 0.36)
    lx0 = _crop_axis_position(map_x(_track_value_at(plan.split_primary_track or plan.primary_track, 0.0, 0.30)), canvas_w, top_w, 0.50)
    ly0 = _crop_axis_position(map_y(_track_value_at(plan.split_primary_y_track or plan.primary_y_track, 0.0, 0.42)), canvas_h, top_h, 0.36)
    rx0 = _crop_axis_position(map_x(_track_value_at(plan.split_secondary_track or plan.primary_track, 0.0, 0.70)), canvas_w, bot_w, 0.50)
    ry0 = _crop_axis_position(map_y(_track_value_at(plan.split_secondary_y_track or plan.primary_y_track, 0.0, 0.42)), canvas_h, bot_h, 0.36)
    # V8.18: current-time A/B overlay keeps a STATIC crop inside each split
    # occurrence, but that crop is locked from the real primary face/body anchor
    # of THAT occurrence rather than one global identity median.  This keeps the
    # current speaker centred and lip-synced without split-camera pan/jitter.
    a_x_vals = [float(v) for _, v in (plan.split_primary_track or []) if math.isfinite(float(v))]
    b_x_vals = [float(v) for _, v in (plan.split_secondary_track or []) if math.isfinite(float(v))]
    a_y_vals = [float(v) for _, v in (plan.split_primary_y_track or []) if math.isfinite(float(v))]
    b_y_vals = [float(v) for _, v in (plan.split_secondary_y_track or []) if math.isfinite(float(v))]
    default_ax = float(statistics.median(a_x_vals)) if a_x_vals else 0.30
    default_bx = float(statistics.median(b_x_vals)) if b_x_vals else 0.70
    default_ay = float(statistics.median(a_y_vals)) if a_y_vals else OPENCLIP_SPLIT_V_ALIGN
    default_by = float(statistics.median(b_y_vals)) if b_y_vals else OPENCLIP_SPLIT_V_ALIGN

    cross_rows = list(plan.cross_scene_split_intervals[:40])
    timeline_rows = list(plan.cross_scene_speaker_intervals or [])

    def _locked_identity_anchor(identity: str, a: float, b: float, fx: float, fy: float) -> Tuple[float, float]:
        sample_times: List[float] = []
        for sa, sb, label in timeline_rows:
            if str(label) != identity:
                continue
            lo=max(float(a),float(sa)); hi=min(float(b),float(sb))
            if hi-lo < 0.04:
                continue
            sample_times.extend([lo+(hi-lo)*0.20, lo+(hi-lo)*0.50, lo+(hi-lo)*0.80])
        if sample_times:
            xs=[float(_track_value_at(plan.primary_track,t,fx)) for t in sample_times]
            ys=[float(_track_value_at(plan.primary_y_track,t,fy)) for t in sample_times]
            xs=[v for v in xs if math.isfinite(v)]; ys=[v for v in ys if math.isfinite(v)]
            if xs: fx=float(statistics.median(xs))
            if ys: fy=float(statistics.median(ys))
        return fx,fy

    def _piecewise_locked_expr(identity: str, axis: str, fallback_x: float, fallback_y: float, crop_w_local: int, crop_h_local: int) -> str:
        default_norm = fallback_x if axis == "x" else fallback_y
        if axis == "x":
            default_px=_crop_axis_position(map_x(default_norm),canvas_w,crop_w_local,0.50)
        else:
            default_px=_crop_axis_position(map_y(default_norm),canvas_h,crop_h_local,0.36)
        expr=str(int(default_px))
        for a,b in reversed(cross_rows):
            ax,ay=_locked_identity_anchor(identity,float(a),float(b),fallback_x,fallback_y)
            if axis == "x":
                value=_crop_axis_position(map_x(ax),canvas_w,crop_w_local,0.50)
            else:
                value=_crop_axis_position(map_y(ay),canvas_h,crop_h_local,0.36)
            expr=f"if(between(t\\,{float(a):.3f}\\,{float(b):.3f})\\,{int(value)}\\,{expr})"
        return expr

    cax_expr=_piecewise_locked_expr("left","x",default_ax,default_ay,top_w,top_h)
    cay_expr=_piecewise_locked_expr("left","y",default_ax,default_ay,top_w,top_h)
    cbx_expr=_piecewise_locked_expr("right","x",default_bx,default_by,bot_w,bot_h)
    cby_expr=_piecewise_locked_expr("right","y",default_bx,default_by,bot_w,bot_h)

    sendcmd_stage = ""
    if command_file is not None:
        _write_reframe_sendcmd(
            command_file, plan, width, height, duration,
            (crop_w, crop_h), (top_w, top_h), (bot_w, bot_h),
            canvas_width=canvas_w, canvas_height=canvas_h,
            pad_x=pad_x, pad_y=pad_y, output_fps=render_fps,
        )
        cmd_path = str(command_file).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        sendcmd_stage = f",sendcmd=f='{cmd_path}'"

    live_dual_ok = bool(
        split_enabled and plan.dual_speaker_intervals
        and plan.split_primary_track and plan.split_secondary_track
    )
    cross_dual_ok = bool(
        split_enabled and split_reference_inputs
        and plan.cross_scene_split_intervals
    )

    # One global occurrence counter controls TOP/BOTTOM order across both live
    # two-face splits and cross-camera splits. Separate mechanisms must not each
    # restart at AB, otherwise Split #2 can incorrectly repeat Split #1 order.
    split_orientation: Dict[Tuple[str, int], int] = {}
    _split_events: List[Tuple[float, str, int]] = []
    if live_dual_ok:
        _split_events.extend((float(a), "live", i) for i, (a, _) in enumerate(plan.dual_speaker_intervals[:120]))
    if cross_dual_ok:
        _split_events.extend((float(a), "cross", i) for i, (a, _) in enumerate(plan.cross_scene_split_intervals[:40]))
    for occurrence_index, (_, kind, local_index) in enumerate(sorted(_split_events, key=lambda item: (item[0], item[1], item[2]))):
        split_orientation[(kind, local_index)] = occurrence_index % 2

    # Build the single camera first; split layouts are overlays only for verified
    # windows. Cross-camera A/B uses the CURRENT source shot for whichever speaker
    # is actually on camera, so lips remain synced to input-0 audio. The non-visible
    # listener alone uses the prepared reference panel underneath.
    parts: List[str] = [
        "[0:v]setpts=PTS-STARTPTS[fg0]",
        "[fg0]split=2[bg0][fg1]",
        f"[bg0]scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase,crop={canvas_w}:{canvas_h},gblur=sigma=28[bg1]",
        f"[bg1][fg1]overlay={pad_x}:{pad_y}{sendcmd_stage}[basev]",
    ]
    identity_needed = bool(live_dual_ok)
    current_needed = bool(cross_dual_ok)
    if identity_needed and current_needed:
        parts.append("[basev]split=5[single_src][id_a_src][id_b_src][cur_a_src][cur_b_src]")
    elif identity_needed:
        parts.append("[basev]split=3[single_src][id_a_src][id_b_src]")
    elif current_needed:
        parts.append("[basev]split=3[single_src][cur_a_src][cur_b_src]")
    else:
        parts.append("[basev]null[single_src]")

    parts.append(
        f"[single_src]crop@single={crop_w}:{crop_h}:x={sx0}:y={sy0},scale={output_w}:{output_h}:flags=lanczos[single_layout]"
    )

    if identity_needed:
        parts.extend([
            f"[id_a_src]crop@dual_top={top_w}:{top_h}:x={lx0}:y={ly0},scale={output_w}:{panel_h}:flags=lanczos[id_a_panel]",
            f"[id_b_src]crop@dual_bottom={bot_w}:{bot_h}:x={rx0}:y={ry0},scale={output_w}:{panel_h}:flags=lanczos[id_b_panel]",
        ])
        def split_identity(label: str, prefix: str) -> List[str]:
            names = [f"{prefix}_live_ab", f"{prefix}_live_ba"]
            parts.append(f"[{label}]split=2[{names[0]}][{names[1]}]")
            return names
        split_identity("id_a_panel", "id_a")
        split_identity("id_b_panel", "id_b")

    if current_needed:
        # Both current panels follow plan.primary_* (the actual visible speaker).
        # Geometry differs only to match A/B apparent size in their respective
        # reference halves. Camera coordinates remain zero-phase smoothed.
        parts.extend([
            f"[cur_a_src]crop={top_w}:{top_h}:x='{cax_expr}':y='{cay_expr}',scale={output_w}:{panel_h}:flags=lanczos[cur_a_panel]",
            f"[cur_b_src]crop={bot_w}:{bot_h}:x='{cbx_expr}':y='{cby_expr}',scale={output_w}:{panel_h}:flags=lanczos[cur_b_panel]",
            "[cur_a_panel]split=2[cur_a_top][cur_a_bottom]",
            "[cur_b_panel]split=2[cur_b_top][cur_b_bottom]",
        ])

    stage_label = "single_layout"
    if live_dual_ok:
        live_intervals = list(plan.dual_speaker_intervals[:120])
        live_expr_ab = "+".join(
            f"between(t,{a:.3f},{b:.3f})" for i, (a, b) in enumerate(live_intervals)
            if split_orientation.get(("live", i), 0) == 0
        ) or "0"
        live_expr_ba = "+".join(
            f"between(t,{a:.3f},{b:.3f})" for i, (a, b) in enumerate(live_intervals)
            if split_orientation.get(("live", i), 0) == 1
        ) or "0"
        parts.extend([
            "[id_a_live_ab][id_b_live_ab]vstack=inputs=2[live_dual_ab]",
            "[id_b_live_ba][id_a_live_ba]vstack=inputs=2[live_dual_ba]",
            f"[{stage_label}][live_dual_ab]overlay=0:0:enable='min(1,{live_expr_ab})'[live_stage_ab]",
            f"[live_stage_ab][live_dual_ba]overlay=0:0:enable='min(1,{live_expr_ba})'[live_stage]",
        ])
        stage_label = "live_stage"

    if cross_dual_ok and split_reference_inputs is not None:
        ref_a_idx, ref_b_idx = split_reference_inputs
        cross_intervals = list(plan.cross_scene_split_intervals[:40])
        cross_expr_ab = "+".join(
            f"between(t,{a:.3f},{b:.3f})" for i, (a, b) in enumerate(cross_intervals)
            if split_orientation.get(("cross", i), 0) == 0
        ) or "0"
        cross_expr_ba = "+".join(
            f"between(t,{a:.3f},{b:.3f})" for i, (a, b) in enumerate(cross_intervals)
            if split_orientation.get(("cross", i), 0) == 1
        ) or "0"

        # Start the silent moving references exactly when the FIRST cross split
        # begins.  Without this offset, a split at t=4.8s entered a 5s reference
        # at frame 4.8 and looped almost immediately, which looked like a hitch.
        first_cross_start = min((float(a) for a, _ in cross_intervals), default=0.0)
        parts.extend([
            f"[{ref_a_idx}:v]fps={render_fps:.6f},setpts=PTS-STARTPTS+{first_cross_start:.6f}/TB,scale={output_w}:{panel_h}:flags=lanczos,split=2[ref_a_ab][ref_a_ba]",
            f"[{ref_b_idx}:v]fps={render_fps:.6f},setpts=PTS-STARTPTS+{first_cross_start:.6f}/TB,scale={output_w}:{panel_h}:flags=lanczos,split=2[ref_b_ab][ref_b_ba]",
            "[ref_a_ab][ref_b_ab]vstack=inputs=2[reference_dual_ab]",
            "[ref_b_ba][ref_a_ba]vstack=inputs=2[reference_dual_ba]",
            f"[{stage_label}][reference_dual_ab]overlay=0:0:enable='min(1,{cross_expr_ab})':eof_action=pass:repeatlast=1[cross_ref_ab]",
            f"[cross_ref_ab][reference_dual_ba]overlay=0:0:enable='min(1,{cross_expr_ba})':eof_action=pass:repeatlast=1[cross_ref_base]",
        ])

        # Overlay the CURRENT source shot into the half belonging to the identity
        # actually on camera. This is the critical lip-sync fix for A/B podcasts:
        # the speaking mouth is never a 5-second reference from another timestamp.
        timeline = list(plan.cross_scene_speaker_intervals or [])
        def _intersection_expr(identity: str, orientation: int, destination: str) -> str:
            rows: List[str] = []
            for idx, (a, b) in enumerate(cross_intervals):
                if split_orientation.get(("cross", idx), 0) != orientation:
                    continue
                for sa, sb, label in timeline:
                    if str(label) != identity:
                        continue
                    lo = max(float(a), float(sa)); hi = min(float(b), float(sb))
                    if hi - lo >= 0.040:
                        rows.append(f"between(t,{lo:.3f},{hi:.3f})")
            return "+".join(rows) or "0"

        left_top = _intersection_expr("left", 0, "top")
        left_bottom = _intersection_expr("left", 1, "bottom")
        right_top = _intersection_expr("right", 1, "top")
        right_bottom = _intersection_expr("right", 0, "bottom")
        parts.extend([
            f"[cross_ref_base][cur_a_top]overlay=0:0:enable='min(1,{left_top})':eof_action=pass:repeatlast=1[cross_live_a_top]",
            f"[cross_live_a_top][cur_a_bottom]overlay=0:{panel_h}:enable='min(1,{left_bottom})':eof_action=pass:repeatlast=1[cross_live_a_bottom]",
            f"[cross_live_a_bottom][cur_b_top]overlay=0:0:enable='min(1,{right_top})':eof_action=pass:repeatlast=1[cross_live_b_top]",
            f"[cross_live_b_top][cur_b_bottom]overlay=0:{panel_h}:enable='min(1,{right_bottom})':eof_action=pass:repeatlast=1[cross_stage]",
        ])
        stage_label = "cross_stage"

    if stage_label != "layout":
        parts.append(f"[{stage_label}]null[layout]")
    return ";".join(parts) + ";", plan.primary_track




def _measure_split_panel_face_center(panel_path: Path) -> Optional[float]:
    """Measure the actual rendered face centre in a 1080x960 split panel."""
    try:
        import cv2  # type: ignore
    except Exception:
        return None
    cap = cv2.VideoCapture(str(panel_path))
    if not cap.isOpened():
        return None
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 25.0
        count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        dur = count / fps if count > 0 else 4.0
        centers: List[float] = []
        detector = detector_kind = None
        try:
            detector, detector_kind = _create_face_detector(cv2)
        except Exception:
            detector = detector_kind = None
        frontal = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"))
        profile = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / "haarcascade_profileface.xml"))
        for frac in (0.25, 0.50, 0.75):
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, dur * frac) * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            h, w = frame.shape[:2]
            candidates: List[Tuple[float, float, float, float, float, float, float]] = []
            if detector is not None:
                try:
                    candidates = _detect_face_boxes_for_reframe(frame, detector, detector_kind, cv2)
                except Exception:
                    candidates = []
            if not candidates:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                raw = list(frontal.detectMultiScale(gray, 1.06, 4, minSize=(70, 70))) if not frontal.empty() else []
                if not raw and not profile.empty():
                    raw = list(profile.detectMultiScale(gray, 1.06, 4, minSize=(70, 70)))
                    if not raw:
                        flip = cv2.flip(gray, 1)
                        raw = [(w-(x+ww), y, ww, hh) for x,y,ww,hh in profile.detectMultiScale(flip,1.06,4,minSize=(70,70))]
                for x,y,ww,hh in raw:
                    cx=(x+ww*0.5)/w; cy=(y+hh*0.5)/h
                    area=(ww*hh)/(w*h)
                    aspect=ww/max(1.0,float(hh))
                    # Reject mic/clothing false positives: a split-panel face must
                    # be a reasonably large head in the upper ~60% of the panel.
                    if area >= 0.012 and cy <= 0.58 and 0.55 <= aspect <= 1.55:
                        candidates.append((cx,cy,area,x/w,y/h,ww/w,hh/h))
            if candidates:
                best=max(candidates,key=lambda b: float(b[2]))
                centers.append(float(best[0]))
        if not centers:
            return None
        return float(statistics.median(centers))
    finally:
        cap.release()


def _recenter_rendered_split_panel(
    panel_path: Path,
    cancel_event: Optional[threading.Event] = None,
) -> Optional[Path]:
    """Post-render face-centre guard; keeps the panel live while shifting it."""
    event = cancel_event or threading.Event()
    if not panel_path.exists() or panel_path.stat().st_size <= 4096:
        return None
    for _ in range(2):
        cx = _measure_split_panel_face_center(panel_path)
        if cx is None:
            # No detector proof: retain the moving-human corrected panel rather
            # than inventing a different speaker; final MP4 QA remains authoritative.
            return panel_path
        if 0.45 <= cx <= 0.55:
            return panel_path
        shift = int(round((0.50 - cx) * 1080.0))
        shift = max(-220, min(220, shift))
        if abs(shift) < 8:
            return panel_path
        tmp = panel_path.with_suffix('.recenter.mp4')
        tmp.unlink(missing_ok=True)
        if shift >= 0:
            ox = shift
        else:
            ox = shift
        filt=(
            "[0:v]setpts=PTS-STARTPTS,split=2[cbg][cfg];"
            "[cbg]gblur=sigma=18[cbg2];"
            f"[cbg2][cfg]overlay={ox}:0:eof_action=pass:shortest=1,"
            "setsar=1,format=yuv420p[v]"
        )
        try:
            run_process([
                "ffmpeg","-y","-hide_banner","-loglevel","warning",
                "-i",str(panel_path),"-filter_complex",filt,"-map","[v]","-an",
                "-c:v","libx264","-preset","medium","-crf","10",
                "-pix_fmt","yuv420p","-movflags","+faststart","-vsync","0",str(tmp),
            ],cancel_event=event,error_prefix="Split panel face recenter failed",timeout_seconds=180)
            if tmp.exists() and tmp.stat().st_size > 4096:
                tmp.replace(panel_path)
            else:
                tmp.unlink(missing_ok=True); return panel_path
        except JobCancelled:
            raise
        except Exception:
            tmp.unlink(missing_ok=True); return panel_path
    return panel_path

def _verify_expected_split_output(
    path: Path,
    split_events: List[Tuple[float, float]],
    require_static_panels: bool = False,
    verified_static_reaction_faces: bool = False,
) -> Tuple[bool, List[str]]:
    """Decode the FINAL MP4 and prove planned split events really rendered.

    This is intentionally output-based QA: planner intervals or FFmpeg filter text
    do not count as success. Each expected event must visibly contain two speaker
    panels for >=4 seconds. Cross-camera split may use two silent moving reference layers plus the current-time
    live speaker overlay. QA verifies motion, centring, identity and order in the FINAL MP4. When face
    detection is available, both halves must contain distinct, centred human faces.
    A second occurrence must reverse A/B order relative to the first.
    """
    events = [
        (max(0.0, float(a)), max(0.0, float(b)))
        for a, b in (split_events or [])
        if float(b) - float(a) >= SMART_REFRAME_DUAL_MIN_SECONDS - 0.08
    ]
    if not events:
        return True, ["split QA: no planned split events"]
    try:
        import cv2  # type: ignore
    except Exception as exc:
        return False, [f"split QA failed: OpenCV unavailable ({str(exc)[:100]})"]

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return False, ["split QA failed: final MP4 could not be decoded"]

    detector = detector_kind = None
    try:
        detector, detector_kind = _create_face_detector(cv2)
    except Exception:
        detector = detector_kind = None

    notes: List[str] = []
    identity_rows: List[Tuple[Optional[Tuple[float, ...]], Optional[Tuple[float, ...]]]] = []
    try:
        for event_index, (start_t, end_t) in enumerate(events[:8]):
            event_duration = end_t - start_t
            if event_duration < 3.92:
                return False, [f"split QA failed: occurrence {event_index + 1} shorter than 4s"]

            # Cover beginning/middle/end so a one-frame overlay cannot fake QA.
            sample_times: List[float] = []
            cursor = start_t + min(0.55, event_duration * 0.14)
            stop = end_t - min(0.45, event_duration * 0.10)
            while cursor <= stop + 1e-6 and len(sample_times) < 8:
                sample_times.append(cursor)
                cursor += max(0.55, min(0.90, event_duration / 6.0))
            if len(sample_times) < 3:
                sample_times = [
                    start_t + event_duration * 0.20,
                    start_t + event_duration * 0.50,
                    start_t + event_duration * 0.80,
                ]

            decoded = 0
            both_face = 0
            top_face_count = 0
            bottom_face_count = 0
            distinct_face = 0
            top_center_ok = 0
            bottom_center_ok = 0
            seam_votes = 0
            top_motion: List[float] = []
            bottom_motion: List[float] = []
            previous_top = previous_bottom = None
            top_sigs: List[Tuple[float, ...]] = []
            bottom_sigs: List[Tuple[float, ...]] = []

            for t in sample_times:
                cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
                ok, frame = cap.read()
                if not ok or frame is None or getattr(frame, "size", 0) <= 0:
                    continue
                decoded += 1
                h, w = frame.shape[:2]
                if h < 8 or w < 8:
                    continue
                half = h // 2
                top = frame[:half, :]
                bottom = frame[half:half * 2, :]

                # Frozen/dead-panel check.  Use a central crop so captions/hook
                # animation cannot make a frozen speaker panel look live.
                def motion_view(panel: Any) -> Any:
                    ph, pw = panel.shape[:2]
                    # Upper/centre human region only.  Exclude the lower caption
                    # band so changing words cannot fake motion on a frozen face.
                    x1 = int(pw * 0.12); x2 = max(x1 + 8, int(pw * 0.88))
                    y1 = int(ph * 0.10); y2 = max(y1 + 8, int(ph * 0.60))
                    roi = panel[y1:y2, x1:x2]
                    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                    return cv2.resize(gray, (144, 84), interpolation=cv2.INTER_AREA)

                tv = motion_view(top); bv = motion_view(bottom)
                if previous_top is not None:
                    top_motion.append(float(cv2.absdiff(tv, previous_top).mean()))
                if previous_bottom is not None:
                    bottom_motion.append(float(cv2.absdiff(bv, previous_bottom).mean()))
                previous_top, previous_bottom = tv, bv

                # Horizontal split seam vote.  Compare the row jump at 50% with
                # ordinary nearby row-to-row changes.  Face detection below is the
                # primary proof; this is a robust fallback for hard profiles.
                small = cv2.resize(frame, (216, 384), interpolation=cv2.INTER_AREA)
                sy = small.shape[0] // 2
                if 4 <= sy < small.shape[0] - 4:
                    seam = float(cv2.absdiff(small[sy - 2:sy], small[sy:sy + 2]).mean())
                    local_values = []
                    for yy in (sy - 24, sy - 16, sy - 8, sy + 8, sy + 16, sy + 24):
                        if 1 <= yy < small.shape[0]:
                            local_values.append(float(cv2.absdiff(small[yy - 1:yy], small[yy:yy + 1]).mean()))
                    baseline = float(statistics.median(local_values)) if local_values else 0.0
                    if seam >= max(2.0, baseline * 1.22):
                        seam_votes += 1

                if detector is not None:
                    top_boxes = _detect_face_boxes_for_reframe(top, detector, detector_kind, cv2)
                    bottom_boxes = _detect_face_boxes_for_reframe(bottom, detector, detector_kind, cv2)
                    tf = max(top_boxes, key=lambda b: float(b[2])) if top_boxes else None
                    bf = max(bottom_boxes, key=lambda b: float(b[2])) if bottom_boxes else None
                    if tf is not None:
                        top_face_count += 1
                        if 0.40 <= float(tf[0]) <= 0.60:
                            top_center_ok += 1
                    if bf is not None:
                        bottom_face_count += 1
                        if 0.40 <= float(bf[0]) <= 0.60:
                            bottom_center_ok += 1
                    if tf is not None and bf is not None:
                        both_face += 1
                        ts = _speaker_visual_signature(top, tf, cv2)
                        bs = _speaker_visual_signature(bottom, bf, cv2)
                        if ts and bs:
                            top_sigs.append(ts); bottom_sigs.append(bs)
                            if _speaker_signature_distance(ts, bs) >= 0.020:
                                distinct_face += 1

            if decoded < 3:
                return False, [f"split QA failed: occurrence {event_index + 1} did not decode across its duration"]

            top_motion_max = max(top_motion or [0.0])
            bottom_motion_max = max(bottom_motion or [0.0])
            top_motion_med = float(statistics.median(top_motion)) if top_motion else 0.0
            bottom_motion_med = float(statistics.median(bottom_motion)) if bottom_motion else 0.0
            # V8.20: compression noise or animated captions must not count as a
            # moving speaker.  Require sustained real change in the face/upper
            # body region across the split occurrence.
            top_live = bool(top_motion and top_motion_med >= 0.60 and sum(v >= 0.80 for v in top_motion) >= 2)
            bottom_live = bool(bottom_motion and bottom_motion_med >= 0.60 and sum(v >= 0.80 for v in bottom_motion) >= 2)
            if require_static_panels:
                # Cross-camera A/B source has only one real current-time camera shot.
                # V8.17 therefore requires ONE genuinely live current-speaker panel;
                # the other half is intentionally a static centred reaction portrait.
                # Requiring both halves to move would force fake lips from another
                # timestamp and recreate the exact sync/lag problem we are avoiding.
                if not (top_live or bottom_live):
                    return False, [
                        f"split QA failed: occurrence {event_index + 1} has no live current-speaker panel "
                        f"(top max diff={top_motion_max:.3f}, bottom={bottom_motion_max:.3f})"
                    ]
            else:
                # Same-frame two-speaker split really contains both people at the
                # current timestamp, so both halves must remain live.
                if not top_live or not bottom_live:
                    return False, [
                        f"split QA failed: occurrence {event_index + 1} has frozen/dead live panel "
                        f"(top max diff={top_motion_max:.3f}, bottom={bottom_motion_max:.3f})"
                    ]

            if detector is not None:
                needed = max(2, int(math.ceil(decoded * 0.45)))
                if verified_static_reaction_faces:
                    # V8.18 moving-reference panels are independently face-verified
                    # BEFORE composition.  Final MP4 QA still requires BOTH halves
                    # to be live (checked above), a repeated 50/50 seam, and every
                    # face that can be rediscovered after scaling/caption overlay to
                    # remain centred.  Hard-profile detector misses are not allowed
                    # to turn a visually valid moving panel into a false failure.
                    if seam_votes < max(2, int(math.ceil(decoded * 0.50))):
                        return False, [
                            f"split QA failed: occurrence {event_index + 1} has no repeated 50/50 split seam"
                        ]
                    if (top_face_count + bottom_face_count) < needed:
                        return False, [
                            f"split QA failed: occurrence {event_index + 1} did not retain a visible human anchor "
                            f"(top={top_face_count}, bottom={bottom_face_count}, samples={decoded})"
                        ]
                    if top_face_count and top_center_ok < max(1, int(math.ceil(top_face_count * 0.40))):
                        return False, [f"split QA failed: occurrence {event_index + 1} TOP detected face is off-centre"]
                    if bottom_face_count and bottom_center_ok < max(1, int(math.ceil(bottom_face_count * 0.40))):
                        return False, [f"split QA failed: occurrence {event_index + 1} BOTTOM detected face is off-centre"]
                    if both_face >= max(1, int(math.ceil(decoded * 0.30))) and distinct_face < max(1, int(math.ceil(both_face * 0.40))):
                        return False, [
                            f"split QA failed: occurrence {event_index + 1} appears to duplicate the same speaker"
                        ]
                elif both_face < needed:
                    return False, [
                        f"split QA failed: occurrence {event_index + 1} did not visibly contain two real speaker faces "
                        f"({both_face}/{decoded} both-face samples; seam={seam_votes}/{decoded})"
                    ]
                else:
                    if distinct_face < max(1, int(math.ceil(both_face * 0.45))):
                        return False, [
                            f"split QA failed: occurrence {event_index + 1} appears to duplicate the same speaker"
                        ]
                    if top_center_ok < max(1, int(math.ceil(both_face * 0.45))) or bottom_center_ok < max(1, int(math.ceil(both_face * 0.45))):
                        return False, [
                            f"split QA failed: occurrence {event_index + 1} speaker panels are not independently centred"
                        ]
            elif seam_votes < max(2, int(math.ceil(decoded * 0.50))):
                return False, [
                    f"split QA failed: occurrence {event_index + 1} has no repeated 50/50 split seam"
                ]

            def mean_signature(rows: List[Tuple[float, ...]]) -> Optional[Tuple[float, ...]]:
                if not rows:
                    return None
                dim = len(rows[0])
                valid = [row for row in rows if len(row) == dim]
                if not valid:
                    return None
                return tuple(sum(float(row[k]) for row in valid) / len(valid) for k in range(dim))

            identity_rows.append((mean_signature(top_sigs), mean_signature(bottom_sigs)))
            notes.append(
                f"split QA occurrence {event_index + 1}: {event_duration:.2f}s, "
                f"two-panel samples={both_face}/{decoded}, seam={seam_votes}/{decoded}, "
                f"motion median top/bottom={top_motion_med:.2f}/{bottom_motion_med:.2f}, max={top_motion_max:.2f}/{bottom_motion_max:.2f}"
            )

        # Required alternating order.  Compare actual FINAL-MP4 face appearance,
        # not the intended filter orientation.  For each consecutive pair, the
        # next top should match the previous bottom more closely than previous top.
        for i in range(1, len(identity_rows)):
            prev_top, prev_bottom = identity_rows[i - 1]
            cur_top, cur_bottom = identity_rows[i]
            if not all((prev_top, prev_bottom, cur_top, cur_bottom)):
                notes.append(f"split QA occurrence {i + 1}: order reversal face-signature check unavailable")
                continue
            same_order = (
                _speaker_signature_distance(prev_top, cur_top)
                + _speaker_signature_distance(prev_bottom, cur_bottom)
            )
            reversed_order = (
                _speaker_signature_distance(prev_top, cur_bottom)
                + _speaker_signature_distance(prev_bottom, cur_top)
            )
            if reversed_order >= same_order:
                return False, [
                    f"split QA failed: occurrence {i + 1} did not reverse TOP/BOTTOM order "
                    f"(reversed={reversed_order:.4f}, same={same_order:.4f})"
                ]
            notes.append(
                f"split QA occurrence {i + 1}: TOP/BOTTOM reversal verified "
                f"({reversed_order:.4f} < {same_order:.4f})"
            )
    finally:
        cap.release()

    return True, notes



def render_auto_clip(
    source_path: Path,
    paragraphs: List["TranscriptParagraph"],
    clip_start: float,
    clip_end: float,
    segment: ViralSegment,
    clip_index: int,
    duration_label: str,
    caption_template: str,
    cancel_event: threading.Event,
    render_timeout_seconds: Optional[int] = None,
    precomputed_word_timings: Optional[List[ClipperWordTiming]] = None,
    qa_retry_mode: bool = False,
    ai_edit_plan: Optional[Dict[str, Any]] = None,
    split_enabled: bool = True,
) -> Tuple[Path, List[Path], str]:
    ensure_not_cancelled(cancel_event)
    cleanup_files: List[Path] = []

    reframe_plan = analyze_smart_reframe_plan(
        source_path,
        clip_start,
        clip_end,
        cancel_event,
    )

    # V8.7 production audit: the primary planner already emits dense verified
    # face/body keyframes. Running a second full detector pass on every render
    # duplicated work, caused multi-minute stalls on 4K/profile shots, and added
    # another failure surface without improving a healthy dynamic track. Only
    # invoke the expensive rescue when the planner genuinely lacks a usable
    # dynamic face track.
    reference_count = 0
    planner_track_usable = (
        reframe_plan.mode == "dynamic"
        and len(reframe_plan.primary_track or []) >= 4
        and len(reframe_plan.primary_y_track or []) >= 4
    )
    if not planner_track_usable:
        reference_x, reference_y, reference_count = _build_forced_face_center_tracks(
            source_path, clip_start, clip_end, cancel_event,
            reframe_plan.primary_track, reframe_plan.primary_y_track,
        )
        if reference_count >= 3 and reference_x:
            normal_x = list(reframe_plan.primary_track or [])
            normal_y = list(reframe_plan.primary_y_track or [])
            source_media_for_fusion = probe_media(source_path)
            fused_x, fused_y = _fuse_verified_face_and_body_tracks(
                reference_x, reference_y or normal_y, normal_x, normal_y,
                max(0.1, clip_end - clip_start),
                int(source_media_for_fusion.get("width") or 1080),
                int(source_media_for_fusion.get("height") or 1920),
            )
            reframe_plan.primary_track = fused_x or reference_x
            reframe_plan.primary_y_track = fused_y or reference_y or normal_y
            reframe_plan.note += f"; v8.7 rescue face/body fusion samples={reference_count}"
    else:
        reframe_plan.note += "; v8.7 reused primary verified dynamic track (no redundant detector pass)"

    # External AI cannot control editing. Compatibility input is ignored.
    ai_edit_plan = {}

    working_source, working_start, ai_cleanup, upscale_note = (
        maybe_ai_upscale_clipper_source(
            source_path,
            clip_start,
            clip_end,
            cancel_event,
        )
    )
    cleanup_files.extend(ai_cleanup)

    working_media = probe_media(working_source)
    width = int(working_media.get("width") or 0)
    height = int(working_media.get("height") or 0)
    fps = float(working_media.get("fps") or 0) or 30.0
    duration = max(0.1, clip_end - clip_start)

    if precomputed_word_timings is not None:
        word_timings = _clean_clipper_word_timings(
            precomputed_word_timings
        )
        word_cleanup: List[Path] = []
        caption_note = "precomputed exact Whisper word timestamps"
    else:
        word_timings, word_cleanup, caption_note = (
            transcribe_clip_word_timings(
                source_path,
                clip_start,
                clip_end,
                paragraphs,
                cancel_event,
            )
        )
    cleanup_files.extend(word_cleanup)

    work_id = uuid.uuid4().hex[:10]
    hook_path = DOWNLOAD_DIR / f"clipper_hook_{work_id}.png"
    ass_path = DOWNLOAD_DIR / f"clipper_captions_{work_id}.ass"
    camera_command_path = DOWNLOAD_DIR / f"clipper_camera_{work_id}.cmd"
    cleanup_files.append(camera_command_path)
    output_path = DOWNLOAD_DIR / (
        f"AutoClip_{clip_index:02d}_{safe_filename(duration_label)}_"
        f"{work_id}.mp4"
    )

    hook_enabled = bool(_clipper_clean_text(segment.hook, 100))
    captions_enabled = caption_template != "none"
    if hook_enabled:
        cleanup_files.append(hook_path)
        generate_hook_overlay(segment.hook, hook_path)
    if captions_enabled:
        cleanup_files.append(ass_path)
        generate_animated_captions(
            word_timings,
            ass_path,
            caption_template,
        )

    # Keep cross-camera split windows on the verified A/B camera timeline.
    # Never retime them just because another part of the audio is louder: moving
    # a split away from its verified shot identity was a direct cause of wrong
    # reference footage and apparent lip/audio delay.

    # V8.18 CapCut-style A/B split: BOTH TOP and BOTTOM are moving VIDEO
    # layers.  They are always silent; input-0 owns the single master audio
    # timeline.  The identity currently visible at this timestamp is overlaid
    # from current-time input-0 for lip sync, while the other speaker continues
    # moving from a verified same-identity reference run.  No panel is frozen.
    # V8.22 invariant: Split Director never uses reference/history panels.
    # Both split halves must come from input-0 at the exact same timestamp.
    if reframe_plan.cross_scene_split_intervals:
        raise RuntimeError("Split Director invariant violated: cross-timestamp split requested")

    split_reference_inputs: Optional[Tuple[int, int]] = None
    split_reference_paths: List[Path] = []
    split_reference_face_verified = False
    if (
        split_enabled
        and reframe_plan.cross_scene_split_intervals
        and reframe_plan.split_primary_reference_time >= 0.0
        and reframe_plan.split_secondary_reference_time >= 0.0
        and reframe_plan.split_primary_track
        and reframe_plan.split_secondary_track
    ):
        left_ref = DOWNLOAD_DIR / f"clipper_split_left_{work_id}.mp4"
        right_ref = DOWNLOAD_DIR / f"clipper_split_right_{work_id}.mp4"
        left_t = float(reframe_plan.split_primary_reference_time)
        right_t = float(reframe_plan.split_secondary_reference_time)
        left_window_abs = (
            working_start + float(reframe_plan.split_primary_reference_window[0]),
            working_start + float(reframe_plan.split_primary_reference_window[1]),
        ) if reframe_plan.split_primary_reference_window[0] >= 0.0 else None
        right_window_abs = (
            working_start + float(reframe_plan.split_secondary_reference_window[0]),
            working_start + float(reframe_plan.split_secondary_reference_window[1]),
        ) if reframe_plan.split_secondary_reference_window[0] >= 0.0 else None

        # Build moving, independently face/body-centred silent panels first.
        left_ok = _extract_split_reference_panel(
            working_source,
            working_start + left_t,
            _track_value_at(reframe_plan.split_primary_track, left_t, 0.30),
            _track_value_at(reframe_plan.split_primary_y_track, left_t, 0.40),
            float(reframe_plan.split_primary_face_h or reframe_plan.primary_face_h or 0.0),
            left_ref, left_window_abs, cancel_event,
            [(working_start + float(t), float(v)) for t, v in reframe_plan.split_primary_track],
            [(working_start + float(t), float(v)) for t, v in reframe_plan.split_primary_y_track],
            [(working_start + float(t), float(v)) for t, v in reframe_plan.primary_track],
            [(working_start + float(t), float(v)) for t, v in reframe_plan.primary_y_track],
        )
        right_ok = _extract_split_reference_panel(
            working_source,
            working_start + right_t,
            _track_value_at(reframe_plan.split_secondary_track, right_t, 0.70),
            _track_value_at(reframe_plan.split_secondary_y_track, right_t, 0.40),
            float(reframe_plan.split_secondary_face_h or reframe_plan.primary_face_h or 0.0),
            right_ref, right_window_abs, cancel_event,
            [(working_start + float(t), float(v)) for t, v in reframe_plan.split_secondary_track],
            [(working_start + float(t), float(v)) for t, v in reframe_plan.split_secondary_y_track],
            [(working_start + float(t), float(v)) for t, v in reframe_plan.primary_track],
            [(working_start + float(t), float(v)) for t, v in reframe_plan.primary_y_track],
        )

        # Deterministic LIVE FFmpeg salvage only.  Static-image fallbacks are not
        # allowed because both split halves must visibly keep moving.
        if not left_ok:
            left_ok = _render_split_panel_bounded_fallback(
                working_source, working_start + left_t, 6.2,
                _track_value_at(reframe_plan.split_primary_track, left_t, 0.30),
                _track_value_at(reframe_plan.split_primary_y_track, left_t, 0.40),
                left_ref, cancel_event,
            )
        if not right_ok:
            right_ok = _render_split_panel_bounded_fallback(
                working_source, working_start + right_t, 6.2,
                _track_value_at(reframe_plan.split_secondary_track, right_t, 0.70),
                _track_value_at(reframe_plan.split_secondary_y_track, right_t, 0.40),
                right_ref, cancel_event,
            )

        if left_ok and right_ok:
            left_ok = _recenter_rendered_split_panel(left_ref, cancel_event)
            right_ok = _recenter_rendered_split_panel(right_ref, cancel_event)

        if left_ok and right_ok:
            split_reference_paths = [left_ref, right_ref]
            cleanup_files.extend(split_reference_paths)
            first_ref_index = 2 if hook_enabled else 1
            split_reference_inputs = (first_ref_index, first_ref_index + 1)
            split_reference_face_verified = True
        else:
            logger.error(
                "Verified A/B split moving-panel build failed after live salvage: primary=%s secondary=%s source=%s",
                bool(left_ok), bool(right_ok), working_source,
            )
            for failed_ref in (left_ref, right_ref):
                try:
                    failed_ref.unlink(missing_ok=True)
                except Exception:
                    pass
            raise RuntimeError(
                "Verified two-speaker A/B split could not build both moving centred panels"
            )
    layout_filter, motion_track = _build_smart_layout_filter(
        reframe_plan,
        width,
        height,
        fps,
        duration,
        camera_command_path,
        safe_framing=qa_retry_mode,
        split_enabled=split_enabled,
        split_reference_inputs=split_reference_inputs,
    )

    # Source timestamps/cadence are preserved. Per-frame sendcmd crop coordinates
    # run on the native timeline; no unnecessary 25->30 frame conversion.
    ass_filter_path = str(ass_path).replace("\\", "/").replace(":", r"\:")
    base_motion = layout_filter + "[layout]setpts=PTS-STARTPTS[motion];"
    if hook_enabled and captions_enabled:
        filter_complex = (
            base_motion
            + f"[motion][1:v]overlay=0:70:enable='between(t,0,{max(0.5, min(5.0, float(segment.hook_duration))):.2f})'[hooked];"
            + f"[hooked]ass='{ass_filter_path}'[vout]"
        )
    elif hook_enabled:
        filter_complex = (
            base_motion
            + f"[motion][1:v]overlay=0:70:enable='between(t,0,{max(0.5, min(5.0, float(segment.hook_duration))):.2f})'[vout]"
        )
    elif captions_enabled:
        filter_complex = base_motion + f"[motion]ass='{ass_filter_path}'[vout]"
    else:
        filter_complex = base_motion + "[motion]null[vout]"

    # CAPCUT-STYLE MASTER AUDIO TRACK: input-0 owns exactly one audio layer for
    # the whole clip. TOP/BOTTOM split layers are VIDEO-ONLY (-an), so two-panel
    # mode can never double, attenuate, echo or desynchronise the dialogue. Use
    # the exact same zero-based clip timeline as video. Do not normalize/amix: those
    # filters caused real silence/level changes and made correct video look delayed.
    # Risky words may still be masked visually in captions, but source audio stays
    # byte-content-equivalent apart from the final AAC encode.
    filter_complex = (
        filter_complex
        + f";[0:a:0]atrim=start=0:end={duration:.3f},asetpts=PTS-STARTPTS[aout]"
    )
    audio_map = "[aout]"

    command = [
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "warning",
        "-ss", f"{working_start:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(working_source),
        *(
            ["-loop", "1", "-framerate", f"{max(24.0, min(60.0, fps)):.3f}", "-i", str(hook_path)]
            if hook_enabled
            else []
        ),
        *(
            [
                "-stream_loop", "-1", "-an", "-i", str(split_reference_paths[0]),
                "-stream_loop", "-1", "-an", "-i", str(split_reference_paths[1]),
            ]
            if len(split_reference_paths) == 2
            else []
        ),
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", audio_map,
        "-t", f"{duration:.3f}",
        "-c:v", "libx264",
        "-preset", "slow",
        "-crf", "12",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-threads", str(FFMPEG_THREADS),
        "-c:a", "aac",
        "-b:a", "256k",
        "-movflags", "+faststart",
        "-vsync", "0",
        str(output_path),
    ]

    run_process(
        command,
        cancel_event=cancel_event,
        error_prefix="Auto Clipper render failed",
        timeout_seconds=(
            int(render_timeout_seconds)
            if render_timeout_seconds
            else min(
                AUTO_CLIPPER_FFMPEG_TIMEOUT_SECONDS,
                max(180, int(duration * 12 + 120)),
            )
        ),
    )

    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise RuntimeError("Auto Clipper output was not created")

    output_media = probe_media(output_path)
    output_duration = float(output_media.get("duration") or 0)
    if output_duration and abs(output_duration - duration) > 1.25:
        raise RuntimeError(
            f"Rendered duration mismatch: expected {duration:.1f}s, "
            f"got {output_duration:.1f}s"
        )

    # Mandatory FINAL-MP4 split QA.  A planned interval is not success: the
    # delivered file itself must show >=4s two-panel video, both panels must be
    # live, distinct speakers must not be duplicated, and later occurrences must
    # reverse TOP/BOTTOM order.
    expected_split_events = sorted(
        list(reframe_plan.dual_speaker_intervals[:120])
        + list(reframe_plan.cross_scene_split_intervals[:40]),
        key=lambda pair: (float(pair[0]), float(pair[1])),
    ) if split_enabled else []
    split_qa_notes: List[str] = []
    if expected_split_events:
        split_qa_ok, split_qa_notes = _verify_expected_split_output(
            output_path, expected_split_events,
            require_static_panels=False,
            verified_static_reaction_faces=split_reference_face_verified,
        )
        if not split_qa_ok:
            raise RuntimeError(
                "Final MP4 two-speaker split QA failed: "
                + "; ".join(split_qa_notes[:4])
            )
        split_audio_ok, split_audio_notes = _verify_split_audio_matches_source(
            working_source, output_path, working_start, expected_split_events
        )
        split_qa_notes.extend(split_audio_notes)
        if not split_audio_ok:
            raise RuntimeError(
                "Final MP4 split audio QA failed: " + "; ".join(split_audio_notes[:4])
            )

    # Never fabricate 4K by scaling a 1080p vertical master. Auto Clipper
    # already chooses the highest REAL YouTube source (4K first when present).
    # The vertical editor remains 1080x1920 because a 16:9 4K source does not
    # contain enough vertical pixels for a genuine 2160x3840 portrait crop.
    source_is_4k = width >= 2160 or height >= 2160
    delivery_upscale_note = (
        "native YouTube 4K/2160p source used; portrait crop preserves native vertical pixels; no synthetic 4K upscale"
        if source_is_4k
        else "native source used; synthetic 4K upscale disabled"
    )

    note = (
        f"{reframe_plan.note}; {upscale_note}; {delivery_upscale_note}; {caption_note}; "
        f"caption template={caption_template}; auto_split={'on' if split_enabled else 'off'} alternating_top_bottom=on; "
        f"split_final_mp4_qa={'PASS' if expected_split_events else 'not-required'} events={len(expected_split_events)}; "
        f"source_audio_locked=single-master-track; cross_split=current-live+moving-silent-reference; blur_canvas_vertical=off; keyframes=low-lag-zero-phase-deadband"
    )
    return output_path, cleanup_files, note


async def run_auto_clipper_worker_with_heartbeat(
    worker: Callable[..., Any],
    worker_args: Tuple[Any, ...],
    progress: Callable[[str], Awaitable[None]],
    label: str,
    cancel_event: threading.Event,
    timeout_seconds: int = AUTO_CLIPPER_RENDER_TIMEOUT_SECONDS,
) -> Any:
    # Queue only the heavy local render/analysis worker. Waiting for a render
    # slot does not consume the guardian timeout. This keeps multiple bot jobs
    # alive without letting simultaneous encodes overload the VPS.
    async with LOCAL_EDITOR_RENDER_SEMAPHORE:
        task = asyncio.create_task(asyncio.to_thread(worker, *worker_args))
        started_at = time.monotonic()

        while True:
            elapsed = time.monotonic() - started_at
            remaining = timeout_seconds - elapsed
            if remaining <= 0:
                cancel_event.set()
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=10)
                except BaseException:
                    pass
                if not task.done():
                    task.cancel()
                raise RuntimeError(
                    f"Auto Clipper step timed out after {timeout_seconds} seconds"
                )

            try:
                return await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=min(
                        AUTO_CLIPPER_PROGRESS_INTERVAL_SECONDS,
                        max(0.1, remaining),
                    ),
                )
            except asyncio.TimeoutError:
                await progress(
                    f"{label}\n"
                    f"⏱ Processing: {format_time(elapsed)}\n"
                    "The local editor is still running safely."
                )

def build_auto_clipper_telegram_caption(
    segment: ViralSegment,
    clip_start: float,
    clip_end: float,
    duration_label: str,
) -> str:
    elite = "🏆 ELITE PICK\n" if segment.elite_pick else ""
    rank = f"Rank #{segment.rank}\n" if segment.rank else ""
    deep = segment.deep_meta or {}
    roman = _vf_text(deep.get("roman_urdu"), 220)
    hook_type = _vf_text(deep.get("hook_type") or (deep.get("hook_lab") or {}).get("recommended_hook_type"), 80)
    risk = deep.get("platform_risk")
    review = bool(deep.get("manual_review"))
    extra_lines = []
    if roman:
        extra_lines.append(f"🗣 Roman Urdu: {roman}")
    if hook_type:
        extra_lines.append(f"🪝 Hook: {hook_type}")
    if risk is not None:
        extra_lines.append(f"🛡 Platform Risk: {risk}/100")
    if review:
        extra_lines.append("⚠️ HUMAN REVIEW REQUIRED")
    extra = ("\n" + "\n".join(extra_lines)) if extra_lines else ""
    caption = (
        f"{elite}{rank}"
        f"🔥 Viral Potential Score: {segment.score}/100\n\n"
        f"{segment.caption}\n\n"
        f"{segment.hashtags}\n\n"
        f"⏱ Duration: {duration_label}\n"
        f"📍 Original: {_clipper_format_timestamp(clip_start)} - "
        f"{_clipper_format_timestamp(clip_end)}\n"
        f"💡 Why selected: {segment.reason}"
        f"{extra}"
    )
    return caption[:1000]


def auto_clipper_mode_menu() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("⚡ Quick Clip", callback_data="clipmode:quick"),
            InlineKeyboardButton("🧠 Deep Viral Research ⭐", callback_data="clipmode:deep"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu:home")],
    ]
    return InlineKeyboardMarkup(rows)


async def handle_auto_clipper_mode_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not query.message or not user:
        return
    if ai_service_gate is not None and not await ai_service_gate(update, context):
        return
    try:
        await query.answer()
    except TelegramError:
        pass
    if not is_auto_clipper_allowed(user.id):
        await safe_edit_message(query.message, paid_access_text("clipper", user.id))
        return
    mode = (query.data or "").split(":", 1)[-1]
    if mode not in {"quick", "deep"}:
        return
    if mode == "deep" and not deep_research_enabled():
        await safe_edit_message(
            query.message,
            "🧠 Deep Viral Research module is not installed/configured on this server yet.\n\n"
            "⚡ Quick Clip is still available.",
            reply_markup=auto_clipper_mode_menu(),
        )
        return
    context.user_data["mode"] = "auto_clipper"
    context.user_data["auto_clipper_analysis_mode"] = mode
    if mode == "deep":
        text = (
            "🧠 Deep Viral Research\n\n"
            "Send a YouTube link or upload the full podcast.\n\n"
            "Workflow:\n"
            "Research host/guest/topic/audience → understand the COMPLETE timestamped transcript → "
            "analyze representative visuals across the FULL source video → build one whole-podcast map → "
            "ONLY THEN create candidates → actual reaction analysis → first 5-second Hook Lab → "
            "global ranking → 10 final clips.\n\n"
            "🏆 Clips #1–#3 are marked ELITE PICKS.\n"
            "If a research/TikTok provider is unavailable, the bot will show the evidence gap instead of inventing data."
        )
    else:
        text = (
            "⚡ Quick Clip\n\n"
            "Send a YouTube link or upload a long video file.\n"
            "This uses the existing fast Auto Clipper pipeline."
        )
    await safe_edit_message(
        query.message,
        text + "\n\n" + owner_footer(),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="menu:clipper")]]),
    )


def auto_clipper_duration_menu(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔥 30-60 sec",
                    callback_data=f"clipdur:{job_id}:60",
                ),
                InlineKeyboardButton(
                    "⚡ 60-90 sec",
                    callback_data=f"clipdur:{job_id}:90",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🎬 90 sec - 3 min",
                    callback_data=f"clipdur:{job_id}:120",
                ),
                InlineKeyboardButton(
                    "🤖 Auto Best Length",
                    callback_data=f"clipdur:{job_id}:all",
                ),
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data=f"c:{job_id}",
                )
            ],
        ]
    )


# =============================================================================
# General helpers
# =============================================================================

def owner_footer() -> str:
    username = f"@{OWNER_USERNAME}" if OWNER_USERNAME else ""
    suffix = f" ({username})" if username else ""
    return f"Owner: {OWNER_DISPLAY_NAME}{suffix}"


def is_owner(update: Update) -> bool:
    user = update.effective_user
    if not user:
        return False
    if OWNER_ID > 0 and user.id == OWNER_ID:
        return True
    username = (user.username or "").strip().lstrip("@").lower()
    return bool(OWNER_USERNAME and username == OWNER_USERNAME)


async def reject_non_owner(update: Update) -> bool:
    if is_owner(update):
        return False
    if update.callback_query:
        await update.callback_query.answer(
            "Administrator access is required.",
            show_alert=True,
        )
    elif update.message:
        await update.message.reply_text(
            f"This command is not available.\n\n{owner_footer()}"
        )
    return True


def clear_old_downloads() -> None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for item in DOWNLOAD_DIR.iterdir():
        try:
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink(missing_ok=True)
        except Exception:
            logger.exception("Could not remove old item: %s", item)


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w\-.()\[\] ]+", "", name, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return (cleaned or "file")[:140]


def extract_youtube_url(text: str) -> Optional[str]:
    match = YOUTUBE_URL_RE.search(text or "")
    if not match:
        return None
    url = match.group(0).rstrip(".,);]}>")
    if not url.lower().startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return (
                f"{value:.0f} {unit}"
                if unit == "B"
                else f"{value:.1f} {unit}"
            )
        value /= 1024
    return f"{value:.1f} PB"


def format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def numeric(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def optional_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def normalize_proxy(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise ValueError("The proxy value is empty.")
    if any(ch.isspace() for ch in value):
        raise ValueError("Each proxy must be on a separate line.")

    if "://" not in value:
        pieces = value.split(":")
        if len(pieces) == 4:
            host, port, username, password = pieces
            if not port.isdigit():
                raise ValueError("The proxy port must be numeric.")
            value = (
                f"http://{quote(username, safe='')}:{quote(password, safe='')}"
                f"@{host}:{port}"
            )
        else:
            value = f"http://{value}"

    parsed = urlsplit(value)
    allowed = {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}
    if parsed.scheme.lower() not in allowed:
        raise ValueError("Unsupported proxy protocol.")
    if not parsed.hostname:
        raise ValueError("The proxy host is missing.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("The proxy port is invalid.") from exc
    if port is None or not 1 <= port <= 65535:
        raise ValueError("The proxy port must be between 1 and 65535.")
    return value


def mask_proxy(proxy_url: Optional[str]) -> str:
    if not proxy_url:
        return "Not configured"
    try:
        parsed = urlsplit(proxy_url)
        auth = ""
        if parsed.username:
            auth = f"{parsed.username}:***@"
        host = parsed.hostname or "?"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port else ""
        return urlunsplit((parsed.scheme, f"{auth}{host}{port}", "", "", ""))
    except Exception:
        return "Configured (credentials hidden)"


def cookies_file_valid(path: Path) -> Tuple[bool, str]:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return False, "The cookies file is empty."
        if path.stat().st_size > 5 * 1024 * 1024:
            return False, "The cookies file must be smaller than 5 MB."
        sample = path.read_bytes()[:512 * 1024].decode(
            "utf-8", errors="ignore"
        )
        lines = sample.splitlines()
        first_line = lines[0].strip() if lines else ""
        if first_line not in {
            "# Netscape HTTP Cookie File",
            "# HTTP Cookie File",
        }:
            return False, "The file is not in Netscape cookies.txt format."
        lowered = sample.lower()
        if "youtube.com" not in lowered and "google.com" not in lowered:
            return False, "No YouTube or Google cookies were found."
        return True, "OK"
    except Exception as exc:
        return False, f"Cookie validation error: {str(exc)[:120]}"


async def safe_edit_message(message, text: str, reply_markup=None) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            logger.warning("Could not edit message: %s", exc)
    except TelegramError as exc:
        logger.warning("Could not edit message: %s", exc)


def stop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⛔ Stop Task", callback_data="stop:active")]]
    )


async def delete_sensitive_message(message) -> None:
    try:
        await message.delete()
    except TelegramError:
        pass


def is_too_large_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        phrase in text
        for phrase in (
            "request entity too large",
            "entity too large",
            "file is too big",
            "file too large",
            "413",
        )
    )


def media_dimensions(
    width: int,
    height: int,
    target_short_side: int,
) -> Tuple[int, int]:
    if width <= 0 or height <= 0:
        return target_short_side, target_short_side

    if width >= height:
        out_h = target_short_side
        out_w = round(width * target_short_side / height)
    else:
        out_w = target_short_side
        out_h = round(height * target_short_side / width)

    out_w = max(2, out_w - out_w % 2)
    out_h = max(2, out_h - out_h % 2)
    return out_w, out_h


def probe_media(path: Path) -> Dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-print_format", "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    raw = json.loads(result.stdout)
    video_stream = next(
        (
            stream
            for stream in raw.get("streams", [])
            if stream.get("codec_type") == "video"
        ),
        {},
    )
    duration = numeric(
        video_stream.get("duration")
        or raw.get("format", {}).get("duration")
    )
    return {
        "width": optional_int(video_stream.get("width")) or 0,
        "height": optional_int(video_stream.get("height")) or 0,
        "duration": duration,
        "fps": parse_frame_rate(video_stream.get("avg_frame_rate")),
    }


def parse_frame_rate(value: Any) -> float:
    text = str(value or "")
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        den = numeric(denominator, 1)
        return numeric(numerator, 25) / den if den else 25.0
    return numeric(text, 25.0) or 25.0


# =============================================================================
# yt-dlp format helpers
# =============================================================================

def is_h264(fmt: Dict[str, Any]) -> bool:
    codec = str(fmt.get("vcodec") or "").lower()
    return codec.startswith(("avc1", "h264"))


def is_aac(fmt: Dict[str, Any]) -> bool:
    codec = str(fmt.get("acodec") or "").lower()
    return codec.startswith(("mp4a", "aac"))


def is_video_format(fmt: Dict[str, Any]) -> bool:
    return (
        bool(fmt.get("format_id"))
        and fmt.get("vcodec") not in (None, "none")
    )


def is_audio_only(fmt: Dict[str, Any]) -> bool:
    return (
        bool(fmt.get("format_id"))
        and fmt.get("vcodec") == "none"
        and fmt.get("acodec") not in (None, "none")
    )


def video_score(fmt: Dict[str, Any]) -> Tuple[float, ...]:
    return (
        numeric(fmt.get("quality"), -1),
        numeric(fmt.get("tbr")),
        numeric(fmt.get("fps")),
        numeric(fmt.get("width")),
        numeric(fmt.get("filesize") or fmt.get("filesize_approx")),
    )


def audio_score(fmt: Dict[str, Any]) -> Tuple[float, ...]:
    return (
        numeric(fmt.get("abr")),
        numeric(fmt.get("asr")),
        numeric(fmt.get("audio_channels")),
        numeric(fmt.get("filesize") or fmt.get("filesize_approx")),
    )


def estimate_component_size(
    fmt: Dict[str, Any],
    duration: float,
) -> Tuple[Optional[int], bool]:
    if fmt.get("filesize"):
        return int(fmt["filesize"]), False
    if fmt.get("filesize_approx"):
        return int(fmt["filesize_approx"]), True

    bitrate = numeric(fmt.get("tbr"))
    if not bitrate:
        bitrate = numeric(fmt.get("vbr")) + numeric(fmt.get("abr"))
    if duration > 0 and bitrate > 0:
        return int((bitrate * 1000 / 8) * duration), True
    return None, True


def estimate_selected_size(
    selected: List[Dict[str, Any]],
    duration: float,
) -> Tuple[Optional[int], bool]:
    sizes: List[int] = []
    approximate = False
    for fmt in selected:
        size, approx = estimate_component_size(fmt, duration)
        if size is None:
            return None, True
        sizes.append(size)
        approximate = approximate or approx
    return int(sum(sizes) * 1.03), True if len(selected) > 1 else approximate


def ydl_base_options(
    progress_hook=None,
    proxy_url: Optional[str] = None,
    use_cookies: bool = True,
    use_external_downloader: bool = True,
) -> Dict[str, Any]:
    options: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "retries": 5,
        "fragment_retries": 5,
        "extractor_retries": 3,
        "file_access_retries": 3,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": YTDLP_CONCURRENT_FRAGMENTS,
        # Native HTTP chunking can help when a CDN/webserver throttles a
        # long single HTTP transfer. Keep this configurable for providers
        # where chunking is not beneficial.
        "http_chunk_size": (
            YTDLP_HTTP_CHUNK_SIZE if YTDLP_HTTP_CHUNK_SIZE > 0 else None
        ),
        "buffersize": YTDLP_BUFFER_SIZE,
        "noresizebuffer": False,
        # If transfer speed collapses below this floor, yt-dlp can
        # re-extract the media URL instead of remaining on a bad CDN route.
        "throttledratelimit": (
            YTDLP_THROTTLED_RATE if YTDLP_THROTTLED_RATE > 0 else None
        ),
        "continuedl": True,
        "overwrites": True,
        "compat_opts": {"manifest-filesize-approx"},
    }
    if YTDLP_FORCE_IPV4:
        # Vultr instances often have both IPv4 and IPv6. Force IPv4 by
        # default so YouTube media requests use the tested IPv4 route.
        options["source_address"] = "0.0.0.0"

    # Modern YouTube extraction can require yt-dlp's external JS challenge
    # solver. Prefer Deno because this VPS has a working Deno + yt-dlp-ejs
    # installation; fall back to Node when Deno is unavailable.
    if YTDLP_ENABLE_EJS:
        deno_path = shutil.which("deno")
        if not deno_path and Path("/usr/local/bin/deno").is_file():
            deno_path = "/usr/local/bin/deno"
        node_path = shutil.which("node")
        if deno_path:
            options["js_runtimes"] = {"deno": {"path": deno_path}}
        elif node_path:
            options["js_runtimes"] = {"node": {"path": node_path}}
            options["remote_components"] = ["ejs:github"]

    # A fast VPS can still be throttled by a single googlevideo connection.
    # For direct (non-proxy) HTTP media transfers, use aria2c with multiple
    # range connections. Keep DASH/HLS on yt-dlp's native downloader; current
    # yt-dlp releases no longer support aria2c for those manifest protocols.
    if (
        use_external_downloader
        and not proxy_url
        and YTDLP_USE_ARIA2
        and shutil.which("aria2c")
    ):
        options["external_downloader"] = {
            "default": "aria2c",
            "m3u8": "native",
            "dash": "native",
        }
        options["external_downloader_args"] = {
            "aria2c": [
                "--continue=true",
                "--file-allocation=none",
                "--auto-file-renaming=false",
                f"--max-connection-per-server={YTDLP_ARIA2_CONNECTIONS}",
                f"--split={YTDLP_ARIA2_CONNECTIONS}",
                "--min-split-size=1M",
                "--connect-timeout=15",
                "--timeout=30",
                "--max-tries=5",
                "--retry-wait=1",
                "--summary-interval=1",
            ]
        }
        # aria2c handles HTTP range splitting itself.
        options["http_chunk_size"] = None

    if progress_hook:
        options["progress_hooks"] = [progress_hook]

    cookie_file = runtime_auth.get_cookie_file()
    if use_cookies and cookie_file and cookie_file.exists():
        options["cookiefile"] = str(cookie_file)

    if proxy_url:
        options["proxy"] = proxy_url

    return options


def auth_attempts() -> List[Tuple[Optional[str], bool, str]]:
    """Build YouTube route attempts with the VPS direct route first.

    Direct access should normally be fastest on a high-bandwidth VPS. Ranked
    proxies are fallback routes for throttling, geo restrictions, or failures.
    """
    proxies = runtime_auth.get_proxy_candidates(PROXY_ATTEMPT_LIMIT)
    cookie = runtime_auth.get_cookie_file()
    cookies_available = bool(cookie and cookie.exists())

    attempts: List[Tuple[Optional[str], bool, str]] = []
    seen: set[Tuple[Optional[str], bool]] = set()

    def add(
        attempt_proxy: Optional[str],
        use_cookies: bool,
        description: str,
    ) -> None:
        key = (attempt_proxy, use_cookies)
        if key not in seen:
            seen.add(key)
            attempts.append((attempt_proxy, use_cookies, description))

    if DIRECT_DOWNLOAD_FIRST:
        # Public videos should not inherit a possibly rate-limited YouTube
        # account session. Try the clean VPS route first; cookies are only
        # used as a fallback for age/private/member restricted content.
        if YTDLP_PUBLIC_DIRECT_FIRST:
            add(None, False, "direct VPS public primary route")
            if cookies_available:
                add(None, True, "direct VPS authenticated fallback")
        else:
            add(None, cookies_available, "direct VPS primary route")
            if cookies_available:
                add(None, False, "direct VPS public fallback")

    for index, proxy in enumerate(proxies, 1):
        # Prefer a clean public proxy session first for public videos, then
        # retry the same proxy with cookies only when authentication is needed.
        add(proxy, False, f"ranked proxy #{index} public")
        if cookies_available:
            add(proxy, True, f"ranked proxy #{index} authenticated")

    if not DIRECT_DOWNLOAD_FIRST:
        if YTDLP_PUBLIC_DIRECT_FIRST:
            add(None, False, "direct VPS public fallback")
            if cookies_available:
                add(None, True, "direct VPS authenticated fallback")
        else:
            add(None, cookies_available, "direct VPS primary route")
            if cookies_available:
                add(None, False, "direct VPS public fallback")

    return attempts


async def extract_info(url: str) -> Dict[str, Any]:
    """Read YouTube metadata through the central fresh-route broker."""
    try:
        await refresh_proxy_benchmarks_if_needed()
    except Exception as exc:
        logger.info("Proxy benchmark refresh skipped: %s", exc)
    try:
        return await media_transport.extract_info(url)
    except TransportCancelled as exc:
        raise JobCancelled(str(exc)) from exc



def build_quality_choices(
    info: Dict[str, Any],
) -> Dict[str, QualityChoice]:
    formats = [
        fmt
        for fmt in (info.get("formats") or [])
        if isinstance(fmt, dict)
    ]
    duration = numeric(info.get("duration"))
    choices: Dict[str, QualityChoice] = {}

    audio_formats = [fmt for fmt in formats if is_audio_only(fmt)]
    aac_audio = [
        fmt
        for fmt in audio_formats
        if is_aac(fmt)
        and str(fmt.get("ext") or "").lower() in {"m4a", "mp4"}
    ]

    best_audio_any = (
        max(audio_formats, key=audio_score)
        if audio_formats
        else None
    )
    best_audio_aac = (
        max(aac_audio, key=audio_score)
        if aac_audio
        else None
    )

    for label in QUALITY_ORDER:
        height = QUALITY_TO_HEIGHT[label]
        exact = [
            fmt
            for fmt in formats
            if is_video_format(fmt)
            and optional_int(fmt.get("height")) == height
        ]
        if not exact:
            continue

        selected: List[Dict[str, Any]]
        selector: str
        playable = False
        container = "mkv"

        progressive = [
            fmt
            for fmt in exact
            if fmt.get("acodec") not in (None, "none")
            and is_h264(fmt)
            and is_aac(fmt)
            and str(fmt.get("ext") or "").lower() == "mp4"
        ]

        if progressive:
            video = max(progressive, key=video_score)
            selected = [video]
            selector = str(video["format_id"])
            playable = True
            container = "mp4"
        else:
            h264_only = [
                fmt
                for fmt in exact
                if fmt.get("acodec") == "none"
                and is_h264(fmt)
                and str(fmt.get("ext") or "").lower() == "mp4"
            ]
            if h264_only and best_audio_aac:
                video = max(h264_only, key=video_score)
                selected = [video, best_audio_aac]
                selector = (
                    f"{video['format_id']}+{best_audio_aac['format_id']}"
                )
                playable = True
                container = "mp4"
            else:
                progressive_any = [
                    fmt
                    for fmt in exact
                    if fmt.get("acodec") not in (None, "none")
                ]
                if progressive_any:
                    video = max(progressive_any, key=video_score)
                    selected = [video]
                    selector = str(video["format_id"])
                    container = str(video.get("ext") or "mkv").lower()
                    if container not in {"mp4", "mkv", "webm"}:
                        container = "mkv"
                else:
                    video_only = [
                        fmt for fmt in exact if fmt.get("acodec") == "none"
                    ]
                    if not video_only or not best_audio_any:
                        continue
                    video = max(video_only, key=video_score)
                    selected = [video, best_audio_any]
                    selector = (
                        f"{video['format_id']}+{best_audio_any['format_id']}"
                    )

        size, approximate = estimate_selected_size(selected, duration)
        choices[label] = QualityChoice(
            label=label,
            height=height,
            selector=selector,
            estimated_size=size,
            approximate=approximate,
            telegram_video=playable,
            output_container=container,
            width=optional_int(selected[0].get("width")),
            fps=numeric(selected[0].get("fps")) or None,
        )

    return choices


def make_progress_hook(
    loop: asyncio.AbstractEventLoop,
    progress_cb: ProgressCallback,
    cancel_event: threading.Event,
    *,
    abort_slow_direct: bool = False,
    route_label: str = "",
):
    state = {
        "last": 0.0,
        "started": time.monotonic(),
        "slow_since": None,
    }

    def hook(data: Dict[str, Any]) -> None:
        ensure_not_cancelled(cancel_event)
        status = data.get("status")
        if status == "downloading":
            now = time.monotonic()

            # When proxies exist, do not let a persistently throttled direct
            # YouTube CDN route hold a job at a few hundred KB/s forever.
            # Give the route time to ramp up, then move to the fastest ranked
            # proxy only if the measured speed stays below the configured floor.
            if abort_slow_direct and DIRECT_SLOW_FALLBACK_BPS > 0:
                try:
                    speed_bps = float(data.get("speed") or 0.0)
                except (TypeError, ValueError):
                    speed_bps = 0.0
                try:
                    downloaded = int(data.get("downloaded_bytes") or 0)
                except (TypeError, ValueError):
                    downloaded = 0
                elapsed = now - float(state["started"])

                if (
                    elapsed >= DIRECT_SLOW_GRACE_SECONDS
                    and downloaded >= DIRECT_SLOW_MIN_BYTES
                    and speed_bps > 0
                    and speed_bps < DIRECT_SLOW_FALLBACK_BPS
                ):
                    if state["slow_since"] is None:
                        state["slow_since"] = now
                    elif now - float(state["slow_since"]) >= DIRECT_SLOW_CONFIRM_SECONDS:
                        speed_kib = speed_bps / 1024.0
                        raise SlowDownloadRoute(
                            f"{route_label or 'direct route'} stayed slow at "
                            f"{speed_kib:.0f} KiB/s; trying proxy fallback"
                        )
                else:
                    state["slow_since"] = None

            if now - float(state["last"]) < 2:
                return
            state["last"] = now
            percent = str(data.get("_percent_str") or "0%").strip()
            speed = str(data.get("_speed_str") or "N/A").strip()
            eta = str(data.get("_eta_str") or "N/A").strip()
            message = f"Downloading: {percent} | {speed} | ETA {eta}"
        elif status == "finished":
            message = "Download complete. Preparing the file..."
        else:
            return
        asyncio.run_coroutine_threadsafe(progress_cb(message), loop)

    return hook


def find_output_file(stem: str) -> Optional[Path]:
    ignored = {
        ".part", ".ytdl", ".temp", ".tmp", ".json", ".description"
    }
    candidates = [
        path
        for path in DOWNLOAD_DIR.glob(f"{stem}.*")
        if path.is_file()
        and path.suffix.lower() not in ignored
        and not path.name.endswith(".info.json")
    ]
    return (
        max(candidates, key=lambda path: path.stat().st_size)
        if candidates
        else None
    )


def format_fallbacks(
    choice: Any,
) -> List[Tuple[str, str, bool]]:
    choice = normalize_quality_choice(choice)
    height = int(choice.get("height") or 720)
    exact = str(choice.get("selector") or "")
    container = str(choice.get("output_container") or "mkv")

    attempts = [
        (exact, container, bool(choice.get("telegram_video"))),
        (
            f"bestvideo[height={height}][ext=mp4][vcodec^=avc1]"
            f"+bestaudio[ext=m4a]/best[height={height}][ext=mp4]",
            "mp4",
            True,
        ),
        (
            f"bestvideo[height={height}]+bestaudio/best[height={height}]",
            "mkv",
            False,
        ),
        (
            f"bestvideo[height<={height}][ext=mp4][vcodec^=avc1]"
            f"+bestaudio[ext=m4a]/best[height<={height}][ext=mp4]",
            "mp4",
            True,
        ),
        (
            f"bestvideo[height<={height}]+bestaudio/"
            f"best[height<={height}]/best",
            "mkv",
            False,
        ),
    ]
    unique: List[Tuple[str, str, bool]] = []
    seen: set[Tuple[str, str]] = set()
    for selector, output, playable in attempts:
        key = (selector, output)
        if selector and key not in seen:
            seen.add(key)
            unique.append((selector, output, playable))
    return unique


async def download_video(
    job: Dict[str, Any],
    choice: Any,
    progress_cb: ProgressCallback,
    cancel_event: threading.Event,
) -> Tuple[Optional[Path], Optional[str]]:
    """Download a complete video through the shared multi-route transport."""
    choice = normalize_quality_choice(choice)
    async with cancellable_slot(DOWNLOAD_SEMAPHORE, cancel_event):
        ensure_not_cancelled(cancel_event)
        try:
            await refresh_proxy_benchmarks_if_needed()
        except Exception as exc:
            logger.info("Proxy benchmark refresh skipped for full download: %s", exc)

        base = (
            f"{safe_filename(str(job.get('title') or 'Video'))}_"
            f"{safe_filename(str(job.get('video_id') or 'id'))}_"
            f"{uuid.uuid4().hex[:8]}"
        )
        candidates = [
            MediaFormatCandidate(selector, container, playable)
            for selector, container, playable in format_fallbacks(choice)
        ]
        try:
            result = await media_transport.download_video(
                str(job["url"]),
                base,
                candidates,
                target_height=int(choice.get("height") or 0) or None,
                progress_cb=progress_cb,
                cancel_event=cancel_event,
            )
            ensure_not_cancelled(cancel_event)
            suffix = result.path.suffix.lower()
            choice["telegram_video"] = suffix == ".mp4"
            choice["output_container"] = suffix.lstrip(".") or result.output_container or "mp4"
            return result.path.resolve(), None
        except TransportCancelled as exc:
            raise JobCancelled(str(exc)) from exc
        except JobCancelled:
            raise
        except Exception as exc:
            logger.warning("Central full-video transport failed: %s", exc)
            return None, f"Download failed: {str(exc)[:420]}"



async def download_mp3(
    job: Dict[str, Any],
    progress_cb: ProgressCallback,
    cancel_event: threading.Event,
) -> Tuple[Optional[Path], Optional[str]]:
    """Download/convert MP3 through the same central route broker."""
    async with cancellable_slot(DOWNLOAD_SEMAPHORE, cancel_event):
        ensure_not_cancelled(cancel_event)
        try:
            await refresh_proxy_benchmarks_if_needed()
        except Exception as exc:
            logger.info("Proxy benchmark refresh skipped for MP3: %s", exc)
        base = f"{safe_filename(str(job.get('title') or 'Audio'))}_{uuid.uuid4().hex[:8]}"
        try:
            result = await media_transport.download_audio(
                str(job["url"]),
                base,
                audio_quality="160K",
                progress_cb=progress_cb,
                cancel_event=cancel_event,
            )
            ensure_not_cancelled(cancel_event)
            return result.path.resolve(), None
        except TransportCancelled as exc:
            raise JobCancelled(str(exc)) from exc
        except JobCancelled:
            raise
        except Exception as exc:
            logger.warning("Central MP3 transport failed: %s", exc)
            return None, f"MP3 download failed: {str(exc)[:420]}"



async def download_audio_for_transcript(
    job: Dict[str, Any],
    progress_cb: ProgressCallback,
    cancel_event: threading.Event,
) -> Tuple[Optional[Path], Optional[str]]:
    """Fetch transcript audio through the same central route broker."""
    async with cancellable_slot(DOWNLOAD_SEMAPHORE, cancel_event):
        ensure_not_cancelled(cancel_event)
        try:
            await refresh_proxy_benchmarks_if_needed()
        except Exception as exc:
            logger.info("Proxy benchmark refresh skipped for transcript audio: %s", exc)
        base = f"transcript_{job['job_id']}_{uuid.uuid4().hex[:8]}"
        try:
            result = await media_transport.download_audio(
                str(job["url"]),
                base,
                audio_quality="48K",
                progress_cb=progress_cb,
                cancel_event=cancel_event,
            )
            ensure_not_cancelled(cancel_event)
            return result.path.resolve(), None
        except TransportCancelled as exc:
            raise JobCancelled(str(exc)) from exc
        except JobCancelled:
            raise
        except Exception as exc:
            logger.warning("Central transcript-audio transport failed: %s", exc)
            return None, f"Audio download failed: {str(exc)[:420]}"



# =============================================================================
# Transcript engine
# =============================================================================

@dataclass
class TranscriptParagraph:
    start: float
    end: float
    text: str


_whisper_model = None
_whisper_lock = threading.RLock()
_transcript_cache_lock = threading.RLock()
_transcript_cache: Dict[str, Tuple[float, List[TranscriptParagraph], str]] = {}
_transcript_build_locks: Dict[str, asyncio.Lock] = {}


def transcript_cache_key(job: Dict[str, Any]) -> str:
    # URL comes first so separate users requesting the same video share one cached transcript.
    return str(job.get("url") or job.get("video_id") or job.get("job_id") or "")


def get_cached_transcript(
    job: Dict[str, Any],
) -> Optional[Tuple[List[TranscriptParagraph], str]]:
    key = transcript_cache_key(job)
    if not key:
        return None
    now = time.time()
    with _transcript_cache_lock:
        item = _transcript_cache.get(key)
        if not item:
            return None
        created_at, paragraphs, detected_language = item
        if now - created_at > TRANSCRIPT_CACHE_TTL_SECONDS:
            _transcript_cache.pop(key, None)
            return None
        copied = [
            TranscriptParagraph(p.start, p.end, p.text)
            for p in paragraphs
        ]
        return copied, detected_language


def set_cached_transcript(
    job: Dict[str, Any],
    paragraphs: List[TranscriptParagraph],
    detected_language: str,
) -> None:
    key = transcript_cache_key(job)
    if not key:
        return
    with _transcript_cache_lock:
        _transcript_cache[key] = (
            time.time(),
            [
                TranscriptParagraph(p.start, p.end, p.text)
                for p in paragraphs
            ],
            detected_language,
        )


def get_transcript_build_lock(job: Dict[str, Any]) -> asyncio.Lock:
    key = transcript_cache_key(job) or str(job.get("job_id") or uuid.uuid4().hex)
    lock = _transcript_build_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _transcript_build_locks[key] = lock
    return lock


def get_whisper_model():
    """
    Load one shared Faster-Whisper model.

    distil-large-v3 is the default because this bot is focused on English
    podcast speech. CPU INT8 keeps RAM use practical on a normal VPS.
    """
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            from faster_whisper import WhisperModel

            whisper_dir = MODEL_DIR / "whisper"
            whisper_dir.mkdir(parents=True, exist_ok=True)

            try:
                _whisper_model = WhisperModel(
                    WHISPER_MODEL_NAME,
                    device=WHISPER_DEVICE,
                    compute_type=WHISPER_COMPUTE_TYPE,
                    cpu_threads=WHISPER_CPU_THREADS,
                    num_workers=1,
                    download_root=str(whisper_dir),
                )
            except Exception:
                # Graceful safety fallback for unusual CPU/CTranslate2 builds.
                logger.exception(
                    "Primary Whisper model failed to load; falling back to small.en"
                )
                _whisper_model = WhisperModel(
                    "small.en",
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=WHISPER_CPU_THREADS,
                    num_workers=1,
                    download_root=str(whisper_dir),
                )
        return _whisper_model


def normalize_transcript_text(value: str) -> str:
    text_value = re.sub(r"\s+", " ", str(value or "")).strip()
    text_value = re.sub(r"\s+([,.;:!?%،؛؟])", r"\1", text_value)
    text_value = re.sub(r"([\(\[]) +", r"\1", text_value)
    return text_value


def _transcript_token_key(token: str) -> str:
    return re.sub(r"[^\w']+", "", str(token or "").lower(), flags=re.UNICODE)


def _remove_transcript_prefix_overlap(
    previous_text: str,
    current_text: str,
    *,
    min_overlap_words: int = 2,
    max_overlap_words: int = 48,
) -> str:
    """Remove rolling-caption/Whisper overlap using suffix-prefix word matching."""
    previous_tokens = normalize_transcript_text(previous_text).split()
    current_tokens = normalize_transcript_text(current_text).split()
    if not previous_tokens or not current_tokens:
        return normalize_transcript_text(current_text)

    previous_keys = [_transcript_token_key(token) for token in previous_tokens]
    current_keys = [_transcript_token_key(token) for token in current_tokens]
    maximum = min(len(previous_keys), len(current_keys), max_overlap_words)

    for size in range(maximum, min_overlap_words - 1, -1):
        left = previous_keys[-size:]
        right = current_keys[:size]
        if left and left == right and all(left):
            return normalize_transcript_text(" ".join(current_tokens[size:]))
    return normalize_transcript_text(current_text)


def _collapse_transcript_stutter_artifacts(text_value: str) -> str:
    """Collapse obvious ASR glitches while preserving normal two-word emphasis."""
    tokens = normalize_transcript_text(text_value).split()
    if not tokens:
        return ""
    output: List[str] = []
    index = 0
    while index < len(tokens):
        key = _transcript_token_key(tokens[index])
        run_end = index + 1
        while (
            run_end < len(tokens)
            and key
            and _transcript_token_key(tokens[run_end]) == key
        ):
            run_end += 1
        run_length = run_end - index
        # Three or more identical adjacent words are usually an ASR/caption loop.
        if run_length >= 3:
            output.append(tokens[index])
        else:
            output.extend(tokens[index:run_end])
        index = run_end
    return normalize_transcript_text(" ".join(output))


def dedupe_transcript_rows(
    rows: List[Tuple[float, float, str]],
) -> List[Tuple[float, float, str]]:
    """Remove repeated segment prefixes without deleting intentional later repeats."""
    output: List[Tuple[float, float, str]] = []
    recent_texts: List[str] = []

    for start, end, value in rows:
        value = _collapse_transcript_stutter_artifacts(value)
        if not value:
            continue
        start = max(0.0, float(start))
        end = max(start + 0.04, float(end))

        context = normalize_transcript_text(" ".join(recent_texts[-2:]))
        if output and start <= output[-1][1] + 1.25:
            value = _remove_transcript_prefix_overlap(
                context,
                value,
                min_overlap_words=2,
            )
        if not value:
            continue

        if output:
            previous_value = output[-1][2]
            if (
                _transcript_token_key(previous_value)
                == _transcript_token_key(value)
                and start <= output[-1][1] + 0.75
            ):
                output[-1] = (
                    output[-1][0],
                    max(output[-1][1], end),
                    previous_value,
                )
                continue

        output.append((start, end, value))
        recent_texts.append(value)

    return output


def append_word_token(current: str, token: str) -> str:
    token = normalize_transcript_text(token)
    if not token:
        return current
    if not current:
        return token
    if token[0] in ".,!?;:%)]}…،؛؟" or current[-1] in "([{":
        return current + token
    return current + " " + token


def sentence_finished(text_value: str) -> bool:
    return bool(re.search(r"[.!?…؟]['\")\]]?$", text_value.strip()))


def build_timestamped_paragraphs(
    words: List[Tuple[float, float, str]],
) -> List[TranscriptParagraph]:
    """
    Build variable-length transcript paragraphs from real cue timestamps.

    Paragraphs are not cut at fixed time intervals. A paragraph closes when
    there is a meaningful pause, or when a complete sentence reaches a useful
    thought length. A hard safety limit prevents one paragraph from becoming
    excessively long.
    """
    if not words:
        return []

    paragraphs: List[TranscriptParagraph] = []
    current_text = ""
    current_start = 0.0
    current_end = 0.0
    previous_end: Optional[float] = None

    def flush() -> None:
        nonlocal current_text, current_start, current_end
        cleaned = normalize_transcript_text(current_text)
        if cleaned:
            paragraphs.append(
                TranscriptParagraph(
                    start=max(0.0, current_start),
                    end=max(current_start, current_end),
                    text=cleaned,
                )
            )
        current_text = ""
        current_start = 0.0
        current_end = 0.0

    for start_time, end_time, token in words:
        token = normalize_transcript_text(token)
        if not token:
            continue

        start_time = max(0.0, float(start_time))
        end_time = max(start_time, float(end_time))
        pause = (
            max(0.0, start_time - previous_end)
            if previous_end is not None
            else 0.0
        )

        if not current_text:
            current_start = start_time
        else:
            duration = max(0.0, current_end - current_start)
            projected = append_word_token(current_text, token)
            complete_sentence = sentence_finished(current_text)

            # Strong spoken pause usually means the speaker completed a thought.
            meaningful_pause_break = (
                pause >= TRANSCRIPT_PAUSE_BREAK_SECONDS
                and duration >= TRANSCRIPT_PARAGRAPH_MIN_SECONDS
                and len(current_text) >= 100
            )

            # With no clear pause, keep sentences together until the paragraph
            # has enough context to feel like one complete idea.
            natural_thought_break = (
                complete_sentence
                and duration >= TRANSCRIPT_PARAGRAPH_TARGET_SECONDS
                and len(current_text) >= 260
            )

            # Prefer to end a long paragraph on punctuation.
            long_sentence_break = (
                complete_sentence
                and duration >= TRANSCRIPT_PARAGRAPH_MAX_SECONDS
            )

            # Emergency limits only; these avoid unbounded paragraphs when
            # punctuation is missing from automatic captions.
            hard_break = (
                duration >= TRANSCRIPT_PARAGRAPH_MAX_SECONDS + 35.0
                or len(projected) > TRANSCRIPT_PARAGRAPH_MAX_CHARS
            )

            if (
                meaningful_pause_break
                or natural_thought_break
                or long_sentence_break
                or hard_break
            ):
                flush()
                current_start = start_time

        current_text = append_word_token(current_text, token)
        current_end = end_time
        previous_end = end_time

    flush()

    # Merge fragments that are too short to stand on their own and are
    # temporally continuous with the previous paragraph.
    merged: List[TranscriptParagraph] = []
    for paragraph in paragraphs:
        if (
            merged
            and (
                len(paragraph.text) < 90
                or (paragraph.end - paragraph.start) < 7.0
            )
            and paragraph.start - merged[-1].end < 1.5
            and (
                paragraph.end - merged[-1].start
                <= TRANSCRIPT_PARAGRAPH_MAX_SECONDS + 35.0
            )
            and len(merged[-1].text) + len(paragraph.text)
            <= TRANSCRIPT_PARAGRAPH_MAX_CHARS
        ):
            merged[-1].text = normalize_transcript_text(
                f"{merged[-1].text} {paragraph.text}"
            )
            merged[-1].end = paragraph.end
        else:
            merged.append(paragraph)

    return merged


def vtt_time_to_seconds(value: str) -> float:
    pieces = value.strip().replace(",", ".").split(":")
    try:
        if len(pieces) == 3:
            hours, minutes, seconds = pieces
        elif len(pieces) == 2:
            hours = "0"
            minutes, seconds = pieces
        else:
            return 0.0
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (TypeError, ValueError):
        return 0.0


def clean_vtt_text(value: str) -> str:
    value = re.sub(r"<\d\d:\d\d:[^>]+>", " ", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return normalize_transcript_text(value)


def parse_youtube_vtt(path: Path) -> List[TranscriptParagraph]:
    content = path.read_text(encoding="utf-8", errors="ignore")
    lines = content.replace("\r\n", "\n").split("\n")
    cues: List[Tuple[float, float, str]] = []
    index = 0
    previous_text = ""

    while index < len(lines):
        line = lines[index].strip()
        if "-->" not in line:
            index += 1
            continue
        left, right = line.split("-->", 1)
        start = vtt_time_to_seconds(left.strip().split()[0])
        end = vtt_time_to_seconds(right.strip().split()[0])
        index += 1
        text_lines: List[str] = []
        while index < len(lines) and lines[index].strip():
            text_lines.append(lines[index].strip())
            index += 1
        cue_text = clean_vtt_text(" ".join(text_lines))
        if not cue_text:
            continue

        # YouTube rolling captions often overlap by only the last few words,
        # not by the full previous cue. Remove the longest suffix-prefix overlap.
        if cue_text == previous_text:
            continue
        delta = _remove_transcript_prefix_overlap(
            previous_text,
            cue_text,
            min_overlap_words=1,
        ) if previous_text else cue_text
        delta = _collapse_transcript_stutter_artifacts(delta)
        if not delta:
            previous_text = cue_text
            continue
        cues.append((start, max(start + 0.3, end), delta))
        previous_text = cue_text

    return build_timestamped_paragraphs(dedupe_transcript_rows(cues))


def _pick_english_caption_key(
    source: Dict[str, Any],
) -> Optional[str]:
    if not isinstance(source, dict):
        return None
    for preferred in ("en", "en-US", "en-GB", "en-orig"):
        if preferred in source:
            return preferred
    for key in source.keys():
        if str(key).lower().startswith("en"):
            return str(key)
    return None


def choose_english_caption_key(info: Dict[str, Any]) -> Optional[str]:
    """
    Prefer creator-provided/manual English subtitles for the best combination
    of accuracy and speed. If they do not exist, use YouTube automatic English
    captions as a fast fallback before downloading audio for Whisper.
    """
    manual = info.get("subtitles") or {}
    key = _pick_english_caption_key(manual)
    if key:
        return key

    if TRANSCRIPT_ALLOW_AUTO_CAPTIONS:
        automatic = info.get("automatic_captions") or {}
        return _pick_english_caption_key(automatic)

    return None


async def try_fast_youtube_captions(
    job: Dict[str, Any],
    cancel_event: threading.Event,
) -> Tuple[Optional[List[TranscriptParagraph]], Optional[Path]]:
    if not TRANSCRIPT_CAPTIONS_FIRST:
        return None, None

    async with cancellable_slot(EXTRACT_SEMAPHORE, cancel_event):
        ensure_not_cancelled(cancel_event)
        stem = f"captions_{job['job_id']}_{uuid.uuid4().hex[:8]}"
        try:
            info = await media_transport.extract_info(
                str(job["url"]),
                cancel_event=cancel_event,
            )
            language = choose_english_caption_key(info)
            if not language:
                return None, None
            caption_path = await media_transport.download_subtitles(
                str(job["url"]),
                stem,
                language,
                cancel_event=cancel_event,
            )
            ensure_not_cancelled(cancel_event)
            paragraphs = parse_youtube_vtt(caption_path)
            return (paragraphs or None), caption_path
        except TransportCancelled as exc:
            raise JobCancelled(str(exc)) from exc
        except JobCancelled:
            raise
        except Exception as exc:
            logger.info("Fast captions unavailable through central transport: %s", exc)
            return None, None



def build_segment_paragraphs(
    rows: List[Tuple[float, float, str]],
) -> List[TranscriptParagraph]:
    """
    Merge Whisper segments into natural variable-length paragraphs.

    Uses real Whisper segment timestamps. It favors complete thoughts,
    punctuation, and meaningful pauses rather than arbitrary fixed intervals.
    """
    if not rows:
        return []

    paragraphs: List[TranscriptParagraph] = []
    current_start = 0.0
    current_end = 0.0
    current_parts: List[str] = []
    current_chars = 0
    previous_end: Optional[float] = None

    def flush() -> None:
        nonlocal current_start, current_end, current_parts, current_chars
        value = normalize_transcript_text(" ".join(current_parts))
        if value:
            paragraphs.append(
                TranscriptParagraph(
                    start=max(0.0, current_start),
                    end=max(current_start, current_end),
                    text=value,
                )
            )
        current_start = 0.0
        current_end = 0.0
        current_parts = []
        current_chars = 0

    for start_time, end_time, value in rows:
        value = normalize_transcript_text(value)
        if not value:
            continue

        start_time = max(0.0, float(start_time))
        end_time = max(start_time, float(end_time))
        pause = (
            max(0.0, start_time - previous_end)
            if previous_end is not None
            else 0.0
        )

        if not current_parts:
            current_start = start_time
        else:
            duration = max(0.0, current_end - current_start)
            complete_sentence = sentence_finished(current_parts[-1])

            meaningful_pause_break = (
                pause >= TRANSCRIPT_PAUSE_BREAK_SECONDS
                and duration >= TRANSCRIPT_PARAGRAPH_MIN_SECONDS
                and current_chars >= 100
            )
            natural_thought_break = (
                complete_sentence
                and duration >= TRANSCRIPT_PARAGRAPH_TARGET_SECONDS
                and current_chars >= 260
            )
            long_sentence_break = (
                complete_sentence
                and duration >= TRANSCRIPT_PARAGRAPH_MAX_SECONDS
            )
            hard_break = (
                duration >= TRANSCRIPT_PARAGRAPH_MAX_SECONDS + 35.0
                or current_chars + len(value) + 1
                > TRANSCRIPT_PARAGRAPH_MAX_CHARS
            )

            if (
                meaningful_pause_break
                or natural_thought_break
                or long_sentence_break
                or hard_break
            ):
                flush()
                current_start = start_time

        current_parts.append(value)
        current_chars += len(value) + 1
        current_end = end_time
        previous_end = end_time

    flush()

    merged: List[TranscriptParagraph] = []
    for paragraph in paragraphs:
        if (
            merged
            and (
                len(paragraph.text) < 90
                or (paragraph.end - paragraph.start) < 7.0
            )
            and paragraph.start - merged[-1].end < 1.5
            and (
                paragraph.end - merged[-1].start
                <= TRANSCRIPT_PARAGRAPH_MAX_SECONDS + 35.0
            )
            and len(merged[-1].text) + len(paragraph.text)
            <= TRANSCRIPT_PARAGRAPH_MAX_CHARS
        ):
            merged[-1].text = normalize_transcript_text(
                f"{merged[-1].text} {paragraph.text}"
            )
            merged[-1].end = paragraph.end
        else:
            merged.append(paragraph)

    return merged


def run_transcription(
    audio_path: Path,
    cancel_event: threading.Event,
) -> Tuple[List[TranscriptParagraph], str]:
    """
    Accurate local English transcription.

    Uses real segment timestamps, VAD to skip silence, and settings recommended
    for distil-large-v3. Word timestamps are intentionally disabled because
    segment timestamps are much faster and are sufficient for natural
    paragraph/SRT generation.
    """
    model = get_whisper_model()

    transcribe_kwargs: Dict[str, Any] = {
        "task": "transcribe",
        "language": WHISPER_LANGUAGE,
        "beam_size": 5,
        "temperature": 0.0,
        "vad_filter": True,
        "vad_parameters": {
            "min_silence_duration_ms": 500,
            "speech_pad_ms": 180,
        },
        "word_timestamps": False,
        "compression_ratio_threshold": 2.4,
        "log_prob_threshold": -1.0,
        "no_speech_threshold": 0.5,
        "initial_prompt": (
            "Transcribe exactly what is spoken. Preserve names, acronyms, "
            "numbers and punctuation. Do not paraphrase or summarize."
        ),
    }

    # Distil-Whisper is designed to run with previous-text conditioning off.
    if "distil" in WHISPER_MODEL_NAME.lower():
        transcribe_kwargs["condition_on_previous_text"] = False
    else:
        transcribe_kwargs["condition_on_previous_text"] = True

    segments, info = model.transcribe(
        str(audio_path),
        **transcribe_kwargs,
    )

    rows: List[Tuple[float, float, str]] = []
    for segment in segments:
        ensure_not_cancelled(cancel_event)
        value = normalize_transcript_text(str(segment.text or ""))
        if not value:
            continue
        start = float(segment.start or 0)
        end = float(segment.end or start)
        rows.append((start, max(start + 0.25, end), value))

    rows = dedupe_transcript_rows(rows)
    paragraphs = build_segment_paragraphs(rows)
    detected_language = str(getattr(info, "language", "") or WHISPER_LANGUAGE)
    if not paragraphs:
        raise RuntimeError("No speech was detected in this video")
    return paragraphs, detected_language


def run_editor_transcription(
    audio_path: Path,
    cancel_event: threading.Event,
) -> Tuple[
    List[TranscriptParagraph],
    str,
    List[ClipperWordTiming],
]:
    """
    One-pass accurate transcription for AI Video Editor.

    V7.1 always normalizes the source's first audio stream to mono 16 kHz PCM WAV
    before faster-whisper sees it.  Some playable MP4/WebM files cannot be decoded
    reliably by PyAV/faster-whisper and surface only as ``IndexError: tuple index
    out of range``.  FFmpeg normalization makes the decoder input deterministic.
    """
    ensure_not_cancelled(cancel_event)
    temp_audio = DOWNLOAD_DIR / f"editor_audio_{uuid.uuid4().hex[:10]}.wav"
    temp_audio.unlink(missing_ok=True)
    try:
        try:
            run_process(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                    "-i", str(audio_path),
                    "-map", "0:a:0",
                    "-vn", "-sn", "-dn",
                    "-ac", "1", "-ar", "16000",
                    "-af", "aresample=async=1:first_pts=0",
                    "-c:a", "pcm_s16le",
                    str(temp_audio),
                ],
                cancel_event=cancel_event,
                error_prefix="AI Video Editor audio normalization failed",
                timeout_seconds=max(120, min(900, int((probe_media(audio_path).get("duration") or 60) * 2 + 90))),
            )
        except JobCancelled:
            raise
        except Exception as first_exc:
            # A second, simpler decode path covers older FFmpeg builds that do not
            # accept the async resampler syntax above.
            temp_audio.unlink(missing_ok=True)
            try:
                run_process(
                    [
                        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                        "-i", str(audio_path),
                        "-map", "0:a:0",
                        "-vn", "-sn", "-dn",
                        "-ac", "1", "-ar", "16000",
                        "-c:a", "pcm_s16le",
                        str(temp_audio),
                    ],
                    cancel_event=cancel_event,
                    error_prefix="AI Video Editor audio decode fallback failed",
                    timeout_seconds=900,
                )
            except Exception as second_exc:
                raise RuntimeError(
                    "AI Video Editor could not decode the source audio for transcription: "
                    f"primary={first_exc}; fallback={second_exc}"
                ) from second_exc

        if not temp_audio.exists() or temp_audio.stat().st_size < 4096:
            raise RuntimeError("AI Video Editor audio normalization produced an empty WAV")

        model = get_whisper_model()
        kwargs: Dict[str, Any] = {
            "task": "transcribe",
            "language": WHISPER_LANGUAGE,
            "beam_size": 5,
            "temperature": 0.0,
            "vad_filter": True,
            "vad_parameters": {
                "min_silence_duration_ms": 300,
                "speech_pad_ms": 120,
            },
            "word_timestamps": True,
            "compression_ratio_threshold": 2.4,
            "log_prob_threshold": -1.0,
            "no_speech_threshold": 0.5,
            "initial_prompt": (
                "Transcribe exactly what is spoken. Preserve names, acronyms, "
                "numbers and punctuation. Do not paraphrase or summarize."
            ),
        }
        kwargs["condition_on_previous_text"] = "distil" not in WHISPER_MODEL_NAME.lower()

        def _transcribe_materialized(call_kwargs: Dict[str, Any]) -> Tuple[List[Any], Any]:
            result = model.transcribe(str(temp_audio), **call_kwargs)
            if not isinstance(result, tuple) or len(result) < 2:
                raise RuntimeError("Whisper returned an invalid transcription result")
            segment_iter, info_value = result[0], result[1]
            # faster-whisper is lazy: decoding/inference exceptions can be raised only
            # when the segment generator is consumed, not by model.transcribe().
            # Materialize it here so IndexError is caught by the retry below.
            return list(segment_iter), info_value

        try:
            segments, info = _transcribe_materialized(kwargs)
        except IndexError as exc:
            logger.warning(
                "Whisper tuple-index failure while consuming segments; "
                "retrying normalized WAV without VAD: %s", exc
            )
            retry_kwargs = dict(kwargs)
            retry_kwargs["vad_filter"] = False
            retry_kwargs.pop("vad_parameters", None)
            try:
                segments, info = _transcribe_materialized(retry_kwargs)
            except IndexError as retry_exc:
                raise RuntimeError(
                    "AI Video Editor transcription decoder failed twice after PCM WAV "
                    "normalization (tuple index error)."
                ) from retry_exc

        paragraph_rows: List[Tuple[float, float, str]] = []
        word_rows: List[ClipperWordTiming] = []
        for segment in segments:
            ensure_not_cancelled(cancel_event)
            value = normalize_transcript_text(str(getattr(segment, "text", "") or ""))
            start_value = float(getattr(segment, "start", 0.0) or 0.0)
            end_value = float(getattr(segment, "end", start_value) or start_value)
            if value:
                paragraph_rows.append((start_value, max(start_value + 0.25, end_value), value))
            for word in getattr(segment, "words", None) or []:
                token = normalize_transcript_text(str(getattr(word, "word", "") or ""))
                if not token:
                    continue
                word_start = max(0.0, float(getattr(word, "start", 0.0) or 0.0))
                word_end = max(
                    word_start + 0.04,
                    float(getattr(word, "end", word_start + 0.04) or word_start + 0.04),
                )
                word_rows.append(ClipperWordTiming(start=word_start, end=word_end, text=token))

        paragraph_rows = dedupe_transcript_rows(paragraph_rows)
        paragraphs = build_segment_paragraphs(paragraph_rows)
        word_rows = _clean_clipper_word_timings(word_rows)
        detected_language = str(getattr(info, "language", "") or WHISPER_LANGUAGE)
        if not paragraphs:
            raise RuntimeError("No speech was detected in this video")
        if not word_rows:
            word_rows = _approximate_word_timings_from_paragraphs(
                paragraphs,
                0.0,
                max(paragraph.end for paragraph in paragraphs),
            )
        return paragraphs, detected_language, word_rows
    finally:
        temp_audio.unlink(missing_ok=True)


def split_translation_text(
    text_value: str,
    max_chars: int = TRANSLATION_CHUNK_CHARS,
) -> List[str]:
    """
    Split English transcript into sentence-level translation units.

    Each normal sentence is translated independently so Google Translate
    cannot reorder separate sentences inside one large request. Extremely
    long/run-on sentences are split by words into smaller ordered chunks.
    """
    text_value = normalize_transcript_text(text_value)
    if not text_value:
        return []

    sentences = [
        item.strip()
        for item in re.split(r"(?<=[.!?…])\s+", text_value)
        if item.strip()
    ]
    if not sentences:
        sentences = [text_value]

    chunks: List[str] = []

    for sentence in sentences:
        if len(sentence) <= max_chars:
            chunks.append(sentence)
            continue

        words = sentence.split()
        current: List[str] = []
        current_length = 0

        for word in words:
            projected = current_length + len(word) + (1 if current else 0)
            if current and projected > max_chars:
                chunks.append(" ".join(current))
                current = []
                current_length = 0

            # A single pathological token longer than max_chars.
            if not current and len(word) > max_chars:
                for index in range(0, len(word), max_chars):
                    part = word[index:index + max_chars].strip()
                    if part:
                        chunks.append(part)
                continue

            current.append(word)
            current_length += len(word) + (1 if current_length else 0)

        if current:
            chunks.append(" ".join(current))

    return [normalize_transcript_text(chunk) for chunk in chunks if chunk.strip()]


def _urdu_translation_looks_valid(source_text: str, translated_text: str) -> bool:
    source_text = normalize_transcript_text(source_text)
    translated_text = normalize_transcript_text(translated_text)
    if not translated_text:
        return False
    if len(source_text) >= 40:
        ratio = len(translated_text) / max(1, len(source_text))
        if ratio < 0.22 or ratio > 5.0:
            return False
        urdu_chars = len(re.findall(r"[\u0600-\u06FF]", translated_text))
        if urdu_chars < max(4, int(len(translated_text) * 0.10)):
            return False
    return True


def _translate_text_to_urdu_with_llm(
    text_value: str,
    previous_context: str = "",
    next_context: str = "",
) -> str:
    # External LLM translation is disabled in local-editor mode. The existing
    # Google translation fallback below remains available.
    return ""


def _translate_text_to_urdu_with_retries(
    text_value: str,
) -> str:
    from deep_translator import GoogleTranslator

    text_value = normalize_transcript_text(text_value)
    if not text_value:
        return ""

    # Protect acronyms/technical identifiers before sending text to Google
    # Translate. Examples matched: MI5, MI6, COVID, NHS, GMC, GCHQ, USAID.
    acronym_pattern = re.compile(r"\b[A-Z]{2,6}[0-9]{0,2}\b")
    acronym_map: Dict[str, str] = {}

    def protect_acronym(match: re.Match[str]) -> str:
        original = match.group(0)
        token = f"ZXQACR{len(acronym_map):04d}QXZ"
        acronym_map[token] = original
        return token

    protected_text = acronym_pattern.sub(protect_acronym, text_value)
    chunks = split_translation_text(protected_text)
    if not chunks:
        return ""

    translated_chunks: List[str] = []

    for chunk in chunks:
        last_error: Optional[Exception] = None
        translated_value = ""

        for attempt in range(1, TRANSLATION_RETRIES + 1):
            try:
                translator = GoogleTranslator(source="en", target="ur")
                translated_value = normalize_transcript_text(
                    translator.translate(chunk)
                )
                if translated_value:
                    break
            except Exception as exc:
                last_error = exc
                if attempt < TRANSLATION_RETRIES:
                    time.sleep(min(4.0, 0.8 * (2 ** (attempt - 1))))

        if not translated_value:
            if last_error:
                raise RuntimeError(
                    f"Urdu translation failed after retries: {last_error}"
                ) from last_error
            raise RuntimeError(
                "Urdu translation service returned an empty result"
            )

        # Restore every protected acronym in this translated unit.
        for token, original in acronym_map.items():
            translated_value = translated_value.replace(token, original)

        translated_chunks.append(translated_value)

    result = normalize_transcript_text(" ".join(translated_chunks))

    # Final safety pass in case a token belongs to a later sentence/chunk.
    for token, original in acronym_map.items():
        result = result.replace(token, original)

    return result


def _translate_text_to_urdu_google_context(
    text_value: str,
    previous_context: str = "",
    next_context: str = "",
) -> str:
    """Give Google Translate neighboring podcast context, then extract TARGET."""
    if not previous_context and not next_context:
        return _translate_text_to_urdu_with_retries(text_value)

    start_marker = "ZXQTARGETSTART987"
    end_marker = "ZXQTARGETEND987"
    combined = (
        f"{previous_context[-900:]}\n"
        f"{start_marker}\n{text_value}\n{end_marker}\n"
        f"{next_context[:900]}"
    )
    try:
        translated = _translate_text_to_urdu_with_retries(combined)
        start_index = translated.find(start_marker)
        end_index = translated.find(end_marker)
        if start_index >= 0 and end_index > start_index:
            current = translated[
                start_index + len(start_marker):end_index
            ].strip(" \n:-")
            current = normalize_transcript_text(current)
            if _urdu_translation_looks_valid(text_value, current):
                return current
    except Exception as exc:
        logger.debug("Google context translation fallback: %s", exc)

    return _translate_text_to_urdu_with_retries(text_value)


def translate_paragraphs_to_urdu(
    paragraphs: List[TranscriptParagraph],
    cancel_event: threading.Event,
) -> List[TranscriptParagraph]:
    """
    Context-aware Urdu translation. An OpenAI-compatible LLM is preferred when
    configured; GoogleTranslator remains the automatic fallback. Timestamps and
    English paragraph order are preserved exactly.
    """
    if not paragraphs:
        return []

    def translate_one(index: int) -> str:
        paragraph = paragraphs[index]
        previous_context = paragraphs[index - 1].text if index > 0 else ""
        next_context = (
            paragraphs[index + 1].text if index + 1 < len(paragraphs) else ""
        )
        if TRANSLATION_USE_LLM:
            try:
                result = _translate_text_to_urdu_with_llm(
                    paragraph.text,
                    previous_context,
                    next_context,
                )
                if result:
                    return result
            except Exception as exc:
                logger.warning(
                    "Context-aware Urdu LLM translation failed for block %d: %s",
                    index + 1,
                    exc,
                )

        result = _translate_text_to_urdu_google_context(
            paragraph.text,
            previous_context,
            next_context,
        )
        if not _urdu_translation_looks_valid(paragraph.text, result):
            raise RuntimeError(
                f"Urdu translation validation failed for block {index + 1}"
            )
        return result

    results: Dict[int, str] = {}
    max_workers = min(TRANSLATION_WORKERS, len(paragraphs))

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="urdu-translate",
    ) as executor:
        futures = {
            executor.submit(translate_one, index): index
            for index in range(len(paragraphs))
        }

        for future in concurrent.futures.as_completed(futures):
            ensure_not_cancelled(cancel_event)
            index = futures[future]
            results[index] = future.result()

    output: List[TranscriptParagraph] = []
    for index, paragraph in enumerate(paragraphs):
        ensure_not_cancelled(cancel_event)
        translated_text = results.get(index, "")
        if not translated_text:
            raise RuntimeError(
                f"Urdu translation was missing for block {index + 1}"
            )
        output.append(
            TranscriptParagraph(
                start=paragraph.start,
                end=paragraph.end,
                text=translated_text,
            )
        )

    return output


def timestamp_range(start: float, end: float) -> str:
    return f"{format_time(start)} - {format_time(end)}"


def srt_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(float(seconds) * 1000)))
    hours, remaining = divmod(total_ms, 3_600_000)
    minutes, remaining = divmod(remaining, 60_000)
    secs, milliseconds = divmod(remaining, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def split_paragraph_for_srt(
    paragraph: TranscriptParagraph,
    max_chars: int = 190,
) -> List[TranscriptParagraph]:
    text_value = paragraph.text.strip()
    if len(text_value) <= max_chars:
        return [paragraph]
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[.!?…؟])\s+", text_value)
        if item.strip()
    ]
    if len(sentences) <= 1:
        sentences = [
            text_value[index:index + max_chars].strip()
            for index in range(0, len(text_value), max_chars)
        ]
    groups: List[str] = []
    current = ""
    for sentence in sentences:
        candidate = sentence if not current else f"{current} {sentence}"
        if current and len(candidate) > max_chars:
            groups.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        groups.append(current)
    total_length = max(1, sum(len(item) for item in groups))
    duration = max(0.5, paragraph.end - paragraph.start)
    output: List[TranscriptParagraph] = []
    cursor = paragraph.start
    for index, item in enumerate(groups):
        end = paragraph.end if index == len(groups) - 1 else min(
            paragraph.end,
            cursor + max(0.7, duration * len(item) / total_length),
        )
        output.append(
            TranscriptParagraph(
                start=cursor,
                end=max(cursor + 0.3, end),
                text=item,
            )
        )
        cursor = output[-1].end
    return output


def find_urdu_font() -> Path:
    candidates = [
        Path("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoNaskhArabic-Regular.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"),
        DEJAVU_FONT,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError("No Urdu-compatible PDF font was found")


def language_name(language_code: str) -> str:
    return {
        "en": "English",
        "ur": "Urdu",
        "bi": "English + Urdu",
    }.get(language_code, "Transcript")


def create_transcript_txt(
    title: str,
    language_code: str,
    detected_language: str,
    english: List[TranscriptParagraph],
    urdu: Optional[List[TranscriptParagraph]],
) -> Path:
    name = language_name(language_code)
    path = DOWNLOAD_DIR / (
        f"{safe_filename(title)}_{safe_filename(name)}_Transcript_"
        f"{uuid.uuid4().hex[:6]}.txt"
    )
    header = (
        f"{title}\n"
        f"Transcript language: {name}\n"
        f"Detected audio language: {detected_language}\n"
        f"{owner_footer()}\n"
        f"{'=' * 78}\n\n"
    )
    blocks: List[str] = []
    for index, paragraph in enumerate(english):
        stamp = f"[{timestamp_range(paragraph.start, paragraph.end)}]"
        if language_code == "en":
            blocks.append(f"{stamp}\n{paragraph.text}")
        elif language_code == "ur":
            value = urdu[index].text if urdu and index < len(urdu) else ""
            blocks.append(f"{stamp}\n\u200f{value}")
        else:
            value = urdu[index].text if urdu and index < len(urdu) else ""
            blocks.append(
                f"{stamp}\nEnglish:\n{paragraph.text}\n\nUrdu:\n\u200f{value}"
            )
    path.write_text(header + "\n\n".join(blocks) + "\n", encoding="utf-8-sig")
    return path


def create_transcript_srt(
    title: str,
    language_code: str,
    english: List[TranscriptParagraph],
    urdu: Optional[List[TranscriptParagraph]],
) -> Path:
    name = language_name(language_code)
    path = DOWNLOAD_DIR / (
        f"{safe_filename(title)}_{safe_filename(name)}_Subtitles_"
        f"{uuid.uuid4().hex[:6]}.srt"
    )
    cues: List[Tuple[TranscriptParagraph, Optional[str]]] = []
    for index, paragraph in enumerate(english):
        urdu_text = urdu[index].text if urdu and index < len(urdu) else None
        if language_code == "bi":
            cues.append((paragraph, urdu_text))
        else:
            source = paragraph if language_code == "en" else TranscriptParagraph(
                paragraph.start, paragraph.end, urdu_text or ""
            )
            for split in split_paragraph_for_srt(source):
                cues.append((split, None))
    blocks: List[str] = []
    for number, (paragraph, urdu_text) in enumerate(cues, 1):
        body = paragraph.text
        if language_code == "bi":
            body = f"{paragraph.text}\n{urdu_text or ''}"
        blocks.append(
            f"{number}\n{srt_timestamp(paragraph.start)} --> "
            f"{srt_timestamp(paragraph.end)}\n{body}"
        )
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8-sig")
    return path


def shape_urdu_text(value: str) -> str:
    import arabic_reshaper
    from bidi.algorithm import get_display
    return get_display(arabic_reshaper.reshape(value))


def create_transcript_pdf(
    title: str,
    language_code: str,
    detected_language: str,
    english: List[TranscriptParagraph],
    urdu: Optional[List[TranscriptParagraph]],
) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer

    if not DEJAVU_FONT.exists():
        raise RuntimeError(f"PDF font file not found: {DEJAVU_FONT}")
    urdu_font_path = find_urdu_font()
    english_font = "TranscriptEnglish"
    urdu_font = "TranscriptUrdu"
    if english_font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(english_font, str(DEJAVU_FONT)))
    if urdu_font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(urdu_font, str(urdu_font_path)))

    name = language_name(language_code)
    path = DOWNLOAD_DIR / (
        f"{safe_filename(title)}_{safe_filename(name)}_Transcript_"
        f"{uuid.uuid4().hex[:6]}.pdf"
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "FinalTranscriptTitle", parent=styles["Title"], fontName=english_font,
        fontSize=17, leading=22, textColor=colors.HexColor("#172033"), spaceAfter=8,
    )
    meta_style = ParagraphStyle(
        "FinalTranscriptMeta", parent=styles["Normal"], fontName=english_font,
        fontSize=8.8, leading=12, textColor=colors.HexColor("#5F6B7A"), spaceAfter=3,
    )
    timestamp_style = ParagraphStyle(
        "FinalTranscriptTimestamp", parent=styles["Normal"], fontName=english_font,
        fontSize=8.5, leading=11, textColor=colors.HexColor("#2463EB"),
        spaceBefore=6, spaceAfter=4,
    )
    english_style = ParagraphStyle(
        "FinalEnglishBody", parent=styles["Normal"], fontName=english_font,
        fontSize=10.5, leading=16, alignment=TA_LEFT,
        textColor=colors.HexColor("#20242B"), spaceAfter=7,
    )
    urdu_style = ParagraphStyle(
        "FinalUrduBody", parent=styles["Normal"], fontName=urdu_font,
        fontSize=11, leading=19, alignment=TA_RIGHT, wordWrap="RTL",
        textColor=colors.HexColor("#20242B"), spaceAfter=9,
    )
    label_style = ParagraphStyle(
        "FinalLabel", parent=styles["Normal"], fontName=english_font,
        fontSize=8, leading=10, textColor=colors.HexColor("#6B7280"), spaceAfter=2,
    )
    story = [
        Paragraph(html.escape(title), title_style),
        Paragraph(html.escape(f"Transcript language: {name}"), meta_style),
        Paragraph(html.escape(f"Detected audio language: {detected_language}"), meta_style),
        Paragraph(html.escape(owner_footer()), meta_style),
        Spacer(1, 5 * mm),
    ]
    for index, paragraph in enumerate(english):
        items = [
            Paragraph(html.escape(timestamp_range(paragraph.start, paragraph.end)), timestamp_style)
        ]
        if language_code in {"en", "bi"}:
            if language_code == "bi":
                items.append(Paragraph("English", label_style))
            items.append(Paragraph(html.escape(paragraph.text), english_style))
        if language_code in {"ur", "bi"}:
            value = urdu[index].text if urdu and index < len(urdu) else ""
            if language_code == "bi":
                items.append(Paragraph("Urdu", label_style))
            items.append(Paragraph(html.escape(shape_urdu_text(value)), urdu_style))
        story.append(KeepTogether(items))

    def footer(canvas, document) -> None:
        canvas.saveState()
        canvas.setFont(english_font, 7.5)
        canvas.setFillColor(colors.HexColor("#7B8491"))
        canvas.drawString(16 * mm, 9 * mm, owner_footer())
        canvas.drawRightString(A4[0] - 16 * mm, 9 * mm, f"Page {document.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(
        str(path), pagesize=A4, leftMargin=17 * mm, rightMargin=17 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"{title} - {name} Transcript", author=OWNER_DISPLAY_NAME,
    )
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return path


async def create_transcript(
    job: Dict[str, Any],
    language_code: str,
    output_format: str,
    progress_cb: ProgressCallback,
    cancel_event: threading.Event,
) -> Tuple[Optional[Path], Optional[str], List[Path]]:
    """
    Create English, Urdu, or aligned bilingual transcripts using the local transcript engine.

    Pipeline:
    1) Prefer creator-provided English captions for maximum speed.
    2) Otherwise download audio and transcribe locally with Faster-Whisper.
    3) Translate the final English paragraphs to Urdu with GoogleTranslator.
    4) Preserve the exact English timestamps for Urdu/bilingual output.
    """
    files_to_clean: List[Path] = []

    async with cancellable_slot(TRANSCRIPT_SEMAPHORE, cancel_event):
        try:
            cached = get_cached_transcript(job)
            if cached:
                english, detected_language = cached
                await progress_cb(
                    "⚡ Using cached English transcript. Preparing your file..."
                )
            else:
                build_lock = get_transcript_build_lock(job)
                async with build_lock:
                    cached = get_cached_transcript(job)
                    if cached:
                        english, detected_language = cached
                        await progress_cb(
                            "⚡ Transcript was already prepared. Using cached result..."
                        )
                    else:
                        local_path_value = str(job.get("local_path") or "")
                        if local_path_value:
                            local_path = Path(local_path_value)
                            if not local_path.exists():
                                return None, "The timestamp clip expired.", files_to_clean
                            await progress_cb(
                                f"🎙 Running local {WHISPER_MODEL_NAME} transcription on the timestamp clip..."
                            )
                            english, detected_language = await asyncio.to_thread(
                                run_transcription,
                                local_path,
                                cancel_event,
                            )
                            caption_path = None
                        else:
                            await progress_cb(
                                "🔎 Checking creator-provided English subtitles..."
                            )
                            english, caption_path = await try_fast_youtube_captions(
                                job,
                                cancel_event,
                            )
                        detected_language = "English captions"
                        if caption_path:
                            files_to_clean.append(caption_path)

                        if not english:
                            await progress_cb(
                                f"🎙 No suitable captions found. Running local "
                                f"{WHISPER_MODEL_NAME} English transcription..."
                            )
                            audio_path, error = await download_audio_for_transcript(
                                job,
                                progress_cb,
                                cancel_event,
                            )
                            if error or not audio_path:
                                return (
                                    None,
                                    error or "Audio download failed.",
                                    files_to_clean,
                                )
                            files_to_clean.append(audio_path)

                            english, detected_language = await asyncio.to_thread(
                                run_transcription,
                                audio_path,
                                cancel_event,
                            )
                        else:
                            await progress_cb(
                                "✅ English subtitles found. Building natural "
                                "timestamped paragraphs..."
                            )

                        set_cached_transcript(
                            job,
                            english,
                            detected_language,
                        )

            ensure_not_cancelled(cancel_event)
            if not english:
                return (
                    None,
                    "No transcript text was available.",
                    files_to_clean,
                )

            urdu: Optional[List[TranscriptParagraph]] = None
            if language_code in {"ur", "bi", "ru", "er", "ur_ru"}:
                await progress_cb(
                    "🌐 Translating English transcript to Urdu with Google Translator..."
                )
                urdu = await asyncio.to_thread(
                    translate_paragraphs_to_urdu,
                    english,
                    cancel_event,
                )

            ensure_not_cancelled(cancel_event)
            await progress_cb("📝 Creating transcript file...")
            title = str(job.get("title") or "YouTube Video")

            if language_code in {"ru", "er", "ur_ru"}:
                output = await asyncio.to_thread(create_roman_transcript_txt, title, language_code, english, urdu or [])
            elif output_format == "pdf":
                output = await asyncio.to_thread(
                    create_transcript_pdf,
                    title,
                    language_code,
                    detected_language,
                    english,
                    urdu,
                )
            elif output_format == "srt":
                output = await asyncio.to_thread(
                    create_transcript_srt,
                    title,
                    language_code,
                    english,
                    urdu,
                )
            else:
                output = await asyncio.to_thread(
                    create_transcript_txt,
                    title,
                    language_code,
                    detected_language,
                    english,
                    urdu,
                )

            files_to_clean.append(output)
            return output, None, files_to_clean

        except JobCancelled:
            raise
        except Exception as exc:
            logger.exception("Transcript processing failed")
            return (
                None,
                f"Transcript processing failed: {str(exc)[:250]}",
                files_to_clean,
            )


# =============================================================================
# Video enhancement engine
# =============================================================================

def run_ffmpeg(
    command: List[str],
    cancel_event: Optional[threading.Event] = None,
) -> None:
    log_handle = tempfile.NamedTemporaryFile(
        prefix="ffmpeg_job_", suffix=".log", delete=False
    )
    log_path = Path(log_handle.name)
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=log_handle,
    )
    try:
        while process.poll() is None:
            if cancel_event and cancel_event.is_set():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                raise JobCancelled("Task stopped by the user")
            time.sleep(CANCEL_POLL_SECONDS)
        log_handle.close()
        if process.returncode != 0:
            error = log_path.read_text(
                encoding="utf-8", errors="ignore"
            )[-1800:]
            raise RuntimeError(error or "FFmpeg error")
    finally:
        if not log_handle.closed:
            log_handle.close()
        log_path.unlink(missing_ok=True)


def fast_enhance_video(
    input_path: Path,
    target_short_side: int,
    cancel_event: threading.Event,
) -> Path:
    """Fast CPU-friendly HD upscale/enhance with Telegram-playable output."""
    media = probe_media(input_path)
    out_w, out_h = media_dimensions(
        int(media["width"]),
        int(media["height"]),
        target_short_side,
    )
    output_path = DOWNLOAD_DIR / (
        f"{safe_filename(input_path.stem)}_Fast_HD{target_short_side}_"
        f"{uuid.uuid4().hex[:6]}.mp4"
    )

    filter_chain = (
        f"scale={out_w}:{out_h}:flags=lanczos,"
        "hqdn3d=0.65:0.55:2.2:2.0,"
        "unsharp=5:5:0.38:3:3:0.0,"
        "format=yuv420p"
    )
    run_ffmpeg(
        [
            "ffmpeg", "-y",
            "-hide_banner",
            "-loglevel", "warning",
            "-i", str(input_path),
            "-vf", filter_chain,
            "-c:v", "libx264",
            "-preset", "superfast",
            "-crf", "20",
            "-threads", str(FFMPEG_THREADS),
            "-c:a", "aac",
            "-b:a", "160k",
            "-max_muxing_queue_size", "4096",
            "-movflags", "+faststart",
            str(output_path),
        ],
        cancel_event,
    )
    return output_path


def external_ai_enhancer_status() -> Tuple[Optional[str], str]:
    """
    Prefer a genuinely available external neural enhancer.
    Order: Real-ESRGAN NCNN -> waifu2x NCNN. If neither exists, return None.
    """
    candidates = [
        (REALESRGAN_BIN, "Real-ESRGAN NCNN"),
        ("/usr/local/bin/waifu2x-ncnn-vulkan", "waifu2x NCNN"),
        ("/usr/bin/waifu2x-ncnn-vulkan", "waifu2x NCNN"),
    ]
    for binary, label in candidates:
        path = Path(binary)
        if path.exists() and os.access(path, os.X_OK):
            return str(path), label
    return None, "No external neural enhancer is installed"


def reliable_enhance_video(
    input_path: Path,
    cancel_event: threading.Event,
) -> Tuple[Path, bool, str]:
    """
    Use a real neural engine when it is installed and passes runtime checks.
    Otherwise use the reliable HQ engine and describe it honestly as HQ,
    not as AI.
    """
    binary, label = external_ai_enhancer_status()
    if binary == REALESRGAN_BIN:
        ok, reason = realesrgan_runtime_status()
        if ok:
            return ai_enhance_video(input_path, cancel_event)
        logger.warning("Real-ESRGAN unavailable: %s", reason)

    # waifu2x integration is optional and only used when the binary is present.
    if binary and "waifu2x" in label.lower():
        media = probe_media(input_path)
        duration = float(media.get("duration") or 0)
        if duration <= CPU_AI_MAX_SAFE_SECONDS:
            work = Path(tempfile.mkdtemp(prefix="waifu2x_video_"))
            frames_in = work / "in"
            frames_out = work / "out"
            frames_in.mkdir()
            frames_out.mkdir()
            fps = float(media.get("fps") or 0) or 30.0
            output = DOWNLOAD_DIR / f"{safe_filename(input_path.stem)}_waifu2x_{uuid.uuid4().hex[:6]}.mp4"
            try:
                run_process([
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                    "-i", str(input_path), str(frames_in / "%08d.png")
                ], cancel_event, "Frame extraction failed", 600)
                run_process([
                    binary, "-i", str(frames_in), "-o", str(frames_out),
                    "-n", "2", "-s", "2", "-f", "png"
                ], cancel_event, "waifu2x enhancement failed", MAX_AI_ENHANCE_WALLCLOCK_SECONDS)
                run_process([
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                    "-framerate", f"{fps:.6f}", "-i", str(frames_out / "%08d.png"),
                    "-i", str(input_path), "-map", "0:v", "-map", "1:a?",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
                    str(output)
                ], cancel_event, "waifu2x video rebuild failed", 900)
                return output, True, "waifu2x NCNN neural enhancement"
            finally:
                shutil.rmtree(work, ignore_errors=True)

    media = probe_media(input_path)
    short_side = min(int(media.get("width") or 0), int(media.get("height") or 0))
    duration = float(media.get("duration") or 0)

    # Use the installed OpenCV EDSR/FSRCNN neural model for short,
    # lower-resolution clips. This is a genuine local AI engine and avoids
    # pretending that the normal FFmpeg fallback is AI.
    cpu_ok, cpu_reason = cpu_ai_engine_status()
    if (
        cpu_ok
        and short_side > 0
        and short_side <= CPU_AI_MAX_SAFE_SHORT_SIDE
        and duration <= CPU_AI_MAX_SAFE_SECONDS
    ):
        try:
            return ai_enhance_video(input_path, cancel_event)
        except Exception as exc:
            logger.warning("OpenCV neural enhancement failed: %s", exc)

    target = min(USER_UPSCALE_MAX_SHORT_SIDE, max(1080, int(max(1, short_side) * 1.35)))
    return fast_enhance_video(input_path, target, cancel_event), False, (
        "Reliable HQ enhancement; no usable neural engine for this clip "
        f"({cpu_reason})"
    )


class AIEnhanceUnavailable(RuntimeError):
    pass


def realesrgan_engine_status() -> Tuple[bool, str]:
    binary = Path(REALESRGAN_BIN)
    models = Path(REALESRGAN_MODELS_DIR)

    if not binary.exists():
        return False, f"Real-ESRGAN binary not found: {binary}"
    if not os.access(binary, os.X_OK):
        return False, f"Real-ESRGAN binary is not executable: {binary}"
    if not models.exists():
        return False, f"Real-ESRGAN models folder not found: {models}"

    required = [
        models / f"{REALESRGAN_MODEL_NAME}.param",
        models / f"{REALESRGAN_MODEL_NAME}.bin",
    ]
    missing = [path.name for path in required if not path.exists()]
    if missing:
        return False, "Missing Real-ESRGAN model files: " + ", ".join(missing)

    return True, "Real-ESRGAN files are installed"


def realesrgan_runtime_status(force: bool = False) -> Tuple[bool, str]:
    """
    Run a realistic, bounded Real-ESRGAN preflight before using the GPU path.

    The probe processes three 128x128 frames as a directory batch, which is
    closer to the real video workload than a single 16x16 image. Positive and
    negative results are cached only briefly.
    """
    global _AI_RUNTIME_PROBE_RESULT, _AI_RUNTIME_PROBE_AT

    installed, reason = realesrgan_engine_status()
    if not installed:
        return False, reason

    now = time.time()
    with _AI_RUNTIME_PROBE_LOCK:
        if (
            not force
            and _AI_RUNTIME_PROBE_RESULT is not None
            and now - _AI_RUNTIME_PROBE_AT < AI_PREFLIGHT_CACHE_SECONDS
        ):
            return _AI_RUNTIME_PROBE_RESULT

        work_dir = Path(tempfile.mkdtemp(prefix="realesrgan_probe_"))
        frames_in = work_dir / "frames_in"
        frames_out = work_dir / "frames_out"
        frames_in.mkdir(parents=True, exist_ok=True)
        frames_out.mkdir(parents=True, exist_ok=True)

        try:
            generated = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi",
                    "-i", "testsrc2=size=128x128:rate=3:duration=1",
                    "-frames:v", "3",
                    str(frames_in / "%03d.png"),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
            )

            input_frames = sorted(frames_in.glob("*.png"))
            if generated.returncode != 0 or len(input_frames) < 3:
                result = (False, "AI preflight frame generation failed")
            else:
                command = [
                    REALESRGAN_BIN,
                    "-i", str(frames_in),
                    "-o", str(frames_out),
                    "-m", REALESRGAN_MODELS_DIR,
                    "-n", REALESRGAN_MODEL_NAME,
                    "-s", str(AI_UPSCALE_SCALE),
                    "-t", "32",
                    "-j", "1:1:1",
                    "-f", "png",
                ]
                try:
                    proc = subprocess.run(
                        command,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=AI_PREFLIGHT_TIMEOUT_SECONDS,
                    )
                    output_frames = sorted(frames_out.glob("*.png"))
                    if proc.returncode == 0 and len(output_frames) >= 3:
                        result = (True, "Real-ESRGAN runtime is ready")
                    else:
                        detail = (proc.stderr or "").strip().splitlines()
                        last = (
                            detail[-1][:180]
                            if detail
                            else "Vulkan/AI runtime unavailable"
                        )
                        result = (False, last)
                except subprocess.TimeoutExpired:
                    result = (
                        False,
                        f"AI runtime preflight timed out after "
                        f"{AI_PREFLIGHT_TIMEOUT_SECONDS}s",
                    )
        except Exception as exc:
            result = (False, f"AI runtime preflight failed: {str(exc)[:180]}")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

        _AI_RUNTIME_PROBE_RESULT = result
        _AI_RUNTIME_PROBE_AT = time.time()
        return result


def run_process(
    command: List[str],
    cancel_event: Optional[threading.Event] = None,
    error_prefix: str = "Process failed",
    timeout_seconds: Optional[float] = 300.0,
) -> None:
    """Run a subprocess with user-cancel support and a hard wall-clock timeout."""
    log_handle = tempfile.NamedTemporaryFile(
        prefix="media_job_",
        suffix=".log",
        delete=False,
    )
    log_path = Path(log_handle.name)
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=log_handle,
    )
    started_at = time.monotonic()

    def stop_process() -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    try:
        while process.poll() is None:
            if cancel_event and cancel_event.is_set():
                stop_process()
                raise JobCancelled("Task stopped by the user")

            if (
                timeout_seconds is not None
                and timeout_seconds > 0
                and time.monotonic() - started_at >= timeout_seconds
            ):
                stop_process()
                if not log_handle.closed:
                    log_handle.close()
                error = log_path.read_text(
                    encoding="utf-8",
                    errors="ignore",
                )[-2200:]
                detail = f": {error}" if error else ""
                raise RuntimeError(
                    f"{error_prefix}: timed out after "
                    f"{int(timeout_seconds)} seconds{detail}"
                )

            time.sleep(CANCEL_POLL_SECONDS)

        if not log_handle.closed:
            log_handle.close()

        if process.returncode != 0:
            error = log_path.read_text(
                encoding="utf-8",
                errors="ignore",
            )[-2200:]
            raise RuntimeError(
                f"{error_prefix}: {error or f'exit code {process.returncode}'}"
            )
    finally:
        if process.poll() is None:
            stop_process()
        if not log_handle.closed:
            log_handle.close()
        log_path.unlink(missing_ok=True)


def _cpu_ai_model_candidates(
    duration: Optional[float] = None,
) -> List[Tuple[Path, str, int, str]]:
    """Return CPU super-resolution models in quality/speed preference order."""
    candidates: List[Tuple[Path, str, int, str]] = []
    quality_path = Path(CPU_AI_QUALITY_MODEL_PATH)
    if (
        quality_path.exists()
        and quality_path.stat().st_size > 0
        and (duration is None or duration <= CPU_AI_QUALITY_MAX_SECONDS)
    ):
        candidates.append((quality_path, "edsr", 2, "OpenCV EDSR x2 CPU AI"))

    fast_path = Path(CPU_AI_MODEL_PATH)
    if fast_path.exists() and fast_path.stat().st_size > 0:
        candidates.append((fast_path, "fsrcnn", 2, "OpenCV FSRCNN x2 CPU AI"))
    return candidates


def _load_cpu_superres_model(
    duration: Optional[float] = None,
) -> Tuple[Any, Path, str, int, str]:
    try:
        import cv2  # type: ignore
    except Exception as exc:
        raise AIEnhanceUnavailable(
            f"OpenCV AI module import failed: {str(exc)[:180]}"
        )

    if not hasattr(cv2, "dnn_superres"):
        raise AIEnhanceUnavailable(
            "OpenCV dnn_superres is unavailable. "
            "Install opencv-contrib-python-headless."
        )

    errors: List[str] = []
    for model_path, algorithm, scale, label in _cpu_ai_model_candidates(duration):
        try:
            sr = cv2.dnn_superres.DnnSuperResImpl_create()
            sr.readModel(str(model_path))
            sr.setModel(algorithm, scale)
            return sr, model_path, algorithm, scale, label
        except Exception as exc:
            errors.append(f"{label}: {str(exc)[:120]}")

    if not _cpu_ai_model_candidates(duration):
        raise AIEnhanceUnavailable(
            "No CPU AI model is installed. Expected EDSR or FSRCNN model files."
        )
    raise AIEnhanceUnavailable("; ".join(errors)[:300])


def cpu_ai_engine_status() -> Tuple[bool, str]:
    """Check the strongest available CPU AI super-resolution engine."""
    try:
        _, _, _, _, label = _load_cpu_superres_model(None)
        return True, f"{label} is ready"
    except AIEnhanceUnavailable as exc:
        return False, str(exc)

def cpu_ai_enhance_video(
    input_path: Path,
    cancel_event: threading.Event,
) -> Path:
    """
    Real AI 2x upscale on CPU using OpenCV dnn_superres.

    EDSR is preferred for short clips when installed; FSRCNN remains the
    faster fallback for longer clips or lower-power CPU VPS instances.

    Enhanced frames are passed to FFmpeg through one dedicated writer thread
    and a small bounded queue. The main AI loop never calls stdin.write()
    directly, so FFmpeg backpressure cannot freeze the enhancement thread
    forever. A stalled writer triggers a hard timeout and FFmpeg is killed.
    """
    ready, reason = cpu_ai_engine_status()
    if not ready:
        raise AIEnhanceUnavailable(reason)

    import cv2  # type: ignore

    media = probe_media(input_path)
    src_w = int(media.get("width") or 0)
    src_h = int(media.get("height") or 0)
    fps = float(media.get("fps") or 0.0) or 30.0
    if src_w <= 0 or src_h <= 0:
        raise RuntimeError("Could not detect video dimensions for CPU AI upscale")

    try:
        cv2.setNumThreads(max(1, min(4, FFMPEG_THREADS)))
    except Exception:
        pass

    duration = float(media.get("duration") or 0.0)
    sr, ai_model_path, ai_algorithm, ai_scale, ai_label = (
        _load_cpu_superres_model(duration)
    )
    logger.info(
        "CPU AI enhancer selected %s (%s, x%d)",
        ai_label,
        ai_model_path,
        ai_scale,
    )

    src_short = min(src_w, src_h)
    prep_short = min(src_short, CPU_AI_PREP_SHORT_SIDE)
    prep_w, prep_h = media_dimensions(src_w, src_h, prep_short)

    ai_w = prep_w * 2
    ai_h = prep_h * 2

    desired_short = min(
        AI_TARGET_SHORT_SIDE,
        max(720, min(src_short * 2, AI_TARGET_SHORT_SIDE)),
    )
    final_w, final_h = media_dimensions(src_w, src_h, desired_short)

    output_path = DOWNLOAD_DIR / (
        f"{safe_filename(input_path.stem)}_AI_CPU_"
        f"{uuid.uuid4().hex[:6]}.mp4"
    )

    capture = cv2.VideoCapture(str(input_path))
    if not capture.isOpened():
        raise RuntimeError("CPU AI could not open the input video")

    log_handle = tempfile.NamedTemporaryFile(
        prefix="cpu_ai_ffmpeg_",
        suffix=".log",
        delete=False,
    )
    log_path = Path(log_handle.name)

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-hide_banner",
        "-loglevel", "warning",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s", f"{final_w}x{final_h}",
        "-r", f"{fps:.6f}",
        "-i", "pipe:0",
        "-i", str(input_path),
        "-map", "0:v:0",
        "-map", "1:a?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-threads", str(FFMPEG_THREADS),
        "-c:a", "aac",
        "-b:a", "160k",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ]

    process = subprocess.Popen(
        ffmpeg_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=log_handle,
        bufsize=0,
    )

    frame_queue: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=2)
    writer_done = threading.Event()
    writer_errors: List[BaseException] = []
    started_at = time.monotonic()
    wallclock_deadline = started_at + MAX_AI_ENHANCE_WALLCLOCK_SECONDS

    def stop_ffmpeg() -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass

    def writer_worker() -> None:
        try:
            if process.stdin is None:
                raise RuntimeError("FFmpeg input pipe is unavailable")

            while True:
                payload = frame_queue.get()
                if payload is None:
                    break
                process.stdin.write(payload)
        except BaseException as exc:
            writer_errors.append(exc)
        finally:
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except Exception:
                pass
            writer_done.set()

    writer_thread = threading.Thread(
        target=writer_worker,
        name=f"cpu-ai-ffmpeg-writer-{uuid.uuid4().hex[:6]}",
        daemon=True,
    )
    writer_thread.start()

    def check_runtime_state() -> None:
        ensure_not_cancelled(cancel_event)

        if time.monotonic() >= wallclock_deadline:
            raise RuntimeError(
                "CPU AI enhancement exceeded the maximum wall-clock time "
                f"({MAX_AI_ENHANCE_WALLCLOCK_SECONDS}s)"
            )

        if writer_errors:
            raise RuntimeError(
                f"FFmpeg AI frame writer failed: {str(writer_errors[0])[:220]}"
            )

        if process.poll() is not None and process.returncode not in (None, 0):
            raise RuntimeError(
                f"FFmpeg stopped while receiving AI frames "
                f"(exit code {process.returncode})"
            )

    def enqueue_with_timeout(payload: Optional[bytes]) -> None:
        write_deadline = min(
            wallclock_deadline,
            time.monotonic() + CPU_AI_STDIN_WRITE_TIMEOUT_SECONDS,
        )

        while True:
            check_runtime_state()
            remaining = write_deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    "FFmpeg stdin writer stalled for more than "
                    f"{CPU_AI_STDIN_WRITE_TIMEOUT_SECONDS}s"
                )
            try:
                frame_queue.put(payload, timeout=min(0.5, remaining))
                return
            except queue.Full:
                continue

    frames_written = 0
    try:
        while True:
            check_runtime_state()
            ok, frame = capture.read()
            if not ok:
                break

            if frame.shape[1] != prep_w or frame.shape[0] != prep_h:
                frame = cv2.resize(
                    frame,
                    (prep_w, prep_h),
                    interpolation=cv2.INTER_AREA,
                )

            enhanced = sr.upsample(frame)
            check_runtime_state()

            if enhanced.shape[1] != ai_w or enhanced.shape[0] != ai_h:
                raise RuntimeError("FSRCNN returned an unexpected frame size")

            if ai_w != final_w or ai_h != final_h:
                interpolation = (
                    cv2.INTER_AREA
                    if ai_w > final_w or ai_h > final_h
                    else cv2.INTER_LANCZOS4
                )
                enhanced = cv2.resize(
                    enhanced,
                    (final_w, final_h),
                    interpolation=interpolation,
                )

            enqueue_with_timeout(enhanced.tobytes())
            frames_written += 1

        if frames_written == 0:
            raise RuntimeError("CPU AI did not receive any video frames")

        # Tell the writer to close FFmpeg stdin after all queued frames drain.
        enqueue_with_timeout(None)

        while not writer_done.wait(timeout=0.25):
            check_runtime_state()

        if writer_errors:
            raise RuntimeError(
                f"FFmpeg AI frame writer failed: {str(writer_errors[0])[:220]}"
            )

        while process.poll() is None:
            check_runtime_state()
            time.sleep(CANCEL_POLL_SECONDS)

        if not log_handle.closed:
            log_handle.close()

        if process.returncode != 0:
            error = log_path.read_text(
                encoding="utf-8",
                errors="ignore",
            )[-2200:]
            raise RuntimeError(
                f"CPU AI video encoding failed: "
                f"{error or f'exit code {process.returncode}'}"
            )

        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise RuntimeError("CPU AI output video was not created")

        return output_path

    except JobCancelled:
        stop_ffmpeg()
        writer_done.wait(timeout=3)
        output_path.unlink(missing_ok=True)
        raise
    except Exception:
        stop_ffmpeg()
        writer_done.wait(timeout=3)
        output_path.unlink(missing_ok=True)
        raise
    finally:
        capture.release()
        stop_ffmpeg()
        writer_done.wait(timeout=1)
        try:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
        except Exception:
            pass
        if not log_handle.closed:
            log_handle.close()
        log_path.unlink(missing_ok=True)


def ai_enhance_video(
    input_path: Path,
    cancel_event: threading.Event,
) -> Tuple[Path, bool, str]:
    """
    AI 2x enhancement with bounded GPU/CPU execution.

    Real-ESRGAN is used only when the realistic preflight succeeds. Every GPU
    subprocess has a hard timeout. If GPU AI is unavailable/fails, CPU FSRCNN
    is tried. Fast HD is the final fallback when enabled.
    """
    media = probe_media(input_path)
    duration = float(media.get("duration") or 0.0)
    width = int(media.get("width") or 0)
    height = int(media.get("height") or 0)
    fps = float(media.get("fps") or 0.0) or 30.0

    if ENABLE_REALESRGAN_GPU:
        gpu_available, gpu_reason = realesrgan_runtime_status()
    else:
        gpu_available, gpu_reason = (
            False,
            "GPU Real-ESRGAN disabled by default for VPS stability",
        )

    if duration > MAX_AI_ENHANCE_SECONDS:
        reason = (
            f"Video is longer than the AI fast limit "
            f"({MAX_AI_ENHANCE_SECONDS}s)"
        )
        if not AI_ENHANCE_FALLBACK:
            raise AIEnhanceUnavailable(reason)
        output = fast_enhance_video(
            input_path,
            AI_TARGET_SHORT_SIDE,
            cancel_event,
        )
        return output, False, reason

    if not gpu_available:
        source_short_side = min(width, height) if width and height else 0
        cpu_available, cpu_reason = cpu_ai_engine_status()

        # CPU frame-by-frame super-resolution on an already-HD portrait clip
        # can take many minutes on a VPS. Use adaptive HQ upscale immediately
        # instead of timing out after 300s.
        if (
            source_short_side > CPU_AI_MAX_SAFE_SHORT_SIDE
            or duration > CPU_AI_MAX_SAFE_SECONDS
        ):
            target_short_side = min(
                USER_UPSCALE_MAX_SHORT_SIDE,
                max(
                    1080,
                    int(round(max(1, source_short_side) * 1.34)),
                ),
            )
            output = fast_enhance_video(
                input_path,
                target_short_side,
                cancel_event,
            )
            return (
                output,
                False,
                "Adaptive HQ upscale used because CPU AI would be too slow "
                f"for {source_short_side}p/{duration:.1f}s source",
            )

        if cpu_available:
            try:
                output = cpu_ai_enhance_video(
                    input_path,
                    cancel_event,
                )
                return output, True, "OpenCV FSRCNN x2 CPU AI"
            except JobCancelled:
                raise
            except Exception as exc:
                logger.exception(
                    "CPU AI enhancement failed; considering fallback"
                )
                if not AI_ENHANCE_FALLBACK:
                    raise
                output = fast_enhance_video(
                    input_path,
                    AI_TARGET_SHORT_SIDE,
                    cancel_event,
                )
                return (
                    output,
                    False,
                    f"CPU AI failed: {str(exc)[:180]}",
                )

        reason = (
            f"GPU AI unavailable: {gpu_reason}; "
            f"CPU AI unavailable: {cpu_reason}"
        )
        if not AI_ENHANCE_FALLBACK:
            raise AIEnhanceUnavailable(reason)
        output = fast_enhance_video(
            input_path,
            AI_TARGET_SHORT_SIDE,
            cancel_event,
        )
        return output, False, reason

    work_dir = Path(
        tempfile.mkdtemp(
            prefix="ai_upscale_",
            dir=str(DOWNLOAD_DIR),
        )
    )
    frames_in = work_dir / "frames_in"
    frames_out = work_dir / "frames_out"
    frames_in.mkdir(parents=True, exist_ok=True)
    frames_out.mkdir(parents=True, exist_ok=True)

    output_path = DOWNLOAD_DIR / (
        f"{safe_filename(input_path.stem)}_AI_HD_"
        f"{uuid.uuid4().hex[:6]}.mp4"
    )

    try:
        ensure_not_cancelled(cancel_event)

        run_process(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-map",
                "0:v:0",
                "-vsync",
                "0",
                str(frames_in / "%08d.png"),
            ],
            cancel_event=cancel_event,
            error_prefix="Frame extraction failed",
            timeout_seconds=AI_SUBPROCESS_TIMEOUT_SECONDS,
        )

        if not any(frames_in.glob("*.png")):
            raise RuntimeError("AI enhancer could not extract video frames")

        ensure_not_cancelled(cancel_event)

        command = [
            REALESRGAN_BIN,
            "-i",
            str(frames_in),
            "-o",
            str(frames_out),
            "-m",
            REALESRGAN_MODELS_DIR,
            "-n",
            REALESRGAN_MODEL_NAME,
            "-s",
            str(AI_UPSCALE_SCALE),
            "-t",
            str(REALESRGAN_TILE),
            "-j",
            "2:2:2",
            "-f",
            "png",
        ]

        run_process(
            command,
            cancel_event=cancel_event,
            error_prefix="Real-ESRGAN failed",
            timeout_seconds=AI_SUBPROCESS_TIMEOUT_SECONDS,
        )

        output_frames = sorted(frames_out.glob("*.png"))
        if not output_frames:
            raise RuntimeError("Real-ESRGAN returned no enhanced frames")

        input_short_side = min(width, height) if width and height else 540
        final_short_side = min(
            AI_TARGET_SHORT_SIDE,
            max(input_short_side, input_short_side * AI_UPSCALE_SCALE),
        )
        final_short_side = max(720, final_short_side)
        out_w, out_h = media_dimensions(
            max(2, width),
            max(2, height),
            final_short_side,
        )

        run_process(
            [
                "ffmpeg",
                "-y",
                "-framerate",
                f"{fps:.6f}",
                "-i",
                str(frames_out / "%08d.png"),
                "-i",
                str(input_path),
                "-map",
                "0:v:0",
                "-map",
                "1:a?",
                "-vf",
                (
                    f"scale={out_w}:{out_h}:flags=lanczos,"
                    "hqdn3d=0.8:0.7:3:3,"
                    "unsharp=5:5:0.35:3:3:0.0,"
                    "format=yuv420p"
                ),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-threads",
                str(FFMPEG_THREADS),
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            cancel_event=cancel_event,
            error_prefix="AI video encode failed",
            timeout_seconds=AI_SUBPROCESS_TIMEOUT_SECONDS,
        )

        return output_path, True, "Real-ESRGAN AI 2x"

    except JobCancelled:
        output_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        if not AI_ENHANCE_FALLBACK:
            raise
        logger.exception("AI enhancement failed; using fast HD fallback")
        fallback = fast_enhance_video(
            input_path,
            AI_TARGET_SHORT_SIDE,
            cancel_event,
        )
        return fallback, False, str(exc)[:240]
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def jobs(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Dict[str, Any]]:
    store = context.application.bot_data.setdefault("jobs", {})
    now = time.time()
    expired = [
        key
        for key, value in store.items()
        if now - float(value.get("created_at", 0)) > 7200
    ]
    for key in expired:
        value = store.pop(key, None) or {}
        path = value.get("input_path")
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass

    if len(store) > 500:
        oldest = sorted(
            store,
            key=lambda key: float(store[key].get("created_at", 0)),
        )
        for key in oldest[:-400]:
            store.pop(key, None)
    return store


def save_youtube_job(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    url: str,
    info: Dict[str, Any],
    choices: Dict[str, QualityChoice],
) -> str:
    job_id = uuid.uuid4().hex[:10]
    jobs(context)[job_id] = {
        "job_id": job_id,
        "type": "youtube",
        "owner_id": user_id,
        "url": url,
        "title": str(info.get("title") or "YouTube Video"),
        "video_id": str(info.get("id") or job_id),
        "duration": optional_int(info.get("duration")),
        "created_at": time.time(),
        "choices": {
            label: asdict(choice)
            for label, choice in choices.items()
        },
    }
    return job_id


def save_transcript_only_job(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    url: str,
    title: str = "YouTube Video",
    duration: Optional[int] = None,
) -> str:
    job_id = uuid.uuid4().hex[:10]
    jobs(context)[job_id] = {
        "job_id": job_id,
        "type": "youtube",
        "owner_id": user_id,
        "url": url,
        "title": title,
        "video_id": job_id,
        "duration": duration,
        "created_at": time.time(),
        "choices": {},
    }
    return job_id


def save_enhance_job(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    input_path: Path,
    original_name: str,
    media: Dict[str, Any],
) -> str:
    job_id = uuid.uuid4().hex[:10]
    jobs(context)[job_id] = {
        "job_id": job_id,
        "type": "enhance",
        "owner_id": user_id,
        "input_path": str(input_path.resolve()),
        "original_name": original_name,
        "duration": float(media.get("duration") or 0),
        "width": int(media.get("width") or 0),
        "height": int(media.get("height") or 0),
        "created_at": time.time(),
    }
    return job_id


def save_video_editor_job(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    input_path: Path,
    original_name: str,
    media: Dict[str, Any],
) -> str:
    job_id = uuid.uuid4().hex[:10]
    jobs(context)[job_id] = {
        "job_id": job_id,
        "type": "video_editor",
        "owner_id": user_id,
        "input_path": str(input_path.resolve()),
        "original_name": original_name,
        "duration": float(media.get("duration") or 0),
        "width": int(media.get("width") or 0),
        "height": int(media.get("height") or 0),
        "created_at": time.time(),
        "split_enabled": True,
    }
    channel_id = str(context.user_data.get("admin_channel_profile_id") or "")
    channel_profile = get_admin_channel(channel_id) if channel_id and int(user_id) == OWNER_ID else None
    if channel_profile:
        jobs(context)[job_id]["admin_channel_profile"] = channel_profile
        jobs(context)[job_id]["channel_name"] = str(channel_profile.get("name") or "")
    return job_id


def video_editor_template_menu(job_id: str) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    keys = list(CAPTION_TEMPLATES.keys())

    for index in range(0, len(keys), 2):
        row: List[InlineKeyboardButton] = []
        for key in keys[index:index + 2]:
            row.append(
                InlineKeyboardButton(
                    CAPTION_TEMPLATES[key]["label"],
                    callback_data=f"vedit:{job_id}:{key}",
                )
            )
        rows.append(row)

    rows.append([
        InlineKeyboardButton("👥 Auto Split: ON/OFF", callback_data=f"vedsplit:{job_id}"),
    ])
    rows.append(
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data=f"c:{job_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def analyzer_source_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔗 Analyze YouTube Link", callback_data="analyzer:youtube")],
            [InlineKeyboardButton("📄 Analyze TXT/PDF/SRT", callback_data="analyzer:file")],
            [InlineKeyboardButton("⬅️ Back", callback_data="menu:home")],
        ]
    )


def downloader_source_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🎬 Full Video Downloader",
                    callback_data="downloadflow:full",
                )
            ],
            [
                InlineKeyboardButton(
                    "✂️ Timestamp Clip Downloader",
                    callback_data="downloadflow:timestamp",
                )
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="menu:home")],
        ]
    )


def timestamp_result_menu(job_id: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🇬🇧 English Transcript", callback_data=f"t:{job_id}:en:txt"),
            InlineKeyboardButton("🇵🇰 Urdu Transcript", callback_data=f"t:{job_id}:ur:txt"),
        ],
        [
            InlineKeyboardButton("🌍 English + Urdu", callback_data=f"t:{job_id}:bi:txt"),
        ],
        [InlineKeyboardButton("⬅️ Main Menu", callback_data="menu:home")],
    ]
    return InlineKeyboardMarkup(rows)


def video_editor_source_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📤 Send Full File", callback_data="editorflow:file"),
                InlineKeyboardButton("✂️ File + Timestamp", callback_data="editorflow:filetimestamp"),
            ],
            [InlineKeyboardButton("🔗 YouTube + Timestamp", callback_data="editorflow:youtube")],
            [InlineKeyboardButton("⬅️ Back", callback_data="menu:home")],
        ]
    )


def avclabs_menu() -> InlineKeyboardMarkup:
    rows = []
    keys = list(AVCLABS_OPERATIONS)
    for i in range(0, len(keys), 2):
        rows.append([InlineKeyboardButton(AVCLABS_OPERATIONS[k], callback_data=f"avc:{k}") for k in keys[i:i+2]])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


def main_menu(
    is_admin: bool,
    clipper_allowed: bool = False,
    editor_allowed: bool = False,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✂️ AI Clipper", callback_data="menu:clipper"),
            InlineKeyboardButton("🎬 AI Video Editor", callback_data="menu:editor"),
        ],
        [InlineKeyboardButton("⛔ Stop Active Job", callback_data="stop:active")],
    ])


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🍪 Set Cookies",
                    callback_data="admin:cookies",
                ),
                InlineKeyboardButton(
                    "🌐 Add Proxies",
                    callback_data="admin:proxies",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📋 List Proxies",
                    callback_data="admin:listproxies",
                ),
                InlineKeyboardButton(
                    "🔐 Auth Status",
                    callback_data="admin:status",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🗑 Clear Cookies",
                    callback_data="admin:clearcookies",
                ),
                InlineKeyboardButton(
                    "🧹 Clear Proxies",
                    callback_data="admin:clearproxies",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⚡ Test Proxy Speed",
                    callback_data="admin:testproxies",
                ),
                InlineKeyboardButton(
                    "📋 Fastest First",
                    callback_data="admin:listproxies",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🧠 Transcript Status",
                    callback_data="admin:apicheck",
                ),
                InlineKeyboardButton(
                    "📡 Live Processing",
                    callback_data="admin:live",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🧠 Deep Viral Research",
                    callback_data="admin:deepviral",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⛔ Stop All Tasks",
                    callback_data="admin:stopall",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Back to Main Menu",
                    callback_data="menu:home",
                )
            ],
        ]
    )


def transcript_menu(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🇬🇧 English TXT",
                    callback_data=f"t:{job_id}:en:txt",
                ),
                InlineKeyboardButton(
                    "🇬🇧 English PDF",
                    callback_data=f"t:{job_id}:en:pdf",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🇬🇧 English SRT",
                    callback_data=f"t:{job_id}:en:srt",
                )
            ],
            [
                InlineKeyboardButton(
                    "🇵🇰 Urdu TXT",
                    callback_data=f"t:{job_id}:ur:txt",
                ),
                InlineKeyboardButton(
                    "🇵🇰 Urdu PDF",
                    callback_data=f"t:{job_id}:ur:pdf",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🇵🇰 Urdu SRT",
                    callback_data=f"t:{job_id}:ur:srt",
                )
            ],
            [
                InlineKeyboardButton(
                    "🌍 English + Urdu TXT",
                    callback_data=f"t:{job_id}:bi:txt",
                ),
                InlineKeyboardButton(
                    "🌍 English + Urdu PDF",
                    callback_data=f"t:{job_id}:bi:pdf",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🌍 English + Urdu SRT",
                    callback_data=f"t:{job_id}:bi:srt",
                )
            ],
            [
                InlineKeyboardButton("🔤 Roman Urdu TXT", callback_data=f"t:{job_id}:ru:txt"),
                InlineKeyboardButton("🇬🇧 English + Roman Urdu", callback_data=f"t:{job_id}:er:txt"),
            ],
            [InlineKeyboardButton("🇵🇰 Urdu + Roman Urdu", callback_data=f"t:{job_id}:ur_ru:txt")],
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data=f"c:{job_id}",
                )
            ],
        ]
    )


def enhance_menu(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⚡ Fast HD 720p",
                    callback_data=f"e:{job_id}:studio720",
                ),
                InlineKeyboardButton(
                    "⚡ Fast HD 1080p",
                    callback_data=f"e:{job_id}:studio1080",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🤖 AI HD Upscale 2x",
                    callback_data=f"e:{job_id}:ai2x",
                )
            ],
            [
                InlineKeyboardButton(
                    "Cancel",
                    callback_data=f"c:{job_id}",
                )
            ],
        ]
    )


def quality_menu(
    job_id: str,
    choices: Dict[str, QualityChoice],
) -> Tuple[str, InlineKeyboardMarkup]:
    lines: List[str] = []
    buttons: List[List[InlineKeyboardButton]] = []

    for label in QUALITY_ORDER:
        choice = choices.get(label)
        if not choice:
            continue
        size = (
            f"about {human_bytes(choice.estimated_size)}"
            if choice.estimated_size is not None
            else "size unavailable"
        )
        warning = (
            " - over 2 GB"
            if choice.estimated_size is not None
            and choice.estimated_size > TELEGRAM_MAX_BYTES
            else ""
        )
        delivery = "video" if choice.telegram_video else "document"
        lines.append(f"{label}: {size}, {delivery}{warning}")
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{label} - {size}",
                    callback_data=f"q:{job_id}:{label}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "MP3",
                callback_data=f"a:{job_id}",
            )
        ]
    )
    buttons.extend(transcript_menu(job_id).inline_keyboard[:-1])
    buttons.append(
        [
            InlineKeyboardButton(
                "Cancel",
                callback_data=f"c:{job_id}",
            )
        ]
    )

    return (
        "Available options:\n" + "\n".join(lines),
        InlineKeyboardMarkup(buttons),
    )


async def send_main_menu(message, admin: bool) -> None:
    await message.reply_text(
        f"🎬 {EDITING_STUDIO_NAME}\n\n"
        "Private editing workspace using the same editing/rendering engine as the main bot.\n\n"
        "✂️ AI Clipper — viral clip selection + local editing\n"
        "🎬 AI Video Editor — file/YouTube/timestamp editing\n"
        "🎞 Master Editor — full automatic podcast edit\n\n"
        "Choose a workflow.",
        reply_markup=main_menu(True, True, True),
    )


# =============================================================================
# Telegram send helpers
# =============================================================================

async def send_local_document(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    path: Path,
    caption: str,
):
    return await context.bot.send_document(
        chat_id=chat_id,
        document=path.resolve(),
        filename=path.name,
        caption=caption,
        read_timeout=7200,
        write_timeout=7200,
        connect_timeout=60,
        pool_timeout=180,
    )


async def send_local_video(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    path: Path,
    caption: str,
    duration: Optional[int] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
):
    return await context.bot.send_video(
        chat_id=chat_id,
        video=path.resolve(),
        filename=path.name,
        caption=caption,
        duration=duration,
        width=width,
        height=height,
        supports_streaming=True,
        read_timeout=7200,
        write_timeout=7200,
        connect_timeout=60,
        pool_timeout=180,
    )


async def send_local_audio(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    path: Path,
    title: str,
    duration: Optional[int],
):
    return await context.bot.send_audio(
        chat_id=chat_id,
        audio=path.resolve(),
        filename=path.name,
        title=title[:64],
        duration=duration,
        caption=f"MP3\n{owner_footer()}",
        read_timeout=7200,
        write_timeout=7200,
        connect_timeout=60,
        pool_timeout=180,
    )


async def send_video_with_fallback(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    path: Path,
    caption: str,
    duration: Optional[int] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> str:
    async with UPLOAD_SEMAPHORE:
        await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
        try:
            await send_local_video(
                context,
                chat_id,
                path,
                caption,
                duration,
                width,
                height,
            )
            return "video"
        except TelegramError as exc:
            if is_too_large_error(exc):
                raise
            logger.warning(
                "Video upload failed, using document fallback: %s",
                exc,
            )
            await context.bot.send_chat_action(
                chat_id,
                ChatAction.UPLOAD_DOCUMENT,
            )
            await send_local_document(
                context,
                chat_id,
                path,
                caption,
            )
            return "document"


def triple_quality_check(
    path: Path,
    expected_duration: Optional[float] = None,
    allow_source_edge_warning: bool = False,
) -> Tuple[bool, List[str]]:
    """
    Three separate pre-delivery review passes.

    PASS 1: streams, audio, resolution and duration
    PASS 2: decoded visual samples, black/frozen frame detection
    PASS 3: final face-framing edge review
    """
    notes: List[str] = []
    if not path.exists() or path.stat().st_size < 50_000:
        return False, ["PASS 1 failed: file missing or too small"]

    # PASS 1
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-print_format", "json",
                "-show_streams", "-show_format",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=45,
        )
        raw = json.loads(result.stdout)
        streams = raw.get("streams", [])
        video_streams = [
            s for s in streams if s.get("codec_type") == "video"
        ]
        audio_streams = [
            s for s in streams if s.get("codec_type") == "audio"
        ]
        if not video_streams:
            return False, ["PASS 1 failed: no video stream"]
        video = video_streams[0]
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        duration = numeric(
            video.get("duration")
            or raw.get("format", {}).get("duration")
        )
        if width <= 0 or height <= 0 or duration <= 0:
            return False, ["PASS 1 failed: invalid metadata"]
        if not audio_streams:
            return False, ["PASS 1 failed: audio stream missing"]
        if expected_duration and abs(duration - expected_duration) > max(
            1.7,
            expected_duration * 0.055,
        ):
            return False, [
                f"PASS 1 failed: duration mismatch {duration:.2f}s"
            ]
        audio_duration = numeric(
            audio_streams[0].get("duration")
            or raw.get("format", {}).get("duration")
        )
        av_delta = abs(float(audio_duration) - float(duration)) if audio_duration > 0 else 0.0
        if audio_duration > 0 and av_delta > max(0.30, duration * 0.015):
            return False, [
                f"PASS 1 failed: A/V duration drift {av_delta:.3f}s"
            ]
        if width < 540 or height < 900:
            return False, [
                f"PASS 1 failed: low output resolution {width}x{height}"
            ]
        fps_label = str(video.get("r_frame_rate") or video.get("avg_frame_rate") or "unknown")
        notes.append(
            f"PASS 1 OK: {width}x{height}, audio+video, {duration:.2f}s, "
            f"fps={fps_label}, A/V delta={av_delta:.3f}s"
        )
    except Exception as exc:
        return False, [f"PASS 1 failed: {str(exc)[:180]}"]

    # PASS 2
    try:
        import cv2  # type: ignore
        cap = cv2.VideoCapture(str(path))
        frames = []
        brightness = []
        for ratio in (
            0.05, 0.13, 0.22, 0.34, 0.46,
            0.58, 0.70, 0.80, 0.90, 0.97,
        ):
            cap.set(cv2.CAP_PROP_POS_MSEC, duration * ratio * 1000.0)
            ok, frame = cap.read()
            if ok and frame is not None and getattr(frame, "size", 0) > 0:
                small = cv2.resize(
                    frame,
                    (160, 284),
                    interpolation=cv2.INTER_AREA,
                )
                frames.append(small)
                brightness.append(float(small.mean()))
        cap.release()

        if len(frames) < 7:
            return False, [
                "PASS 2 failed: too many sampled frames could not decode"
            ]
        if sum(1 for value in brightness if value < 7.0) >= 3:
            return False, [
                "PASS 2 failed: repeated black frames detected"
            ]

        diffs = [
            float(cv2.absdiff(frames[i], frames[i - 1]).mean())
            for i in range(1, len(frames))
        ]
        if diffs and sum(1 for value in diffs if value < 0.15) >= len(diffs) - 1:
            return False, ["PASS 2 failed: output appears frozen"]
        notes.append("PASS 2 OK: frames decode, visible, not frozen")
    except Exception as exc:
        return False, [f"PASS 2 failed: {str(exc)[:180]}"]

    # PASS 3
    try:
        import cv2  # type: ignore
        detector, detector_kind = _create_face_detector(cv2)
        cap = cv2.VideoCapture(str(path))
        face_samples = 0
        clipped_samples = 0
        offcenter_samples = 0
        center_45_55_samples = 0
        single_face_samples = 0
        multi_face_samples = 0

        for ratio in (0.08, 0.20, 0.32, 0.44, 0.56, 0.68, 0.80, 0.92):
            cap.set(cv2.CAP_PROP_POS_MSEC, duration * ratio * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            boxes = _detect_face_boxes_for_reframe(
                frame,
                detector,
                detector_kind,
                cv2,
            )
            if not boxes:
                continue
            face_samples += 1
            if len(boxes) >= 2:
                multi_face_samples += 1
            # V7.4 delivery guard: do not silently ship the same failure the user
            # reported (speaker face parked at the far left/right). For a single
            # visible speaker, require the dominant face to live near the visual
            # centre. Two-face split layouts are reviewed separately by edge safety.
            if len(boxes) == 1:
                single_face_samples += 1
                dominant_cx = float(boxes[0][0])
                if 0.45 <= dominant_cx <= 0.55:
                    center_45_55_samples += 1
                # Allow a little detector/profile noise beyond the requested 45–55%
                # measurement band, but never silently deliver a speaker parked
                # meaningfully away from centre.
                if dominant_cx < 0.40 or dominant_cx > 0.60:
                    offcenter_samples += 1
            for _, _, _, nx, ny, nw, nh in boxes[:2]:
                if (
                    nx <= 0.006
                    or ny <= 0.006
                    or nx + nw >= 0.994
                    or ny + nh >= 0.994
                ):
                    clipped_samples += 1
                    break
        cap.release()

        if face_samples >= 3 and clipped_samples / face_samples >= 0.60:
            if allow_source_edge_warning:
                notes.append(
                    "PASS 3 WARNING: face still touches source/output edge after safe-framing retry; delivery allowed because the retry already widened the camera crop"
                )
            else:
                return False, [
                    "PASS 3 failed: speaker face repeatedly cut by frame edge"
                ]
        if single_face_samples >= 3 and offcenter_samples / single_face_samples >= 0.50:
            ratio = offcenter_samples / max(1, single_face_samples)
            if allow_source_edge_warning:
                # A forced face-centre retry has already run at this point. Do not
                # throw away an otherwise valid render because one detector sees a
                # profile/partial face slightly outside the QA band. The rescue
                # track itself is built from dense source-face detections.
                notes.append(
                    f"PASS 3 WARNING: {offcenter_samples}/{single_face_samples} single-speaker samples "
                    f"still measured off-centre after forced face-centre retry ({ratio:.0%}); delivery allowed"
                )
            else:
                return False, [
                    f"PASS 3 failed: speaker repeatedly off-centre after tracking ({offcenter_samples}/{single_face_samples} single-speaker samples)"
                ]
        centre_note = ""
        if single_face_samples:
            centre_note = (
                f", centre 45-55%={center_45_55_samples}/{single_face_samples} "
                f"({center_45_55_samples / max(1, single_face_samples):.0%})"
            )
        notes.append(
            "PASS 3 OK: final face framing reviewed"
            + centre_note
            + (
                f" ({multi_face_samples} multi-face samples)"
                if multi_face_samples
                else ""
            )
        )
    except Exception as exc:
        notes.append(
            f"PASS 3 WARNING: detector unavailable ({str(exc)[:100]})"
        )

    return True, notes


def _purge_expired_upscale_jobs() -> None:
    now = time.time()
    with UPSCALE_JOB_LOCK:
        expired = [
            key for key, value in UPSCALE_JOBS.items()
            if now - float(value.get("created_at") or 0) > UPSCALE_RESULT_TTL_SECONDS
        ]
        for key in expired:
            value = UPSCALE_JOBS.pop(key, None) or {}
            cleanup_values = [value.get("path")]
            extra = value.get("extra") or {}
            cleanup_values.append(extra.get("source_path"))

            # A Re-edit result can share the same preserved clean source with
            # its parent result. Do not delete a path still referenced by any
            # remaining live result token.
            referenced_paths = set()
            for other in UPSCALE_JOBS.values():
                referenced_paths.add(str(other.get("path") or ""))
                referenced_paths.add(
                    str((other.get("extra") or {}).get("source_path") or "")
                )

            for path in cleanup_values:
                if path and str(path) not in referenced_paths:
                    try:
                        Path(str(path)).unlink(missing_ok=True)
                    except OSError:
                        pass


def create_clean_reedit_source(
    source_path: Path,
    clip_start: float,
    clip_end: float,
    cancel_event: threading.Event,
) -> Path:
    """Create a clean trimmed source kept specifically for later Re-edit."""
    duration = max(0.1, clip_end - clip_start)
    output = DOWNLOAD_DIR / f"reedit_source_{uuid.uuid4().hex[:12]}.mp4"
    temp_output = output.with_suffix(".part.mp4")
    temp_output.unlink(missing_ok=True)
    run_process(
        [
            "ffmpeg", "-y",
            "-hide_banner", "-loglevel", "warning",
            "-ss", f"{clip_start:.3f}",
            "-t", f"{duration:.3f}",
            "-i", str(source_path),
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(temp_output),
        ],
        cancel_event=cancel_event,
        error_prefix="Could not preserve clean Re-edit source",
        timeout_seconds=min(900, max(180, int(duration * 10 + 120))),
    )
    # Never register a half-written/corrupt source for the Re-edit button.
    try:
        media = probe_media(temp_output)
        if float(media.get("duration") or 0) <= 0:
            raise RuntimeError("preserved Re-edit source has no readable duration")
        if int(media.get("width") or 0) <= 0 or int(media.get("height") or 0) <= 0:
            raise RuntimeError("preserved Re-edit source has no readable video stream")
        temp_output.replace(output)
    except Exception:
        temp_output.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        raise
    return output


def relative_clip_paragraphs(
    paragraphs: List["TranscriptParagraph"],
    clip_start: float,
    clip_end: float,
) -> List["TranscriptParagraph"]:
    output: List[TranscriptParagraph] = []
    duration = max(0.1, clip_end - clip_start)
    for paragraph in paragraphs:
        if paragraph.end <= clip_start or paragraph.start >= clip_end:
            continue
        start_value = max(0.0, paragraph.start - clip_start)
        end_value = min(duration, paragraph.end - clip_start)
        if end_value <= start_value:
            continue
        output.append(
            TranscriptParagraph(
                start=start_value,
                end=end_value,
                text=paragraph.text,
            )
        )
    return output


def register_upscale_job(
    *,
    user_id: int,
    chat_id: int,
    path: Path,
    caption: str,
    title: str,
    hashtags: str,
    viral_score: int,
    source_type: str,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    _purge_expired_upscale_jobs()
    token = uuid.uuid4().hex[:12]
    with UPSCALE_JOB_LOCK:
        UPSCALE_JOBS[token] = {
            "user_id": int(user_id),
            "chat_id": int(chat_id),
            "path": str(path),
            "caption": str(caption),
            "title": str(title),
            "hashtags": str(hashtags),
            "viral_score": int(viral_score),
            "source_type": str(source_type),
            "message_id": 0,
            "created_at": time.time(),
            "extra": dict(extra or {}),
        }
    return token


def upscale_button(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎚 Change Quality", callback_data=f"resultq:{token}"),
                InlineKeyboardButton("✏️ Re-edit", callback_data=f"reedit:{token}"),
            ],
            [
                InlineKeyboardButton("📝 Transcript", callback_data=f"resulttrans:{token}"),
            ],
        ]
    )

def result_quality_menu(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1080p", callback_data=f"resultqset:{token}:1080"),
            InlineKeyboardButton("720p", callback_data=f"resultqset:{token}:720"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"resultqset:{token}:back")],
    ])

async def handle_result_quality_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query=update.callback_query
    user=update.effective_user
    if not query or not query.message or not user:
        return
    token=(query.data or "").split(":",1)[-1]
    with UPSCALE_JOB_LOCK:
        job=dict(UPSCALE_JOBS.get(token) or {})
    if not job or int(job.get("user_id") or 0)!=user.id:
        await query.answer("This result expired.",show_alert=True)
        return
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=result_quality_menu(token))

async def handle_result_quality_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query=update.callback_query
    user=update.effective_user
    chat=update.effective_chat
    if not query or not query.message or not user or not chat:
        return
    parts=(query.data or "").split(":")
    if len(parts)!=3:
        return
    _,token,quality=parts
    with UPSCALE_JOB_LOCK:
        job=dict(UPSCALE_JOBS.get(token) or {})
    if not job or int(job.get("user_id") or 0)!=user.id:
        await query.answer("This result expired.",show_alert=True)
        return
    if quality=="back":
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=upscale_button(token))
        return
    if quality not in {"1080","720"}:
        await query.answer("Unsupported quality.",show_alert=True)
        return

    source=Path(str(job.get("path") or ""))
    try:
        source_media=await asyncio.to_thread(probe_media,source)
        if not source.is_file() or source.stat().st_size < 1024 or float(source_media.get("duration") or 0)<=0:
            raise RuntimeError("source unavailable")
    except Exception:
        await query.answer("Source result expired or is incomplete.",show_alert=True)
        return

    await query.answer(f"Creating {quality}p")
    status=await context.bot.send_message(chat.id,f"🎚 Creating {quality}p version...")
    output=DOWNLOAD_DIR/f"quality_{quality}_{uuid.uuid4().hex[:8]}.mp4"
    cancel=threading.Event()
    target_w,target_h=(1080,1920) if quality=="1080" else (720,1280)

    commands=[
        [
            "ffmpeg","-y","-hide_banner","-loglevel","warning",
            "-i",str(source),
            "-map","0:v:0","-map","0:a:0?",
            "-vf",(
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:"
                "flags=lanczos,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,"
                "setsar=1"
            ),
            "-r","60","-vsync","cfr",
            "-c:v","libx264","-preset","medium","-crf","18",
            "-profile:v","high","-pix_fmt","yuv420p",
            "-c:a","aac","-b:a","192k",
            "-movflags","+faststart",str(output),
        ],
        [
            "ffmpeg","-y","-hide_banner","-loglevel","warning",
            "-i",str(source),
            "-map","0:v:0","-map","0:a:0?",
            "-vf",f"scale={target_w}:{target_h}:flags=lanczos,setsar=1",
            "-c:v","libx264","-preset","veryfast","-crf","19",
            "-pix_fmt","yuv420p","-c:a","aac","-b:a","160k",
            "-movflags","+faststart",str(output),
        ],
    ]

    try:
        last_error=None
        for attempt,command in enumerate(commands,1):
            output.unlink(missing_ok=True)
            try:
                await asyncio.to_thread(
                    run_process,
                    command,
                    cancel,
                    f"Quality conversion attempt {attempt} failed",
                    3600,
                )
                converted=await asyncio.to_thread(probe_media,output)
                if (
                    not output.exists()
                    or output.stat().st_size < 1024
                    or int(converted.get("width") or 0)<=0
                    or int(converted.get("height") or 0)<=0
                    or float(converted.get("duration") or 0)<=0
                ):
                    raise RuntimeError("Converted video did not pass media validation")
                break
            except Exception as exc:
                last_error=exc
        else:
            raise RuntimeError(str(last_error or "quality conversion failed"))

        media=await asyncio.to_thread(probe_media,output)
        new_token=register_upscale_job(
            user_id=user.id,chat_id=chat.id,path=output,
            caption=str(job.get("caption") or ""),
            title=str(job.get("title") or "Video"),
            hashtags=str(job.get("hashtags") or ""),
            viral_score=int(job.get("viral_score") or 0),
            source_type=str(job.get("source_type") or "quality"),
            extra=dict(job.get("extra") or {}),
        )
        _,sent=await send_video_with_upscale_button(
            context,chat.id,output,
            f"✅ {quality}p version\n{str(job.get('caption') or '')}"[:1000],
            new_token,
            optional_int(media.get("duration")),
            optional_int(media.get("width")),
            optional_int(media.get("height")),
        )
        with UPSCALE_JOB_LOCK:
            if new_token in UPSCALE_JOBS:
                UPSCALE_JOBS[new_token]["message_id"]=int(getattr(sent,"message_id",0) or 0)
        await safe_edit_message(status,f"✅ {quality}p version sent.")
    except Exception as exc:
        logger.exception("Result quality conversion failed")
        await safe_edit_message(
            status,
            "❌ Quality conversion could not complete.\n"
            f"Error: {str(exc)[:260]}"
        )
    finally:
        # Keep successful result alive through UPSCALE_JOBS TTL.
        if not any(str(v.get("path") or "") == str(output) for v in UPSCALE_JOBS.values()):
            await cleanup_manager.schedule([output],minutes=30)


async def send_video_with_upscale_button(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    path: Path,
    caption: str,
    token: str,
    duration: Optional[int] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Tuple[str, Any]:
    markup = upscale_button(token)
    async with UPLOAD_SEMAPHORE:
        await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
        try:
            message = await context.bot.send_video(
                chat_id=chat_id,
                video=path.resolve(),
                filename=path.name,
                caption=caption[:1000],
                duration=duration,
                width=width,
                height=height,
                supports_streaming=True,
                reply_markup=markup,
                read_timeout=7200,
                write_timeout=7200,
                connect_timeout=60,
                pool_timeout=180,
            )
            return "video", message
        except TelegramError as exc:
            if is_too_large_error(exc):
                raise
            logger.warning(
                "Video upload failed, using document fallback with upscale button: %s",
                exc,
            )
            message = await context.bot.send_document(
                chat_id=chat_id,
                document=path.resolve(),
                filename=path.name,
                caption=caption[:1000],
                reply_markup=markup,
                read_timeout=7200,
                write_timeout=7200,
                connect_timeout=60,
                pool_timeout=180,
            )
            return "document", message


def smart_user_upscale(
    input_path: Path,
    cancel_event: threading.Event,
) -> Tuple[Path, bool, str]:
    """
    Smart Pro enhancement selector.

    Engine priority:
    1. Real-ESRGAN NCNN Vulkan when runtime probe succeeds.
    2. OpenCV EDSR x2 for short lower-resolution clips.
    3. OpenCV FSRCNN x2 for longer lower-resolution clips.
    4. High-quality denoise/upscale/sharpen fallback for already-HD material.

    The returned note reports the REAL engine used.
    """
    return reliable_enhance_video(
        input_path,
        cancel_event,
    )


def reedit_menu(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🚫 Remove Video Hook", callback_data=f"reopt:{token}:nohook"),
                InlineKeyboardButton("🚫 Remove Captions", callback_data=f"reopt:{token}:none"),
            ],
            [
                InlineKeyboardButton("🟩 Green Captions", callback_data=f"reopt:{token}:reference_green"),
                InlineKeyboardButton("🟨 Yellow Captions", callback_data=f"reopt:{token}:reference_yellow"),
            ],
            [
                InlineKeyboardButton("🔥 Viral Mix", callback_data=f"reopt:{token}:viral_mix"),
                InlineKeyboardButton("⬅️ Back", callback_data=f"reopt:{token}:back"),
            ],
        ]
    )



def find_result_token_by_message(user_id: int, message_id: int) -> Optional[str]:
    """Find a live generated-result token from the Telegram message being replied to."""
    _purge_expired_upscale_jobs()
    with UPSCALE_JOB_LOCK:
        for token, value in UPSCALE_JOBS.items():
            if (
                int(value.get("user_id") or 0) == int(user_id)
                and int(value.get("message_id") or 0) == int(message_id)
            ):
                return token
    return None


async def ai_reedit_result(
    context: ContextTypes.DEFAULT_TYPE,
    user: Any,
    chat: Any,
    token: str,
    instruction: str,
    style_profile: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """
    AI-guided re-edit through the EXISTING trusted renderer.
    The model can choose only safe existing controls; it cannot invent arbitrary
    FFmpeg/code operations.
    """
    with UPSCALE_JOB_LOCK:
        result_job = dict(UPSCALE_JOBS.get(token) or {})
    if not result_job or int(result_job.get("user_id") or 0) != int(user.id):
        return False, "That edited result has expired."

    extra = result_job.get("extra") or {}
    source_path = Path(str(extra.get("source_path") or ""))
    try:
        media = await asyncio.to_thread(probe_media, source_path)
        if not source_path.is_file() or float(media.get("duration") or 0) <= 0:
            raise RuntimeError("source unavailable")
    except Exception:
        return False, "The clean Re-edit source has expired. Generate the clip again."

    try:
        paragraphs = [
            TranscriptParagraph(float(x["start"]), float(x["end"]), str(x["text"]))
            for x in extra.get("paragraphs", [])
        ]
        words = [
            ClipperWordTiming(float(x["start"]), float(x["end"]), str(x["text"]))
            for x in extra.get("word_timings", [])
        ]
        segment = ViralSegment(**(extra.get("segment") or {}))
    except Exception as exc:
        return False, f"Re-edit metadata is incomplete: {str(exc)[:160]}"

    available_templates = list(CAPTION_TEMPLATES.keys())
    plan: Dict[str, Any] = {}
    try:
        from viralforge.core import llm_json as _vf_llm_json, settings as _vf_settings
        _s = _vf_settings(Path(__file__).resolve().parent / "viralforge")
        plan = await asyncio.to_thread(
            _vf_llm_json,
            _s,
            (
                "You are a human-style podcast video editing planner. Interpret the user's requested "
                "change and map it ONLY to these safe controls: caption_template, hook_action "
                "(keep/remove/replace), hook_text, trim_start_seconds, trim_end_seconds. "
                "Do not request code changes. Keep trims conservative (0-4 seconds each). "
                "If the request is vague, improve readability and pacing without changing meaning. "
                "Return JSON only."
            ),
            {
                "instruction": instruction,
                "current_template": extra.get("caption_template"),
                "available_templates": available_templates,
                "current_hook": segment.hook,
                "transcript": extra.get("transcript_text") or "",
                "style_reference": style_profile or {},
            },
        )
        if not isinstance(plan, dict):
            plan = {}
    except Exception as exc:
        logger.warning("AI re-edit planner degraded: %s", exc)

    template = str(plan.get("caption_template") or extra.get("caption_template") or "reference_green")
    if template not in CAPTION_TEMPLATES:
        template = str(extra.get("caption_template") or "reference_green")
        if template not in CAPTION_TEMPLATES:
            template = "reference_green"

    action = str(plan.get("hook_action") or "keep").lower()
    if "remove hook" in instruction.lower() or "no hook" in instruction.lower():
        action = "remove"
    if action == "remove":
        segment.hook = ""
    elif action == "replace":
        new_hook = _clipper_clean_text(str(plan.get("hook_text") or ""), 90)
        if new_hook:
            segment.hook = new_hook

    duration = float(extra.get("clip_end") or 0) - float(extra.get("clip_start") or 0)
    if duration <= 0:
        duration = float(media.get("duration") or 0)
    trim_start = max(0.0, min(4.0, float(plan.get("trim_start_seconds") or 0)))
    trim_end = max(0.0, min(4.0, float(plan.get("trim_end_seconds") or 0)))
    if duration - trim_start - trim_end < 8:
        trim_start = trim_end = 0.0

    clip_start = trim_start
    clip_end = max(clip_start + 0.1, duration - trim_end)

    # Rebase transcript/word timings if a trim was requested.
    if trim_start or trim_end:
        new_paragraphs: List[TranscriptParagraph] = []
        for x in paragraphs:
            if x.end <= clip_start or x.start >= clip_end:
                continue
            new_paragraphs.append(
                TranscriptParagraph(
                    max(0.0, x.start - clip_start),
                    min(clip_end - clip_start, x.end - clip_start),
                    x.text,
                )
            )
        new_words: List[ClipperWordTiming] = []
        for x in words:
            if x.end <= clip_start or x.start >= clip_end:
                continue
            new_words.append(
                ClipperWordTiming(
                    max(0.0, x.start - clip_start),
                    min(clip_end - clip_start, x.end - clip_start),
                    x.text,
                )
            )
        paragraphs = new_paragraphs or paragraphs
        words = new_words or words

    status = await context.bot.send_message(
        chat.id,
        "🎬 AI Edit is applying your requested changes...\n"
        "🧠 Planning → ✂️ Re-render → ✅ Quality check",
    )
    cancel_event = threading.Event()
    output = None
    cleanup: List[Path] = []
    try:
        output, cleanup, note = await asyncio.to_thread(
            render_auto_clip,
            source_path,
            paragraphs,
            clip_start,
            clip_end,
            segment,
            1,
            "AI-ReEdit",
            template,
            cancel_event,
            VIDEO_EDITOR_MAX_WALLCLOCK_SECONDS,
            words or None,
        )
        ok, qa = await asyncio.to_thread(
            triple_quality_check, output, max(0.1, clip_end - clip_start)
        )
        if not ok and any("PASS 3 failed: speaker face repeatedly cut by frame edge" in item for item in qa):
            await safe_edit_message(status, "🛡 Face-edge QA found a tight crop. Re-rendering once with safe face margins while keeping body-follow motion...")
            try:
                output.unlink(missing_ok=True)
            except Exception:
                pass
            output, retry_cleanup, note = await asyncio.to_thread(
                render_auto_clip,
                source_path, paragraphs, clip_start, clip_end, segment, 1,
                "AI-ReEdit", template, cancel_event, VIDEO_EDITOR_MAX_WALLCLOCK_SECONDS,
                words or None, True,
            )
            cleanup.extend(retry_cleanup)
            ok, qa = await asyncio.to_thread(
                triple_quality_check, output, max(0.1, clip_end - clip_start), True
            )
        if not ok:
            raise RuntimeError("Quality check failed: " + "; ".join(qa))
        output_media = await asyncio.to_thread(probe_media, output)

        new_extra = {
            **extra,
            "clip_start": 0.0,
            "clip_end": max(0.1, clip_end - clip_start),
            "caption_template": template,
            "segment": asdict(segment),
            "paragraphs": [asdict(x) for x in paragraphs],
            "word_timings": [asdict(x) for x in words],
            "ai_edit_instruction": instruction,
            "ai_edit_plan": plan,
        }
        new_token = register_upscale_job(
            user_id=user.id,
            chat_id=chat.id,
            path=output,
            caption=str(result_job.get("caption") or ""),
            title=str(result_job.get("title") or "AI Re-edited Clip"),
            hashtags=str(result_job.get("hashtags") or ""),
            viral_score=int(result_job.get("viral_score") or 0),
            source_type="ai_reedit",
            extra=new_extra,
        )
        _, sent = await send_video_with_upscale_button(
            context,
            chat.id,
            output,
            (
                "✅ AI Edit completed\n\n"
                f"🎯 Requested change: {instruction[:180]}\n"
                f"📝 Captions: {template}\n"
                f"🪝 Hook: {'removed' if not segment.hook else 'kept/updated'}"
            )[:1000],
            new_token,
            optional_int(output_media.get("duration")),
            optional_int(output_media.get("width")),
            optional_int(output_media.get("height")),
        )
        with UPSCALE_JOB_LOCK:
            if new_token in UPSCALE_JOBS:
                UPSCALE_JOBS[new_token]["message_id"] = int(getattr(sent, "message_id", 0) or 0)
        await safe_edit_message(status, "✅ Your requested edit passed quality checks and was sent.")
        return True, "Edit completed."
    except Exception as exc:
        logger.exception("AI guided re-edit failed")
        await safe_edit_message(status, f"❌ AI Edit could not complete: {str(exc)[:260]}")
        return False, str(exc)
    finally:
        await cleanup_manager.schedule(cleanup)


async def ai_edit_external_video(
    context: ContextTypes.DEFAULT_TYPE,
    user: Any,
    chat: Any,
    input_path: Path,
    instruction: str,
    style_profile: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """
    Import ANY replied Telegram video into the existing AI Editor pipeline.

    The source is preserved in downloads so the delivered result still supports
    Re-edit / quality changes. Heavy transcription/rendering work runs in worker
    threads and does not block the Telegram event loop.
    """
    source_path: Optional[Path] = None
    audio_path: Optional[Path] = None
    output_path: Optional[Path] = None
    cleanup: List[Path] = []
    cancel_event = threading.Event()

    try:
        input_path = Path(input_path)
        if not input_path.is_file():
            return False, "The replied video file is unavailable."

        media = await asyncio.to_thread(probe_media, input_path)
        duration = float(media.get("duration") or 0)
        if duration <= 0:
            return False, "The replied video has no readable duration."
        if duration > float(MAX_VIDEO_EDITOR_SECONDS):
            return False, (
                f"The video is longer than the current editor limit "
                f"({format_time(MAX_VIDEO_EDITOR_SECONDS)})."
            )
        if input_path.stat().st_size > MAX_VIDEO_EDITOR_INPUT_BYTES:
            return False, (
                f"The video is larger than the editor input limit "
                f"({human_bytes(MAX_VIDEO_EDITOR_INPUT_BYTES)})."
            )

        suffix = input_path.suffix.lower()
        if suffix not in VIDEO_EXTENSIONS:
            suffix = ".mp4"
        source_path = DOWNLOAD_DIR / (
            f"ai_external_source_{user.id}_{uuid.uuid4().hex[:10]}{suffix}"
        )
        await asyncio.to_thread(shutil.copy2, input_path, source_path)

        status = await context.bot.send_message(
            chat.id,
            "🧠 AI Editor is understanding the video...\n"
            "📝 Transcript → 🎯 Edit plan → 🎬 Render → ✅ Quality check",
        )

        audio_path = DOWNLOAD_DIR / f"ai_external_audio_{uuid.uuid4().hex[:10]}.wav"
        await asyncio.to_thread(
            run_process,
            [
                "ffmpeg", "-y",
                "-hide_banner", "-loglevel", "error",
                "-i", str(source_path),
                "-vn",
                "-ac", "1",
                "-ar", "16000",
                "-c:a", "pcm_s16le",
                str(audio_path),
            ],
            cancel_event,
            "Could not prepare audio for AI editing",
            min(
                VIDEO_EDITOR_MAX_WALLCLOCK_SECONDS,
                max(240, int(duration * 5 + 120)),
            ),
        )

        paragraphs, detected_language, word_timings = await asyncio.to_thread(
            run_editor_transcription,
            audio_path,
            cancel_event,
        )
        full_text = normalize_transcript_text(
            " ".join(x.text for x in paragraphs)
        )
        if not full_text:
            return False, "No usable speech was detected in the replied video."

        available_templates = {
            key: value.get("label", key)
            for key, value in CAPTION_TEMPLATES.items()
        }
        plan: Dict[str, Any] = {}
        try:
            from viralforge.core import (
                llm_json as _vf_llm_json,
                settings as _vf_settings,
            )
            _s = _vf_settings(
                Path(__file__).resolve().parent / "viralforge"
            )
            plan = await asyncio.to_thread(
                _vf_llm_json,
                _s,
                (
                    "You are a human podcast/video editor. Interpret the user's requested "
                    "change and map it to the EXISTING safe renderer. Return JSON only with "
                    "caption_template, hook_action(keep/remove/replace), hook_text, "
                    "trim_start_seconds, trim_end_seconds, editing_intent, notes. "
                    "Choose caption_template only from the supplied templates. "
                    "Do not invent content or request source-code changes. "
                    "Keep trims conservative unless the user explicitly asks otherwise. "
                    "Use the supplied style reference when present."
                ),
                {
                    "instruction": instruction,
                    "duration": duration,
                    "language": detected_language,
                    "transcript": full_text[:16000],
                    "available_templates": available_templates,
                    "style_reference": style_profile or {},
                },
            )
            if not isinstance(plan, dict):
                plan = {}
        except Exception as exc:
            logger.warning(
                "External AI edit planner degraded: %s",
                exc,
            )

        template = str(
            plan.get("caption_template")
            or (
                "creator_clean_large"
                if "creator_clean_large" in CAPTION_TEMPLATES
                else "reference_green"
            )
        )
        if template not in CAPTION_TEMPLATES:
            template = (
                "reference_green"
                if "reference_green" in CAPTION_TEMPLATES
                else next(iter(CAPTION_TEMPLATES))
            )

        hook = generate_editor_hook(full_text)
        hook_action = str(
            plan.get("hook_action") or "keep"
        ).lower()
        low_instruction = instruction.lower()
        if (
            "remove hook" in low_instruction
            or "no hook" in low_instruction
        ):
            hook_action = "remove"
        if hook_action == "remove":
            hook = ""
        elif hook_action == "replace":
            proposed = _clipper_clean_text(
                str(plan.get("hook_text") or ""),
                90,
            )
            if proposed:
                hook = proposed

        trim_start = max(
            0.0,
            min(
                8.0,
                float(plan.get("trim_start_seconds") or 0),
            ),
        )
        trim_end = max(
            0.0,
            min(
                8.0,
                float(plan.get("trim_end_seconds") or 0),
            ),
        )
        if duration - trim_start - trim_end < 8:
            trim_start = 0.0
            trim_end = 0.0

        clip_start = trim_start
        clip_end = duration - trim_end

        # Viral score is informational here; editing instructions remain primary.
        try:
            score, reason = await asyncio.to_thread(
                pro_viral_potential_score,
                full_text,
                source_path,
                clip_start,
                clip_end,
                cancel_event,
                None,
                None,
            )
        except Exception as exc:
            logger.warning(
                "External edit viral scoring fallback: %s",
                exc,
            )
            score, reason = 50, "Edited on user request"

        segment = ViralSegment(
            start=clip_start,
            end=clip_end,
            score=int(score),
            reason=str(reason),
            text=full_text,
            hook=hook,
            caption="",
            hashtags="",
            hook_duration=max(
                5.0,
                VIRALFORGE_FIRST5_SECONDS,
            ),
            deep_meta={
                "source": "ai_external_edit",
                "instruction": instruction,
                "edit_plan": plan,
                "style_reference": style_profile or {},
            },
        )

        # Caption timings must be relative to the rendered clip window.
        render_words: List[ClipperWordTiming] = []
        for word in word_timings:
            if word.end <= clip_start or word.start >= clip_end:
                continue
            render_words.append(
                ClipperWordTiming(
                    max(0.0, word.start - clip_start),
                    min(clip_end - clip_start, word.end - clip_start),
                    word.text,
                )
            )
        render_words = _clean_clipper_word_timings(render_words)

        output_path, render_cleanup, render_note = await asyncio.to_thread(
            render_auto_clip,
            source_path,
            paragraphs,
            clip_start,
            clip_end,
            segment,
            1,
            "AI-Edit",
            template,
            cancel_event,
            VIDEO_EDITOR_MAX_WALLCLOCK_SECONDS,
            render_words or None,
        )
        cleanup.extend(render_cleanup)

        qa_ok, qa_notes = await asyncio.to_thread(
            triple_quality_check,
            output_path,
            max(0.1, clip_end - clip_start),
        )
        if not qa_ok:
            raise RuntimeError(
                "Technical quality check failed: "
                + "; ".join(qa_notes)
            )

        if False and viralforge_qa_render:
            try:
                ai_qa = await asyncio.to_thread(
                    viralforge_qa_render,
                    output_path,
                    {
                        "feature": "AI Reply Video Editor",
                        "instruction": instruction,
                        "duration": clip_end - clip_start,
                        "caption_template": template,
                        "hook": hook,
                        "render_note": render_note,
                        "style_reference": style_profile or {},
                    },
                )
                severe = (
                    list(
                        ai_qa.get(
                            "actionable_high_severity"
                        )
                        or []
                    )
                    if isinstance(ai_qa, dict)
                    else []
                )
                if severe:
                    raise RuntimeError(
                        "AI editing QA found a high-severity issue: "
                        + _vf_text(severe, 260)
                    )
            except RuntimeError:
                raise
            except Exception as exc:
                logger.warning(
                    "External edit AI QA degraded: %s",
                    exc,
                )

        output_media = await asyncio.to_thread(
            probe_media,
            output_path,
        )
        reedit_source = await asyncio.to_thread(
            create_clean_reedit_source,
            source_path,
            clip_start,
            clip_end,
            cancel_event,
        )
        relative_paragraphs = relative_clip_paragraphs(
            paragraphs,
            clip_start,
            clip_end,
        )
        token = register_upscale_job(
            user_id=user.id,
            chat_id=chat.id,
            path=output_path,
            caption="",
            title="AI Edited Video",
            hashtags="",
            viral_score=int(score),
            source_type="ai_external_edit",
            extra={
                "source_path": str(reedit_source.resolve()),
                "clip_start": 0.0,
                "clip_end": max(0.1, clip_end - clip_start),
                "paragraphs": [
                    asdict(x)
                    for x in relative_paragraphs
                ],
                "word_timings": [
                    asdict(x)
                    for x in render_words
                ],
                "segment": asdict(segment),
                "caption_template": template,
                "transcript_text": full_text,
                "ai_edit_instruction": instruction,
                "ai_edit_plan": plan,
            },
        )
        _, sent = await send_video_with_upscale_button(
            context,
            chat.id,
            output_path,
            (
                "✅ AI Video Edit completed\n\n"
                f"🎯 Change: {instruction[:220]}\n"
                f"📝 Captions: {CAPTION_TEMPLATES[template]['label']}\n"
                f"🪝 Video hook: {'Off' if not hook else 'On'}\n"
                f"🔥 Viral Potential: {int(score)}/100"
            )[:1000],
            token,
            optional_int(output_media.get("duration")),
            optional_int(output_media.get("width")),
            optional_int(output_media.get("height")),
        )
        with UPSCALE_JOB_LOCK:
            if token in UPSCALE_JOBS:
                UPSCALE_JOBS[token]["message_id"] = int(
                    getattr(sent, "message_id", 0)
                    or 0
                )

        try:
            await status.edit_text(
                "✅ AI Editor finished. The new video passed quality checks."
            )
        except Exception:
            pass

        # source_path stays intentionally protected by the result token.
        if audio_path:
            audio_path.unlink(missing_ok=True)
        return True, "Edit completed."

    except JobCancelled:
        return False, "Editing was cancelled."
    except Exception as exc:
        logger.exception(
            "AI external reply edit failed"
        )
        return False, str(exc)
    finally:
        if audio_path:
            audio_path.unlink(missing_ok=True)
        # Keep successful source/output when referenced by a result token.
        registered_paths = {
            str(value.get("path") or "")
            for value in UPSCALE_JOBS.values()
        }
        protected_sources = {
            str(
                (value.get("extra") or {}).get(
                    "source_path"
                )
                or ""
            )
            for value in UPSCALE_JOBS.values()
        }
        await cleanup_manager.schedule([
            path
            for path in [
                source_path,
                output_path,
                *cleanup,
            ]
            if (
                path is not None
                and str(path) not in registered_paths
                and str(path) not in protected_sources
            )
        ])


async def handle_reedit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not query.message or not user:
        return
    token = (query.data or "").split(":", 1)[-1]
    with UPSCALE_JOB_LOCK:
        job = dict(UPSCALE_JOBS.get(token) or {})
    if not job or int(job.get("user_id") or 0) != user.id:
        await query.answer("This result expired.", show_alert=True)
        return
    await query.answer()
    try:
        await query.edit_message_reply_markup(
            reply_markup=reedit_menu(token),
        )
    except TelegramError:
        await context.bot.edit_message_reply_markup(
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            reply_markup=reedit_menu(token),
        )


async def handle_result_transcript_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not query.message or not user:
        return

    token = (query.data or "").split(":", 1)[-1]
    with UPSCALE_JOB_LOCK:
        job = dict(UPSCALE_JOBS.get(token) or {})

    if not job or int(job.get("user_id") or 0) != user.id:
        await query.answer("This result expired.", show_alert=True)
        return

    await query.answer("Creating English + Urdu transcript")
    status = await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="📝 Creating bilingual English + Urdu transcript...",
    )

    extra = job.get("extra") or {}
    text_value = normalize_transcript_text(
        str(extra.get("transcript_text") or "")
    )
    raw_paragraphs = extra.get("paragraphs") or []

    paragraphs: List[TranscriptParagraph] = []
    for item in raw_paragraphs:
        try:
            paragraphs.append(
                TranscriptParagraph(
                    float(item["start"]),
                    float(item["end"]),
                    str(item["text"]),
                )
            )
        except Exception:
            continue

    if not paragraphs and text_value:
        paragraphs = [
            TranscriptParagraph(
                0.0,
                float(extra.get("clip_end") or 1.0),
                text_value,
            )
        ]

    if not paragraphs:
        await safe_edit_message(
            status,
            "Transcript metadata is unavailable for this result.",
        )
        return

    cancel_event = threading.Event()
    path = DOWNLOAD_DIR / f"bilingual_transcript_{token}.txt"

    try:
        urdu_paragraphs = await asyncio.to_thread(
            translate_paragraphs_to_urdu,
            paragraphs,
            cancel_event,
        )
        lines = [
            "ENGLISH + URDU TRANSCRIPT",
            "=" * 44,
            "",
        ]
        for english, urdu in zip(paragraphs, urdu_paragraphs):
            lines.extend(
                [
                    (
                        f"[{_clipper_format_timestamp(english.start)} - "
                        f"{_clipper_format_timestamp(english.end)}]"
                    ),
                    f"English: {english.text}",
                    f"Urdu: {urdu.text}",
                    "",
                ]
            )
        path.write_text("\n".join(lines), encoding="utf-8")
        await send_local_document(
            context,
            query.message.chat_id,
            path,
            "📝 English + Urdu Clip Transcript",
        )
        await safe_edit_message(
            status,
            "✅ English + Urdu transcript sent.",
        )
    except Exception as exc:
        logger.exception("Bilingual result transcript failed")
        await safe_edit_message(
            status,
            f"Transcript error: {str(exc)[:280]}",
        )
    finally:
        path.unlink(missing_ok=True)


async def handle_reedit_option_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if not query or not query.message or not user or not chat:
        return
    parts = (query.data or "").split(":")
    if len(parts) != 3:
        return
    _, token, option = parts
    with UPSCALE_JOB_LOCK:
        result_job = dict(UPSCALE_JOBS.get(token) or {})
    if not result_job or int(result_job.get("user_id") or 0) != user.id:
        await query.answer("This result expired.", show_alert=True)
        return
    if option == "back":
        await query.answer()
        try:
            await query.edit_message_reply_markup(
                reply_markup=upscale_button(token),
            )
        except TelegramError:
            await context.bot.edit_message_reply_markup(
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                reply_markup=upscale_button(token),
            )
        return

    extra = result_job.get("extra") or {}
    source_path = Path(str(extra.get("source_path") or ""))
    try:
        source_media = await asyncio.to_thread(probe_media, source_path)
        source_ok = (
            source_path.is_file()
            and source_path.stat().st_size > 1024
            and float(source_media.get("duration") or 0) > 0
            and int(source_media.get("width") or 0) > 0
            and int(source_media.get("height") or 0) > 0
        )
    except Exception:
        source_ok = False
    if not source_ok:
        await query.answer(
            "Re-edit source expired or is incomplete. Generate the clip again; new clips now keep a verified clean source.",
            show_alert=True,
        )
        return

    try:
        paragraphs = [
            TranscriptParagraph(float(item["start"]), float(item["end"]), str(item["text"]))
            for item in extra.get("paragraphs", [])
        ]
        words = [
            ClipperWordTiming(float(item["start"]), float(item["end"]), str(item["text"]))
            for item in extra.get("word_timings", [])
        ]
        segment = ViralSegment(**(extra.get("segment") or {}))
        clip_start = float(extra.get("clip_start") or 0)
        clip_end = float(extra.get("clip_end") or 0)
    except Exception:
        await query.answer("Re-edit metadata is invalid.", show_alert=True)
        return

    template = str(extra.get("caption_template") or "reference_green")
    if option == "nohook":
        segment.hook = ""
    elif option in CAPTION_TEMPLATES:
        template = option
    else:
        await query.answer("Unsupported re-edit option.", show_alert=True)
        return

    await query.answer("Re-edit started")
    status = await context.bot.send_message(chat.id, "✏️ Re-rendering selected clip...")
    cancel_event = threading.Event()
    output = None
    cleanup = []
    try:
        output, cleanup, note = await asyncio.to_thread(
            render_auto_clip,
            source_path,
            paragraphs,
            clip_start,
            clip_end,
            segment,
            1,
            "ReEdit",
            template,
            cancel_event,
            VIDEO_EDITOR_MAX_WALLCLOCK_SECONDS,
            words or None,
        )
        ok, qa = await asyncio.to_thread(
            triple_quality_check, output, max(0.1, clip_end - clip_start)
        )
        if not ok and any("PASS 3 failed: speaker face repeatedly cut by frame edge" in item for item in qa):
            await safe_edit_message(status, "🛡 Face-edge QA found a tight crop. Re-rendering once with safe face margins while keeping body-follow motion...")
            try:
                output.unlink(missing_ok=True)
            except Exception:
                pass
            output, retry_cleanup, note = await asyncio.to_thread(
                render_auto_clip,
                source_path, paragraphs, clip_start, clip_end, segment, 1,
                "ReEdit", template, cancel_event, VIDEO_EDITOR_MAX_WALLCLOCK_SECONDS,
                words or None, True,
            )
            cleanup.extend(retry_cleanup)
            ok, qa = await asyncio.to_thread(
                triple_quality_check, output, max(0.1, clip_end - clip_start), True
            )
        if not ok:
            raise RuntimeError("Quality check failed: " + "; ".join(qa))
        media = await asyncio.to_thread(probe_media, output)
        new_token = register_upscale_job(
            user_id=user.id,
            chat_id=chat.id,
            path=output,
            caption=str(result_job.get("caption") or ""),
            title=str(result_job.get("title") or "Re-edited Clip"),
            hashtags=str(result_job.get("hashtags") or ""),
            viral_score=int(result_job.get("viral_score") or 0),
            source_type=str(result_job.get("source_type") or "reedit"),
            extra={**extra, "caption_template": template, "segment": asdict(segment)},
        )
        await send_video_with_upscale_button(
            context, chat.id, output,
            (
                f"✏️ Re-edited: {result_job.get('title') or 'Clip'}\n"
                f"🔥 Viral Potential: {int(result_job.get('viral_score') or 0)}/100\n"
                f"{result_job.get('hashtags') or ''}"
            )[:1000],
            new_token,
            optional_int(media.get("duration")),
            optional_int(media.get("width")),
            optional_int(media.get("height")),
        )
        await safe_edit_message(status, "✅ Re-edit passed 3-stage QA and was sent.")
    except Exception as exc:
        logger.exception("Re-edit failed")
        await safe_edit_message(status, f"Re-edit error: {str(exc)[:300]}")
    finally:
        await cleanup_manager.schedule(cleanup)


async def handle_upscale_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if not query or not query.message or not user or not chat:
        return

    try:
        await query.answer("Upscale started")
    except TelegramError:
        pass

    token = (query.data or "").split(":", 1)[-1]
    _purge_expired_upscale_jobs()

    with UPSCALE_JOB_LOCK:
        job = dict(UPSCALE_JOBS.get(token) or {})

    if not job:
        await safe_edit_message(
            query.message,
            "This upscale option expired. Generate the clip again.",
        )
        return
    if int(job.get("user_id") or 0) != user.id:
        await query.answer(
            "Only the user who generated this clip can upscale it.",
            show_alert=True,
        )
        return

    input_path = Path(str(job.get("path") or ""))
    if not input_path.exists():
        await safe_edit_message(
            query.message,
            "The source clip expired. Generate it again.",
        )
        return

    key = f"upscale:{token}"
    started, reason, cancel_event = active_jobs.try_start(
        user.id,
        key,
        "upscale",
        is_admin=is_owner(update),
        title=str(job.get("title") or "Upscale"),
        username=str(user.username or ""),
    )
    if not started or cancel_event is None:
        await query.answer(reason or "Another task is active.", show_alert=True)
        return

    # User requested replacement: delete the first delivered clip before
    # sending the upscaled version.
    try:
        await query.message.delete()
    except TelegramError:
        try:
            await context.bot.delete_message(
                chat_id=chat.id,
                message_id=query.message.message_id,
            )
        except TelegramError:
            pass

    status = await context.bot.send_message(
        chat_id=chat.id,
        text="✨ Upscaling selected clip...\nThe original delivered clip was removed.",
        reply_markup=stop_keyboard(),
    )

    async def upscale_progress(message: str) -> None:
        active_jobs.update(key, stage=message)
        await safe_edit_message(
            status,
            message,
            reply_markup=stop_keyboard(),
        )

    output: Optional[Path] = None
    try:
        async with cancellable_slot(
            AI_ENHANCE_SEMAPHORE,
            cancel_event,
        ):
            output, used_ai, note = await run_enhance_with_heartbeat(
                smart_user_upscale,
                (input_path, cancel_event),
                upscale_progress,
                (
                    "🤖 AI/HQ upscale is running."
                    if ENABLE_REALESRGAN_GPU
                    else "✨ Adaptive HQ upscale is running."
                ),
            )

        media = await asyncio.to_thread(probe_media, output)
        title = str(job.get("title") or "Edited Clip")
        hashtags = str(job.get("hashtags") or "")
        score = int(job.get("viral_score") or 0)

        final_caption = (
            f"🎬 {title}\n"
            f"🔥 Viral Potential: {score}/100\n\n"
            f"{hashtags}\n\n"
            f"✨ Upscale: {note}"
        )[:1000]

        await send_video_with_fallback(
            context,
            chat.id,
            output,
            final_caption,
            optional_int(media.get("duration")),
            optional_int(media.get("width")),
            optional_int(media.get("height")),
        )

        try:
            await status.delete()
        except TelegramError:
            pass

        with UPSCALE_JOB_LOCK:
            UPSCALE_JOBS.pop(token, None)
        input_path.unlink(missing_ok=True)

    except JobCancelled:
        await safe_edit_message(status, "⛔ Upscale stopped.")
    except Exception as exc:
        logger.exception("Post-delivery upscale failed")
        await safe_edit_message(
            status,
            "Upscale could not complete.\n"
            f"Error: {str(exc)[:300]}",
        )
    finally:
        active_jobs.finish(user.id, key)
        if output is not None:
            await cleanup_manager.schedule([output])


# =============================================================================
# Public command and menu handlers
# =============================================================================

async def command_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    raw = update.message.text or update.message.caption or ""
    text = raw.split(None, 1)[1] if len(raw.split(None, 1)) > 1 else ""
    # Reply-broadcast preserves the replied message formatting too.
    if not text and update.message.reply_to_message:
        text = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
    if not text:
        await update.message.reply_text("Usage: /broadcast your message")
        return
    data = _read_user_history()
    user_ids = [int(k) for k in (data.get("users") or {}) if str(k).isdigit()]
    sent = failed = 0
    for uid in user_ids:
        try:
            await context.bot.send_message(uid, text)
            sent += 1
        except TelegramError:
            failed += 1
        await asyncio.sleep(0.04)
    await update.message.reply_text(f"📡 Broadcast complete\nSent: {sent}\nFailed: {failed}")


async def _notify_feature_failure(
    context: ContextTypes.DEFAULT_TYPE,
    user: Any,
    feature: str,
    error: Exception,
    stage: str = "",
    traceback_text: str = "",
) -> None:
    """Notify the owner about a feature failure; never modify source code automatically."""
    if OWNER_ID <= 0:
        return
    try:
        username = f"@{getattr(user, 'username', '')}" if getattr(user, "username", None) else "-"
        trace_value = str(traceback_text or "").strip()
        # Extract bot.py line numbers without depending on any external repair service.
        trace_lines = [int(v) for v in re.findall(r'File "[^"]*bot\.py", line (\d+)', trace_value)]
        location = ", ".join(f"bot.py:{line}" for line in trace_lines[-3:]) or "unavailable"
        text = (
            "🛡 FEATURE GUARDIAN ALERT\n\n"
            f"Feature: {feature}\n"
            f"Stage: {stage or 'processing'}\n"
            f"User: {getattr(user, 'id', '-')} {username}\n"
            f"Error: {type(error).__name__}: {str(error)[:1200]}\n"
            f"Trace location: {location}\n\n"
            "Automatic retries/fallbacks were attempted where available. "
            "Source code was not modified automatically."
        )
        await context.bot.send_message(OWNER_ID, text[:4090])
    except Exception:
        logger.exception("Feature guardian could not notify owner")


async def _send_support_to_owner(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or OWNER_ID <= 0:
        return False
    clean = re.sub(r"\s+", " ", str(text or "")).strip()[:SUPPORT_MAX_MESSAGE_CHARS]
    if not clean:
        return False
    username = f"@{user.username}" if user.username else "-"
    payload = (
        "🆘 USER SUPPORT REPORT\n\n"
        f"Name: {user.full_name}\n"
        f"Username: {username}\n"
        f"User ID: {user.id}\n"
        f"Chat ID: {chat.id}\n\n"
        f"Problem:\n{clean}"
    )
    try:
        await context.bot.send_message(OWNER_ID, payload[:4090])
        if update.message and update.message.reply_to_message:
            try:
                await update.message.reply_to_message.forward(OWNER_ID)
            except TelegramError:
                pass
        return True
    except TelegramError:
        logger.exception("Could not forward support report to owner")
        return False


async def command_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    text = " ".join(context.args or []).strip()
    if not text and update.message.reply_to_message:
        text = update.message.reply_to_message.text or update.message.reply_to_message.caption or "Forwarded message/media issue"
    if not text:
        context.user_data["mode"] = "support_feedback_wait"
        await update.message.reply_text(
            "Apna masla next message mein detail se bhejein. Main pehle basic checks note karunga; "
            f"phir report seedha admin @{OWNER_USERNAME} ko chali jayegi."
        )
        return
    sent = await _send_support_to_owner(update, context, text)
    await update.message.reply_text(
        ("✅ Report admin ko send ho gayi. " if sent else "❌ Report send nahi ho saki. ")
        + f"Admin: @{OWNER_USERNAME}"
    )


PUBLIC_BOT_STATE_FILE = AUTH_DIR / "public_bot_link.json"
PUBLIC_BOT_STATE_LOCK = threading.RLock()


def _read_public_bot_username() -> str:
    with PUBLIC_BOT_STATE_LOCK:
        try:
            if not PUBLIC_BOT_STATE_FILE.exists():
                return ""
            data = json.loads(PUBLIC_BOT_STATE_FILE.read_text(encoding="utf-8"))
            return re.sub(r"[^A-Za-z0-9_]", "", str(data.get("username") or "").lstrip("@"))[:32]
        except Exception:
            return ""


def _write_public_bot_username(username: str) -> None:
    username = re.sub(r"[^A-Za-z0-9_]", "", str(username or "").lstrip("@"))[:32]
    with PUBLIC_BOT_STATE_LOCK:
        PUBLIC_BOT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        PUBLIC_BOT_STATE_FILE.write_text(json.dumps({"username": username}), encoding="utf-8")
        try:
            PUBLIC_BOT_STATE_FILE.chmod(0o600)
        except Exception:
            pass


async def command_addbot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not is_owner(update):
        return
    raw = " ".join(context.args or []).strip()
    if not raw:
        _write_public_bot_username("")
        await update.message.reply_text("Public bot link cleared. /start will show Private Bot.")
        return
    username = re.sub(r"[^A-Za-z0-9_]", "", raw.lstrip("@"))[:32]
    if len(username) < 5:
        await update.message.reply_text("Use: /addbot example_bot  (or /addbot with no name to clear)")
        return
    _write_public_bot_username(username)
    await update.message.reply_text(f"✅ Public bot set: @{username}")


async def _send_public_bot_promo(message: Any) -> None:
    username = _read_public_bot_username()
    if username:
        await message.reply_text(
            "You Can Use Public Bot",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🤖 Open Public Bot", url=f"https://t.me/{username}")]]),
        )
    else:
        await message.reply_text("🔒 Private Bot")


async def command_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message:
        return
    if not is_owner(update):
        await _send_public_bot_promo(update.message)
        if PERSONAL_EDITING_STUDIO:
            return
    context.user_data.pop("mode", None)
    await send_main_menu(update.message, True)


async def command_stop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message or not update.effective_user:
        return
    count = active_jobs.cancel_user(update.effective_user.id)
    if count:
        await update.message.reply_text(
            "Stop requested. The active task will close safely."
        )
    else:
        await update.message.reply_text("You do not have an active task.")


def _compact_stage(value: str, limit: int = 92) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


async def build_live_processing_text() -> str:
    import psutil

    rows = active_jobs.snapshot()
    counts = active_jobs.counts_by_kind()
    cpu = await asyncio.to_thread(psutil.cpu_percent, 0.25)
    memory = psutil.virtual_memory()
    disk = shutil.disk_usage(DOWNLOAD_DIR)

    lines = [
        "📡 Live Processing Dashboard",
        "",
        f"Active jobs: {len(rows)}",
        "By type: " + (
            ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
            if counts else "none"
        ),
        f"CPU: {cpu:.0f}%",
        f"RAM: {memory.percent:.0f}% "
        f"({human_bytes(memory.used)} / {human_bytes(memory.total)})",
        f"Disk free: {human_bytes(disk.free)}",
        "",
    ]

    if not rows:
        lines.append("No jobs are currently processing.")
        return "\n".join(lines)

    now = time.time()
    for index, row in enumerate(rows[:ADMIN_DASHBOARD_MAX_ROWS], 1):
        elapsed = format_time(now - float(row["started_at"]))
        username = (
            f"@{row['username']}"
            if row.get("username")
            else f"ID {row['user_id']}"
        )
        title = _compact_stage(row.get("title") or row["job_key"], 58)
        stage = _compact_stage(row.get("stage") or "Processing", 100)
        lines.extend(
            [
                f"{index}. {row['kind'].title()} | {elapsed} | {username}",
                f"   {title}",
                f"   {stage}",
            ]
        )

    if len(rows) > ADMIN_DASHBOARD_MAX_ROWS:
        lines.append(
            f"\n+ {len(rows) - ADMIN_DASHBOARD_MAX_ROWS} more active jobs"
        )
    return "\n".join(lines)


ACTIVITY_LOG_PATH = AUTH_DIR / "activity.jsonl"
ACTIVITY_LOG_LOCK = threading.Lock()

def record_user_activity(user: Any, action: str, detail: str = "") -> None:
    """Durable compact audit used only for the owner's /last10 command."""
    if not user:
        return
    row = {
        "ts": int(time.time()),
        "user_id": int(getattr(user, "id", 0) or 0),
        "username": str(getattr(user, "username", "") or ""),
        "name": str(getattr(user, "full_name", "") or ""),
        "action": str(action or "unknown")[:80],
        "detail": str(detail or "")[:240],
    }
    try:
        ACTIVITY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with ACTIVITY_LOG_LOCK:
            with ACTIVITY_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("Could not record user activity")


async def command_last10(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    try:
        raw_lines = (
            ACTIVITY_LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
            if ACTIVITY_LOG_PATH.exists() else []
        )
        parsed = []
        for line in raw_lines[-1000:]:
            try:
                parsed.append(json.loads(line))
            except Exception:
                continue
        latest_by_user = {}
        for row in parsed:
            uid = int(row.get("user_id") or 0)
            if uid:
                latest_by_user[uid] = row
        rows = sorted(
            latest_by_user.values(),
            key=lambda item: int(item.get("ts") or 0),
            reverse=True,
        )[:10]
    except Exception as exc:
        await update.message.reply_text(f"Activity log error: {str(exc)[:200]}")
        return
    if not rows:
        await update.message.reply_text("No user activity has been recorded yet.")
        return
    lines = ["👥 Last 10 users — latest bot action", ""]
    for index, row in enumerate(rows, 1):
        username = f"@{row.get('username')}" if row.get("username") else f"ID {row.get('user_id')}"
        action = row.get("action") or "unknown"
        detail = row.get("detail") or ""
        lines.append(f"{index}. {username} — {action}" + (f"\n   {detail}" if detail else ""))
    await update.message.reply_text("\n".join(lines)[:4090])


async def command_live(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    await update.message.reply_text(await build_live_processing_text())


async def command_stopall(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    count = active_jobs.cancel_all()
    await update.message.reply_text(
        f"Stop requested for {count} active task(s)."
        if count
        else "No active tasks are running."
    )


async def command_stopuser(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: /stopuser TELEGRAM_USER_ID"
        )
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("The Telegram user ID must be numeric.")
        return
    count = active_jobs.cancel_user(target_id)
    await update.message.reply_text(
        f"Stop requested for {count} task(s) belonging to user {target_id}."
        if count
        else f"User {target_id} has no active tasks."
    )


async def handle_stop_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not query.message or not user:
        return
    count = active_jobs.cancel_user(user.id)
    await query.answer(
        "Stop requested." if count else "No active task.",
        show_alert=False,
    )
    if count:
        await safe_edit_message(
            query.message,
            "Stopping the active task safely...",
        )


async def command_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "How to use the bot\n\n"
        "✂️ AI Clipper: choose Quick/Deep mode, send a YouTube link or supported source, then choose clip duration.\n\n"
        "🎬 AI Video Editor: use a file, file + timestamp, or YouTube + timestamp workflow.\n\n"
        "🎞 Master Editor: full file, file + timestamp, or YouTube + timestamp; automatic speaker framing, layouts and captions.\n\n"
        "⛔ Use /stop to cancel an active normal job. Master Editor also has its own Stop Master Job button.\n\n"
        f"{owner_footer()}"
    )


def status_text(admin: bool) -> str:
    free = shutil.disk_usage(DOWNLOAD_DIR).free
    kind_counts = active_jobs.counts_by_kind()
    kind_summary = (
        ", ".join(f"{key}={value}" for key, value in sorted(kind_counts.items()))
        if kind_counts else "none"
    )
    text = (
        "📊 Bot Status\n\n"
        f"Active tasks: {active_jobs.active_count()} ({kind_summary})\n"
        f"Download workers: {MAX_CONCURRENT_DOWNLOADS}\n"
        f"YTDLP fragments: {YTDLP_CONCURRENT_FRAGMENTS}\n"
        f"HTTP chunk: {YTDLP_HTTP_CHUNK_SIZE // (1024 * 1024)} MiB\n"
        f"aria2 multi-connection: {YTDLP_USE_ARIA2 and bool(shutil.which('aria2c'))} "
        f"({YTDLP_ARIA2_CONNECTIONS} connections)\n"
        f"EJS/Node: {YTDLP_ENABLE_EJS and bool(shutil.which('node'))}\n"
        f"Public direct first: {YTDLP_PUBLIC_DIRECT_FIRST}\n"
        f"Force IPv4: {YTDLP_FORCE_IPV4}\n"
        f"Transcript workers: {MAX_CONCURRENT_TRANSCRIPTS}\n"
        f"Fast HD workers: {MAX_CONCURRENT_FAST_ENHANCE}\n"
        f"AI HD workers: {MAX_CONCURRENT_AI_ENHANCE}\n"
        f"Auto Clipper workers: {MAX_CONCURRENT_AUTO_CLIPPER}\n"
        f"AI Video Editor workers: {MAX_CONCURRENT_VIDEO_EDITOR}\n"
        f"Maximum Telegram output: {human_bytes(TELEGRAM_MAX_BYTES)}\n"
        f"Free disk space: {human_bytes(free)}\n"
        f"Automatic cleanup: {FILE_CLEANUP_MINUTES} minutes\n\n"
        f"{owner_footer()}"
    )
    if admin:
        cookie = runtime_auth.get_cookie_file()
        text += (
            "\n\nAdministrator Status\n"
            f"Cookies: {'Active' if cookie and cookie.exists() else 'Not configured'}\n"
            f"Proxy pool: {runtime_auth.enabled_proxy_count()} enabled / "
            f"{runtime_auth.proxy_count()} total\n"
            f"Transcript engine: Local Faster-Whisper ({WHISPER_MODEL_NAME})\n"
            f"Transcript device: {WHISPER_DEVICE} / {WHISPER_COMPUTE_TYPE}\n"
            f"English captions first: {'Yes' if TRANSCRIPT_CAPTIONS_FIRST else 'No'}\n"
            f"Auto captions allowed: {'Yes' if TRANSCRIPT_ALLOW_AUTO_CAPTIONS else 'No'}\n"
            f"Urdu translation: Context-aware LLM/Google fallback\n"
            f"Local upscale engine: {'Ready' if (realesrgan_engine_status()[0] or cpu_ai_engine_status()[0]) else 'Fallback mode'}\n"
            f"Transcript cache: {TRANSCRIPT_CACHE_TTL_SECONDS // 60} minutes\n"
            f"Auto Clipper access: key + {FREE_TRIAL_USES_PER_FEATURE} free uses per user\n"
            f"AI Video Editor access: key + {FREE_TRIAL_USES_PER_FEATURE} free uses per user\n"
            f"Smart reframe detector: {'YuNet' if Path(YUNET_MODEL_PATH).exists() else 'Haar fallback'}\n"
            f"Auto Clipper analysis: {'AI semantic analysis ready' if _bot_ai_text_ready() else 'AI maintenance/local fallback mode'}\n"
            f"Administrator job limit: {'Unlimited submissions' if ADMIN_UNLIMITED_JOBS else str(MAX_ACTIVE_JOBS_PER_USER)}"
        )
    return text


async def command_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message:
        await update.message.reply_text(status_text(is_owner(update)))


async def handle_menu_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()
    action = (query.data or "").split(":", 1)[-1]
    if action in {"download", "clipper", "editor", "transcript", "analyzer", "enhance"}:
        record_user_activity(update.effective_user, action.replace("_", " ").title())

    if action == "home":
        context.user_data.pop("mode", None)
        await safe_edit_message(
            query.message,
            f"🎬 {EDITING_STUDIO_NAME}\n\n"
            "Choose AI Clipper, AI Video Editor or Master Editor below.\n\n"
            f"{owner_footer()}",
            reply_markup=main_menu(
                is_owner(update),
                is_auto_clipper_allowed(
                    update.effective_user.id if update.effective_user else 0
                ),
                is_video_editor_allowed(
                    update.effective_user.id if update.effective_user else 0
                ),
            ),
        )
        return

    if action == "download":
        context.user_data.pop("mode", None)
        await safe_edit_message(
            query.message,
            "🎬 YouTube Downloader\n\n"
            "Choose full video download or provide your own timestamps to download only a clip.\n\n"
            f"{owner_footer()}",
            reply_markup=downloader_source_menu(),
        )
    elif action == "transcript":
        context.user_data["mode"] = "transcript"
        await safe_edit_message(
            query.message,
            "📝 Send a YouTube link for video transcription.\n\n"
            "Choose:\n"
            "🇬🇧 English\n"
            "🇵🇰 Urdu\n"
            "🌍 English + Urdu Combined\n\n"
            "Formats: TXT, PDF, SRT.\n"
            "Accurate timestamps are arranged into natural readable paragraphs.\n\n"
            f"{owner_footer()}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Back", callback_data="menu:home")]]
            ),
        )
    elif action == "analyzer":
        context.user_data.pop("mode", None)
        await safe_edit_message(
            query.message,
            "🔎 Video Analyzer\n\n"
            "Send a YouTube link or upload TXT/PDF/SRT transcript. "
            "The bot returns an estimated TikTok viral score and community-guidelines risk.\n\n"
            "These are probability-style estimates, not guarantees.",
            reply_markup=analyzer_source_menu(),
        )
    elif action == "enhance":
        context.user_data["mode"] = "enhance"
        await safe_edit_message(
            query.message,
            "Upload a video as a Telegram video message or as a document.\n\n"
            "Input limit: "
            f"{human_bytes(MAX_ENHANCE_INPUT_BYTES)}.\n"
            "Fast HD 720p/1080p and AI HD 2x are available.\n"
            f"Maximum enhancement duration: {MAX_FAST_ENHANCE_SECONDS // 60} minutes.\n"
            f"AI fast-processing limit: {format_time(MAX_AI_ENHANCE_SECONDS)}; "
            "larger/HD clips automatically use adaptive HQ fallback instead of timing out.\n\n"
            f"{owner_footer()}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Back", callback_data="menu:home")]]
            ),
        )
    elif action == "clipper":
        user = update.effective_user
        if ai_service_gate is not None and not await ai_service_gate(update, context):
            return
        if not user or not is_auto_clipper_allowed(user.id):
            await safe_edit_message(
                query.message,
                paid_access_text("clipper", user.id if user else 0),
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Back", callback_data="menu:home")]]
                ),
            )
            return
        context.user_data.pop("mode", None)
        context.user_data.pop("auto_clipper_analysis_mode", None)
        await safe_edit_message(
            query.message,
            "🔥 Viral Clip Studio\n\n"
            "Choose analysis mode.\n\n"
            "⚡ Quick Clip — current fast Auto Clipper.\n"
            "🧠 Deep Viral Research — research-first semantic + multimodal analysis, first-5-second hook optimization, "
            "10 final clips with Top 3 Elite Picks.\n\n"
            + (f"🎁 Free uses left: {feature_trial_remaining(user.id, 'clipper')}\n\n" if not user_has_key_access(user.id, "clipper") and not is_owner(update) else "")
            + owner_footer(),
            reply_markup=auto_clipper_mode_menu(),
        )
    elif action == "editor":
        user = update.effective_user
        if ai_service_gate is not None and not await ai_service_gate(update, context):
            return
        if not user or not is_video_editor_allowed(user.id):
            await safe_edit_message(
                query.message,
                paid_access_text("editor", user.id if user else 0),
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Back", callback_data="menu:home")]]
                ),
            )
            return

        context.user_data.pop("mode", None)
        await safe_edit_message(
            query.message,
            (
                "🎞 AI Video Editor\n\n"
                "Choose how you want to send the source.\n\n"
                "📤 File: upload the exact video you want edited.\n"
                "🔗 YouTube + Timestamp: send a YouTube link with your own start/end range.\n\n"
                + (f"🎁 Free uses left: {feature_trial_remaining(user.id, 'editor')}\n\n" if not user_has_key_access(user.id, "editor") and not is_owner(update) else "")
                + "The editor keeps the active speaker centered, switches to top/bottom "
                "only during detected simultaneous speaking, adds word-synced captions, "
                "a compact two-line hook and smooth motion.\n\n"
                + owner_footer()
            ),
            reply_markup=video_editor_source_menu(),
        )
    elif action == "status":
        await safe_edit_message(
            query.message,
            status_text(is_owner(update)),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Back", callback_data="menu:home")]]
            ),
        )
    elif action == "help":
        await safe_edit_message(
            query.message,
            "🚀 Choose a service and follow the prompt.\n\n"
            "🎬 Downloads support multiple users.\n"
            "📝 English, Urdu, and combined transcripts are supported.\n"
            "✨ Fast HD and AI HD enhancement are available.\n"
            "✂️ Auto Clipper Pro creates scored vertical viral clips when access is enabled.\n"
            "🎞 AI Video Editor edits uploaded videos with smart reframing and word-synced captions when access is enabled.\n"
            "⛔ Use /stop to cancel your active task.\n\n"
            f"{owner_footer()}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Back", callback_data="menu:home")]]
            ),
        )
    elif action == "channelstudio":
        if not is_owner(update):
            await query.answer("Only for admin.", show_alert=True)
            return
        context.user_data.pop("mode", None)
        await safe_edit_message(
            query.message,
            "🎛 Admin Channel Studio\n\nCreate multiple private channel profiles. "
            "Each channel learns only from its own 2–3 reference videos.",
            reply_markup=admin_channel_menu(),
        )
    elif action == "admin":
        if await reject_non_owner(update):
            return
        await safe_edit_message(
            query.message,
            "Administrator Settings\n\n"
            "Cookies and proxies are shared by all YouTube jobs. Multiple "
            "proxies rotate automatically.\n\n"
            f"{owner_footer()}",
            reply_markup=admin_menu(),
        )


# =============================================================================
# Administrator handlers
# =============================================================================

async def command_allow_clipper(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    set_auto_clipper_public_enabled(True)
    await update.message.reply_text(
        "✅ Auto Clipper Pro is now enabled for all users.\n"
        "They will see it in the /start menu."
    )


async def command_remove_clipper(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    set_auto_clipper_public_enabled(False)
    await update.message.reply_text(
        "✅ Auto Clipper Pro has been removed from normal users.\n"
        "The owner can still use it."
    )


async def command_clipper_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    state = "Enabled for all users" if auto_clipper_public_enabled() else "Owner only"
    llm = "Configured" if (
        _bot_ai_text_ready()
    ) else "Local fallback scoring"
    await update.message.reply_text(
        "✂️ Auto Clipper Pro Status\n\n"
        f"Access: {state}\n"
        f"Viral analysis: {llm}\n"
        f"Max viral moments: {AUTO_CLIPPER_MAX_SEGMENTS}\n"
        f"Worker slots: {MAX_CONCURRENT_AUTO_CLIPPER}"
    )


async def command_allow_editor(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    set_video_editor_public_enabled(True)
    await update.message.reply_text(
        "✅ AI Video Editor is now enabled for all users.\n"
        "They will see it in the /start menu."
    )


async def command_remove_editor(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    set_video_editor_public_enabled(False)
    await update.message.reply_text(
        "✅ AI Video Editor has been removed from normal users.\n"
        "The owner can still use it."
    )


async def command_editor_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await reject_non_owner(update) or not update.message:
        return

    state = (
        "Enabled for all users"
        if video_editor_public_enabled()
        else "Owner only"
    )
    yunet_path = Path(YUNET_MODEL_PATH)
    yunet_ready = (
        yunet_path.exists()
        and yunet_path.stat().st_size > 100000
    )
    await update.message.reply_text(
        "🎞 AI Video Editor Status\n\n"
        f"Access: {state}\n"
        f"Smart face detector: {'YuNet ready' if yunet_ready else 'Haar fallback'}\n"
        f"Worker slots: {MAX_CONCURRENT_VIDEO_EDITOR}\n"
        f"Maximum duration: {MAX_VIDEO_EDITOR_SECONDS // 60} minutes"
    )


async def command_myid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    user = update.effective_user
    await update.message.reply_text(
        "Administrator Account\n"
        f"User ID: {user.id if user else 'Unknown'}\n"
        f"Username: @{user.username if user and user.username else 'Not set'}"
    )


async def auth_status_text() -> str:
    cookie = runtime_auth.get_cookie_file()
    proxies = runtime_auth.list_proxies()
    return (
        "YouTube Authentication Status\n\n"
        f"Cookies: {'Active' if cookie and cookie.exists() else 'Not configured'}\n"
        f"Proxies: {runtime_auth.enabled_proxy_count()} enabled / "
        f"{len(proxies)} total\n"
        "Proxy rotation: Automatic round-robin\n\n"
        "Commands:\n"
        "/setcookies\n"
        "/addproxies\n"
        "/listproxies\n"
        "/clearcookies\n"
        "/clearproxies\n"
        "/authstatus\n"
        "/transcriptstatus"
    )


async def command_authstatus(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    await update.message.reply_text(await auth_status_text())


def _check_transcript_engine_sync() -> Tuple[str, str]:
    checks: List[str] = []
    ok = True

    if shutil.which("ffmpeg"):
        checks.append("✅ FFmpeg: Ready")
    else:
        ok = False
        checks.append("❌ FFmpeg: Not found")

    try:
        import faster_whisper  # noqa: F401
        checks.append(
            f"✅ Faster-Whisper: Ready ({WHISPER_MODEL_NAME})"
        )
    except Exception as exc:
        ok = False
        checks.append(
            f"❌ Faster-Whisper: {str(exc)[:120]}"
        )

    try:
        from deep_translator import GoogleTranslator  # noqa: F401
        checks.append("✅ Google Translator module: Ready")
    except Exception as exc:
        ok = False
        checks.append(
            f"❌ Google Translator module: {str(exc)[:120]}"
        )

    gpu_ai_ok, gpu_ai_reason = realesrgan_engine_status()
    cpu_ai_ok, cpu_ai_reason = cpu_ai_engine_status()
    ai_ok = gpu_ai_ok or cpu_ai_ok
    ai_reason = (
        "Real-ESRGAN GPU AI ready"
        if gpu_ai_ok
        else (
            cpu_ai_reason
            if cpu_ai_ok
            else f"GPU: {gpu_ai_reason}; CPU: {cpu_ai_reason}"
        )
    )
    if ai_ok:
        checks.append("✅ AI Upscale: Real-ESRGAN installed")
    else:
        checks.append(
            "⚡ AI Upscale: Fast HD fallback active"
        )
        checks.append(
            f"AI detail: {ai_reason[:160]}"
        )

    heading = (
        "✅ Local Transcript Engine Ready"
        if ok
        else "⚠️ Local Transcript Engine Needs Attention"
    )
    return (
        "active" if ok else "error",
        heading
        + "\n\n"
        + "\n".join(checks)
        + f"\n\nTranscript workers: {MAX_CONCURRENT_TRANSCRIPTS}"
        + f"\nTranslation workers: {TRANSLATION_WORKERS}",
    )


async def transcript_api_status_text() -> str:
    _, message = await asyncio.to_thread(
        _check_transcript_engine_sync
    )
    return message


async def command_apicheck(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await reject_non_owner(update) or not update.message:
        return

    status_message = await update.message.reply_text(
        "🔄 Checking local transcript engine..."
    )
    result = await transcript_api_status_text()

    try:
        await status_message.edit_text(result)
    except TelegramError:
        await update.message.reply_text(result)


async def command_setcookies(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    context.user_data["admin_action"] = "cookies"
    await update.message.reply_text(
        "Upload a Netscape-format cookies.txt file as a document. "
        "Do not send a Google password."
    )


async def save_cookie_document(
    message,
    document,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if document.file_size and document.file_size > 5 * 1024 * 1024:
        await message.reply_text("The cookies file must be smaller than 5 MB.")
        return

    temp = AUTH_DIR / f"cookies_{uuid.uuid4().hex}.tmp"
    try:
        tg_file = await document.get_file()
        await tg_file.download_to_drive(custom_path=temp)
        valid, reason = cookies_file_valid(temp)
        if not valid:
            temp.unlink(missing_ok=True)
            await message.reply_text(f"Cookies rejected: {reason}")
            return
        os.chmod(temp, 0o600)
        temp.replace(UPLOADED_COOKIE_FILE)
        os.chmod(UPLOADED_COOKIE_FILE, 0o600)
        runtime_auth.set_cookie_file(UPLOADED_COOKIE_FILE)
        context.user_data.pop("admin_action", None)
        await delete_sensitive_message(message)
        await context.bot.send_message(
            chat_id=message.chat_id,
            text="YouTube cookies were saved successfully.",
        )
    except Exception as exc:
        temp.unlink(missing_ok=True)
        logger.exception("Could not save cookies")
        await message.reply_text(f"Cookie save error: {str(exc)[:200]}")


async def command_addproxies(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    if context.args:
        raw = "\n".join(context.args)
        added, skipped = runtime_auth.add_proxies(raw)
        await delete_sensitive_message(update.message)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                f"Proxy pool updated. Added: {added}, skipped: {skipped}. "
                f"Total: {runtime_auth.proxy_count()}."
            ),
        )
        return
    context.user_data["admin_action"] = "proxies"
    await update.message.reply_text(
        "Send one or more proxies, one per line.\n\n"
        "Supported examples:\n"
        "http://user:pass@ip:port\n"
        "socks5://user:pass@ip:port\n"
        "ip:port:user:pass\n"
        "ip:port"
    )


async def command_listproxies(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    proxies = runtime_auth.list_proxies()
    if not proxies:
        await update.message.reply_text("The proxy pool is empty.")
        return

    now = time.time()
    lines = []
    for index, item in enumerate(proxies[:50], 1):
        disabled_until = float(item.get("disabled_until") or 0)
        status = (
            f"disabled for {max(1, int((disabled_until - now) / 60))} min"
            if disabled_until > now
            else "enabled"
        )
        latency = float(item.get("benchmark_ms") or 0.0)
        throughput = float(item.get("throughput_mbps") or 0.0)
        metric = (
            f"{latency:.0f}ms" if 0 < latency < 999999 else "untested"
        )
        if throughput > 0:
            metric += f", learned {throughput:.1f}Mbps"
        lines.append(
            f"{index}. {mask_proxy(str(item.get('url')))} | {status} | "
            f"{metric} | success {int(item.get('successes') or 0)}"
        )
    await update.message.reply_text(
        "Rotating Proxy Pool\n\n" + "\n".join(lines)
    )


async def command_testproxies(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    if not runtime_auth.proxy_count():
        await update.message.reply_text("The proxy pool is empty.")
        return

    status = await update.message.reply_text(
        "⚡ Testing proxy latency and rebuilding fastest-first ranking..."
    )
    try:
        tested = await refresh_proxy_benchmarks_if_needed(force=True)
        rows = runtime_auth.list_proxies()
        now = time.time()
        rows = [
            item for item in rows
            if float(item.get("disabled_until") or 0) <= now
        ]
        rows.sort(
            key=lambda item: (
                0 if float(item.get("throughput_mbps") or 0) > 0 else 1,
                -float(item.get("throughput_mbps") or 0),
                float(item.get("benchmark_ms") or 999999),
            )
        )
        lines: List[str] = []
        for index, item in enumerate(rows[:12], 1):
            latency = float(item.get("benchmark_ms") or 0)
            throughput = float(item.get("throughput_mbps") or 0)
            latency_text = (
                f"{latency:.0f} ms" if 0 < latency < 999999 else "failed"
            )
            speed_text = (
                f" | learned {throughput:.1f} Mbps" if throughput > 0 else ""
            )
            lines.append(
                f"{index}. {mask_proxy(str(item.get('url')))} | "
                f"{latency_text}{speed_text}"
            )
        body = "\n".join(lines) if lines else "No healthy proxy responded."
        await safe_edit_message(
            status,
            f"⚡ Proxy ranking refreshed. Tested: {tested}\n\n{body}",
        )
    except Exception as exc:
        logger.exception("Proxy speed test failed")
        await safe_edit_message(status, f"Proxy test failed: {str(exc)[:240]}")


async def command_clearproxies(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    runtime_auth.clear_proxies()
    context.user_data.pop("admin_action", None)
    await update.message.reply_text("All proxies were removed.")


async def command_clearcookies(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    old = runtime_auth.clear_cookie_file()
    for path in {old, UPLOADED_COOKIE_FILE}:
        if path:
            try:
                if path.resolve().parent == AUTH_DIR.resolve():
                    path.resolve().unlink(missing_ok=True)
            except OSError:
                pass
    await update.message.reply_text("YouTube cookies were removed.")


async def command_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await reject_non_owner(update) or not update.message:
        return
    context.user_data.pop("admin_action", None)
    context.user_data.pop("mode", None)
    await update.message.reply_text("The current action was cancelled.")


async def handle_admin_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    if await reject_non_owner(update):
        return
    await query.answer()
    action = (query.data or "").split(":", 1)[-1]

    if action == "cookies":
        context.user_data["admin_action"] = "cookies"
        await query.message.reply_text(
            "Upload a Netscape-format cookies.txt file as a document."
        )
    elif action == "proxies":
        context.user_data["admin_action"] = "proxies"
        await query.message.reply_text(
            "Send one or more proxies, one per line."
        )
    elif action == "listproxies":
        proxies = runtime_auth.list_proxies()
        if not proxies:
            await query.message.reply_text("The proxy pool is empty.")
        else:
            lines = [
                f"{index}. {mask_proxy(str(item.get('url')))}"
                for index, item in enumerate(proxies[:50], 1)
            ]
            await query.message.reply_text("\n".join(lines))
    elif action == "testproxies":
        status_message = await query.message.reply_text(
            "⚡ Testing proxy latency and rebuilding fastest-first ranking..."
        )
        try:
            tested = await refresh_proxy_benchmarks_if_needed(force=True)
            rows = runtime_auth.list_proxies()
            rows.sort(
                key=lambda item: (
                    float(item.get("benchmark_ms") or 999999),
                    -float(item.get("throughput_mbps") or 0),
                )
            )
            lines = []
            for index, item in enumerate(rows[:12], 1):
                latency = float(item.get("benchmark_ms") or 0)
                throughput = float(item.get("throughput_mbps") or 0)
                latency_text = (
                    f"{latency:.0f} ms" if 0 < latency < 999999 else "failed"
                )
                speed_text = (
                    f" | learned {throughput:.1f} Mbps" if throughput > 0 else ""
                )
                lines.append(
                    f"{index}. {mask_proxy(str(item.get('url')))} | "
                    f"{latency_text}{speed_text}"
                )
            await safe_edit_message(
                status_message,
                f"⚡ Proxy ranking refreshed. Tested: {tested}\n\n"
                + ("\n".join(lines) if lines else "No proxies configured."),
            )
        except Exception as exc:
            await safe_edit_message(
                status_message, f"Proxy test failed: {str(exc)[:240]}"
            )
    elif action == "status":
        await query.message.reply_text(await auth_status_text())
    elif action == "apicheck":
        status_message = await query.message.reply_text(
            "🔄 Checking local transcript engine..."
        )
        result = await transcript_api_status_text()
        try:
            await status_message.edit_text(result)
        except TelegramError:
            await query.message.reply_text(result)
    elif action == "clearcookies":
        old = runtime_auth.clear_cookie_file()
        if old:
            old.unlink(missing_ok=True)
        UPLOADED_COOKIE_FILE.unlink(missing_ok=True)
        await query.message.reply_text("YouTube cookies were removed.")
    elif action == "clearproxies":
        runtime_auth.clear_proxies()
        await query.message.reply_text("All proxies were removed.")
    elif action == "live":
        await query.message.reply_text(await build_live_processing_text())
    elif action == "deepviral":
        await safe_edit_message(
            query.message,
            deep_research_status_text(),
            reply_markup=deep_research_admin_menu(),
        )
    elif action == "deepviral_on":
        _write_deep_research_state(True)
        await safe_edit_message(
            query.message,
            deep_research_status_text(),
            reply_markup=deep_research_admin_menu(),
        )
    elif action == "deepviral_off":
        _write_deep_research_state(False)
        await safe_edit_message(
            query.message,
            deep_research_status_text(),
            reply_markup=deep_research_admin_menu(),
        )
    elif action == "deepviral_status":
        await safe_edit_message(
            query.message,
            deep_research_status_text(),
            reply_markup=deep_research_admin_menu(),
        )
    elif action == "stopall":
        count = active_jobs.cancel_all()
        await query.message.reply_text(
            f"Stop requested for {count} active task(s)."
            if count
            else "No active tasks are running."
        )


# =============================================================================
# AI Video Editor source selection + timestamp downloader
# =============================================================================

def _parse_editor_timestamp_token(value: str) -> Optional[float]:
    value = str(value or "").strip()
    if not value:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return float(value)

    parts = value.split(":")
    if len(parts) not in {2, 3}:
        return None
    try:
        numbers = [float(item) for item in parts]
    except ValueError:
        return None
    if any(number < 0 for number in numbers):
        return None
    if len(numbers) == 2:
        minutes, seconds = numbers
        if seconds >= 60:
            return None
        return minutes * 60 + seconds
    hours, minutes, seconds = numbers
    if minutes >= 60 or seconds >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds


def parse_editor_youtube_request(
    value: str,
) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """Parse 'YouTube URL  00:30 - 01:20' without guessing missing times."""
    value = str(value or "")
    url = extract_youtube_url(value)
    if not url:
        return None, None, None

    remaining = value.replace(url, " ")
    match = re.search(
        r"(?P<start>\d+(?::\d{1,2}){1,2}|\d+(?:\.\d+)?)"
        r"\s*(?:-|–|—|\bto\b|\buntil\b)\s*"
        r"(?P<end>\d+(?::\d{1,2}){1,2}|\d+(?:\.\d+)?)",
        remaining,
        flags=re.I,
    )
    if not match:
        return url, None, None

    start = _parse_editor_timestamp_token(match.group("start"))
    end = _parse_editor_timestamp_token(match.group("end"))
    if start is None or end is None:
        return url, None, None
    return url, start, end


async def handle_download_flow_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()
    action = (query.data or "").split(":", 1)[-1]
    if action == "full":
        context.user_data["mode"] = "download"
        await safe_edit_message(
            query.message,
            "🎬 Send a YouTube video, Shorts, or live-video link.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="menu:download")]]
            ),
        )
    elif action == "timestamp":
        context.user_data["mode"] = "timestamp_download"
        await safe_edit_message(
            query.message,
            "✂️ Send YouTube link and timestamp range in one message.\\n\\n"
            "Example:\\nhttps://youtu.be/VIDEO_ID  10:30 - 11:30\\n\\n"
            "After the clip is sent, English and Urdu transcript options will appear.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="menu:download")]]
            ),
        )


async def handle_editor_flow_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not query.message or not user:
        return
    await query.answer()

    if not is_video_editor_allowed(user.id):
        await safe_edit_message(
            query.message,
            paid_access_text("editor", update.effective_user.id),
        )
        return

    action = (query.data or "").split(":", 1)[-1]
    if action in {"file", "filetimestamp"}:
        context.user_data["mode"] = "video_editor_file_timestamp" if action == "filetimestamp" else "video_editor_file"
        await safe_edit_message(
            query.message,
            "📤 Send the video file now.\n\n" + ("After upload, send the timestamp range (example 00:30 - 01:20).\n\n" if action == "filetimestamp" else "") + 
            "You can send it as a Telegram video or document. The bot will "
            "analyze faces/speakers and then show caption styles.\n\n"
            f"Maximum Telegram Bot API file size: {human_bytes(MAX_VIDEO_EDITOR_INPUT_BYTES)}. "
            "Telegram bots cannot receive a new 4 GB upload; for larger sources use YouTube/timestamp input.\n\n"
            f"{owner_footer()}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="menu:editor")]]
            ),
        )
        return

    if action == "youtube":
        context.user_data["mode"] = "video_editor_youtube"
        await safe_edit_message(
            query.message,
            "🔗 Send the YouTube link and your exact edit range in one message.\n\n"
            "Example:\n"
            "https://youtu.be/VIDEO_ID  00:30 - 01:20\n\n"
            "Accepted time formats: MM:SS or HH:MM:SS. Only that range will be "
            "prepared for the editor.\n\n"
            f"{owner_footer()}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="menu:editor")]]
            ),
        )



def _local_timestamp_cut_fallbacks(
    full_file: Path,
    stem: str,
    start: float,
    end: float,
) -> Tuple[Path, List[str]]:
    """Cut a downloaded source locally with several FFmpeg-safe fallbacks.

    The primary path remains H.264/AAC MP4. Some VPS FFmpeg builds either do
    not ship libx264, cannot mux a selected YouTube codec into MP4, or choke on
    odd source timestamps. In those cases we progressively fall back to native
    MPEG-4/AAC, MPEG-4/PCM in Matroska, then a no-transcode Matroska stream
    copy. This keeps timestamp jobs usable instead of treating one FFmpeg
    encoder/container combination as fatal.
    """
    cut_duration = max(0.1, float(end) - float(start))
    errors: List[str] = []
    timeout_seconds = max(300, int(cut_duration * 6.0 + 240))

    common_prefix = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-fflags", "+genpts",
        "-ss", f"{float(start):.3f}",
        "-i", str(full_file),
        "-t", f"{cut_duration:.3f}",
        "-map", "0:v:0", "-map", "0:a:0?",
        "-sn", "-dn",
    ]

    # Use only software encoders here. Hardware encoders may be listed by
    # FFmpeg even when the VPS has no matching device and would fail at runtime.
    strategies: List[Tuple[str, Path, List[str]]] = [
        (
            "h264-aac-mp4",
            DOWNLOAD_DIR / f"{stem}.mp4",
            [
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                "-avoid_negative_ts", "make_zero",
            ],
        ),
        (
            "mpeg4-aac-mp4",
            DOWNLOAD_DIR / f"{stem}.mpeg4.mp4",
            [
                "-c:v", "mpeg4", "-q:v", "3",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                "-avoid_negative_ts", "make_zero",
            ],
        ),
        (
            "mpeg4-pcm-mkv",
            DOWNLOAD_DIR / f"{stem}.mpeg4.mkv",
            [
                "-c:v", "mpeg4", "-q:v", "3",
                "-pix_fmt", "yuv420p",
                "-c:a", "pcm_s16le",
                "-avoid_negative_ts", "make_zero",
            ],
        ),
        (
            "stream-copy-mkv",
            DOWNLOAD_DIR / f"{stem}.copy.mkv",
            [
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
            ],
        ),
    ]

    for label, output_path, output_args in strategies:
        output_path.unlink(missing_ok=True)
        cmd = common_prefix + output_args + [str(output_path)]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"{label}: ffmpeg timed out")
            output_path.unlink(missing_ok=True)
            continue
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            output_path.unlink(missing_ok=True)
            continue

        if proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
            detail = (proc.stderr or "").strip().replace("\n", " ")[-900:]
            errors.append(
                f"{label}: {detail or 'ffmpeg exit ' + str(proc.returncode)}"
            )
            output_path.unlink(missing_ok=True)
            continue

        # Reject a zero/invalid media result before returning it. The outer job
        # performs the final requested-range tolerance check as well.
        try:
            media = probe_media(output_path)
            if float(media.get("duration") or 0.0) <= 0:
                raise RuntimeError("no valid duration")
        except Exception as exc:
            errors.append(f"{label}: output probe failed: {exc}")
            output_path.unlink(missing_ok=True)
            continue

        return output_path.resolve(), errors

    raise DownloadError(
        "all local timestamp cut strategies failed: "
        + " | ".join(errors[-4:])
    )



def _local_timestamp_cut_two_inputs_fallbacks(
    video_file: Path,
    audio_file: Optional[Path],
    stem: str,
    start: float,
    end: float,
) -> Tuple[Path, List[str]]:
    """Cut separately downloaded video/audio without any yt-dlp merge step.

    This is deliberately independent of yt-dlp --download-sections.  It is the
    recovery path for VPS/FFmpeg combinations where section downloading or the
    yt-dlp merger exits before a usable source file is produced.
    """
    cut_duration = max(0.1, float(end) - float(start))
    errors: List[str] = []
    timeout_seconds = max(300, int(cut_duration * 8.0 + 300))

    # Seek each elementary source independently.  Accurate seek happens during
    # the following re-encode; no timestamp section downloader is involved.
    prefix = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-fflags", "+genpts+discardcorrupt",
        "-ss", f"{float(start):.3f}", "-i", str(video_file),
    ]
    if audio_file is not None:
        prefix += ["-ss", f"{float(start):.3f}", "-i", str(audio_file)]

    mapping = ["-map", "0:v:0"]
    if audio_file is not None:
        mapping += ["-map", "1:a:0?"]
    else:
        mapping += ["-map", "0:a:0?"]

    common = mapping + [
        "-t", f"{cut_duration:.3f}", "-sn", "-dn",
        "-max_interleave_delta", "0",
    ]
    strategies: List[Tuple[str, Path, List[str]]] = [
        (
            "components-h264-aac",
            DOWNLOAD_DIR / f"{stem}.mp4",
            ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", "-avoid_negative_ts", "make_zero"],
        ),
        (
            "components-mpeg4-aac",
            DOWNLOAD_DIR / f"{stem}.mpeg4.mp4",
            ["-c:v", "mpeg4", "-q:v", "3", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
             "-avoid_negative_ts", "make_zero"],
        ),
    ]

    for label, output_path, output_args in strategies:
        output_path.unlink(missing_ok=True)
        cmd = prefix + common + output_args + [str(output_path)]
        try:
            proc = subprocess.run(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True, timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"{label}: ffmpeg timed out")
            continue
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            continue
        if proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
            detail = (proc.stderr or "").strip().replace("\n", " ")[-1400:]
            errors.append(f"{label}: {detail or 'ffmpeg exit ' + str(proc.returncode)}")
            output_path.unlink(missing_ok=True)
            continue
        try:
            media = probe_media(output_path)
            if float(media.get("duration") or 0.0) <= 0:
                raise RuntimeError("no valid duration")
        except Exception as exc:
            errors.append(f"{label}: output probe failed: {exc}")
            output_path.unlink(missing_ok=True)
            continue
        return output_path.resolve(), errors

    raise DownloadError(
        "separate video/audio local cut failed: " + " | ".join(errors[-3:])
    )

def _progressive_editor_fallback_selector() -> str:
    """Single-file emergency source ladder that avoids FFmpeg merging.

    YouTube commonly exposes a progressive 720p/360p stream even when adaptive
    4K/2K streams need a video+audio merge. This is intentionally used only
    after the high-quality adaptive/full-source route has failed.
    """
    return "/".join(
        [
            "best[height=1080][vcodec!=none][acodec!=none]",
            "best[height=720][vcodec!=none][acodec!=none]",
            "best[height=480][vcodec!=none][acodec!=none]",
            "best[height=360][vcodec!=none][acodec!=none]",
            "best[vcodec!=none][acodec!=none]",
        ]
    )


async def download_youtube_editor_segment(
    url: str,
    title: str,
    start: float,
    end: float,
    progress_cb: ProgressCallback,
    cancel_event: threading.Event,
) -> Tuple[Optional[Path], Optional[str]]:
    """Download only the requested range first; full-source fetch is final fallback."""
    async with cancellable_slot(DOWNLOAD_SEMAPHORE, cancel_event):
        ensure_not_cancelled(cancel_event)
        try:
            await refresh_proxy_benchmarks_if_needed()
        except Exception as exc:
            logger.info("Proxy benchmark refresh skipped for editor range: %s", exc)

        stem = f"editor_range_{safe_filename(title)}_{uuid.uuid4().hex[:8]}"
        formats = [
            MediaFormatCandidate(
                "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
                "mp4",
                True,
            )
        ]
        try:
            result = await media_transport.download_timestamp(
                url,
                stem,
                formats,
                start=float(start),
                end=float(end),
                target_height=1080,
                progress_cb=progress_cb,
                cancel_event=cancel_event,
                full_fallback=True,
            )
            ensure_not_cancelled(cancel_event)
            return result.path.resolve(), None
        except TransportCancelled as exc:
            raise JobCancelled(str(exc)) from exc
        except JobCancelled:
            raise
        except Exception as exc:
            logger.warning("Central timestamp transport failed: %s", exc)
            return None, f"Timestamp download failed: {str(exc)[:420]}"



async def prepare_youtube_editor_job(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    start: float,
    end: float,
) -> None:
    if not update.message or not update.effective_user:
        return
    if ai_service_gate is not None and not await ai_service_gate(update, context):
        return

    status = await update.message.reply_text("Reading the YouTube timestamp range...")
    if not is_video_editor_allowed(update.effective_user.id):
        await safe_edit_message(status, paid_access_text("editor", update.effective_user.id))
        return
    if end <= start:
        await safe_edit_message(status, "End timestamp must be after the start timestamp.")
        return
    if end - start < 2.0:
        await safe_edit_message(status, "Choose a range of at least 2 seconds.")
        return
    if end - start > MAX_VIDEO_EDITOR_SECONDS:
        await safe_edit_message(
            status,
            "The selected range is too long for AI Video Editor. "
            f"Maximum: {MAX_VIDEO_EDITOR_SECONDS // 60} minutes.",
        )
        return

    key = f"editorfetch:{update.effective_user.id}:{uuid.uuid4().hex[:8]}"
    started, reason, cancel_event = active_jobs.try_start(
        update.effective_user.id,
        key,
        "editor_download",
        is_admin=is_owner(update),
        title="YouTube timestamp editor",
        username=str(update.effective_user.username or ""),
    )
    if not started or cancel_event is None:
        await safe_edit_message(status, reason)
        return

    input_path: Optional[Path] = None

    async def progress(message: str) -> None:
        active_jobs.update(key, stage=message)
        await safe_edit_message(status, message, reply_markup=stop_keyboard())

    try:
        info = await extract_info(url)
        source_duration = float(info.get("duration") or 0.0)
        title = str(info.get("title") or "YouTube Video")
        active_jobs.update(key, title=title)

        if source_duration > 0 and start >= source_duration:
            await safe_edit_message(
                status,
                "Start timestamp is outside the YouTube video duration.",
            )
            return
        if source_duration > 0 and end > source_duration + 0.5:
            await safe_edit_message(
                status,
                f"End timestamp is beyond the video duration ({format_time(source_duration)}).",
            )
            return

        await progress(
            f"Downloading selected range {format_time(start)} - {format_time(end)}..."
        )
        input_path, error = await download_youtube_editor_segment(
            url,
            title,
            start,
            end,
            progress,
            cancel_event,
        )
        if error or not input_path:
            await safe_edit_message(status, error or "Timestamp download failed.")
            return

        ensure_not_cancelled(cancel_event)
        media = await asyncio.to_thread(probe_media, input_path)
        duration = float(media.get("duration") or 0.0)
        if duration <= 0:
            raise RuntimeError("Downloaded editor range has no valid duration")

        job_id = save_video_editor_job(
            context,
            update.effective_user.id,
            input_path,
            f"{title} [{format_time(start)}-{format_time(end)}].mp4",
            media,
        )
        jobs(context)[job_id]["source_url"] = url
        jobs(context)[job_id]["source_start"] = start
        jobs(context)[job_id]["source_end"] = end

        await safe_edit_message(
            status,
            "🎞 Timestamp range is ready for AI Video Editor.\n\n"
            f"Video: {title[:150]}\n"
            f"Selected: {format_time(start)} - {format_time(end)}\n"
            f"Downloaded duration: {format_time(duration)}\n\n"
            "Choose a caption template.\n\n"
            f"{owner_footer()}",
            reply_markup=video_editor_template_menu(job_id),
        )
        await cleanup_manager.schedule([input_path], minutes=360)
    except JobCancelled:
        await safe_edit_message(status, "Task stopped successfully.")
    except Exception as exc:
        logger.exception("Could not prepare YouTube timestamp editor job")
        await safe_edit_message(status, f"Editor preparation failed: {str(exc)[:260]}")
    finally:
        active_jobs.finish(update.effective_user.id, key)


async def prepare_timestamp_download_job(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    start: float,
    end: float,
) -> None:
    if not update.message or not update.effective_user or not update.effective_chat:
        return
    status = await update.message.reply_text("Reading timestamp request...")
    if end <= start or end - start < 1.0:
        await safe_edit_message(status, "End timestamp must be after start timestamp.")
        return
    if end - start > MAX_TRANSCRIPT_SECONDS:
        await safe_edit_message(status, "Timestamp range is too long.")
        return

    key = f"timestampdl:{update.effective_user.id}:{uuid.uuid4().hex[:8]}"
    started, reason, cancel_event = active_jobs.try_start(
        update.effective_user.id,
        key,
        "timestamp_download",
        is_admin=is_owner(update),
        title="Timestamp clip download",
        username=str(update.effective_user.username or ""),
    )
    if not started or cancel_event is None:
        await safe_edit_message(status, reason)
        return

    clip_path: Optional[Path] = None

    async def progress(message: str) -> None:
        active_jobs.update(key, stage=message)
        await safe_edit_message(status, message, reply_markup=stop_keyboard())

    try:
        info = await extract_info(url)
        title = str(info.get("title") or "YouTube Clip")
        duration = float(info.get("duration") or 0)
        if duration > 0 and (start >= duration or end > duration + 0.5):
            await safe_edit_message(status, f"Timestamp is outside video duration: {format_time(duration)}")
            return
        await progress(f"Downloading {format_time(start)} - {format_time(end)}...")
        clip_path, error = await download_youtube_editor_segment(
            url, title, start, end, progress, cancel_event
        )
        if error or not clip_path:
            await safe_edit_message(status, error or "Timestamp download failed.")
            return

        media = await asyncio.to_thread(probe_media, clip_path)
        transcript_job_id = uuid.uuid4().hex[:10]
        jobs(context)[transcript_job_id] = {
            "job_id": transcript_job_id,
            "type": "local_transcript",
            "owner_id": update.effective_user.id,
            "local_path": str(clip_path.resolve()),
            "title": f"{title} [{format_time(start)}-{format_time(end)}]",
            "duration": optional_int(media.get("duration")),
            "created_at": time.time(),
        }

        caption = (
            f"✂️ {title[:170]}\\n"
            f"⏱ {format_time(start)} - {format_time(end)}\\n\\n"
            "Choose a transcript option below."
        )
        await send_video_with_fallback(
            context,
            update.effective_chat.id,
            clip_path,
            caption,
            optional_int(media.get("duration")),
            optional_int(media.get("width")),
            optional_int(media.get("height")),
        )
        await safe_edit_message(
            status,
            "✅ Timestamp clip sent. Select transcript language:",
            reply_markup=timestamp_result_menu(transcript_job_id),
        )
        await cleanup_manager.schedule([clip_path], minutes=360)
    except JobCancelled:
        await safe_edit_message(status, "Task stopped.")
    except Exception as exc:
        logger.exception("Timestamp downloader failed")
        await safe_edit_message(status, f"Timestamp download error: {str(exc)[:300]}")
    finally:
        active_jobs.finish(update.effective_user.id, key)


def read_transcript_document(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".srt"}:
        value = path.read_text(encoding="utf-8", errors="ignore")
        if suffix == ".srt":
            value = re.sub(r"(?m)^\d+\s*$", "", value)
            value = re.sub(
                r"(?m)^\d{2}:\d{2}:\d{2}[,.]\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}[,.]\d{3}.*$",
                "",
                value,
            )
        return normalize_transcript_text(value)[:ANALYZER_MAX_TEXT_CHARS]
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except Exception as exc:
            raise RuntimeError("pypdf is required for PDF analysis") from exc
        reader = PdfReader(str(path))
        value = "\n".join(page.extract_text() or "" for page in reader.pages)
        return normalize_transcript_text(value)[:ANALYZER_MAX_TEXT_CHARS]
    raise RuntimeError("Only TXT, PDF, and SRT are supported")


def _analyzer_evidence(
    text_value: str,
    terms: Iterable[str],
    limit: int = 3,
) -> List[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text_value)
    output: List[str] = []
    terms_lower = [term.lower() for term in terms]
    for sentence in sentences:
        lower = sentence.lower()
        if any(term in lower for term in terms_lower):
            cleaned = _clipper_clean_text(sentence, 180)
            if cleaned and cleaned not in output:
                output.append(cleaned)
        if len(output) >= limit:
            break
    return output


def community_guideline_risk(
    text_value: str,
) -> Tuple[int, List[str], List[str]]:
    """
    Context-sensitive local policy-risk fallback.
    Neutral reporting/discussion scores lower than promotion, instruction,
    glorification, threats, graphic detail or solicitation.
    """
    text_lower = text_value.lower()

    categories: Dict[str, set[str]] = {
        "Violence / threats": {
            "kill", "murder", "shoot", "stab", "bomb", "attack",
            "torture", "blood", "weapon", "gun", "knife",
        },
        "Sexual / adult content": {
            "sex", "nude", "nudity", "porn", "penis", "vagina",
            "orgasm", "rape", "sexual",
        },
        "Self-harm / suicide": {
            "suicide", "self harm", "self-harm", "kill myself",
            "cut myself", "overdose",
        },
        "Hate / harassment": {
            "hate speech", "racial slur", "nazi", "inferior race",
            "go back to your country", "death threat",
        },
        "Regulated goods / drugs": {
            "cocaine", "heroin", "meth", "cannabis", "marijuana",
            "drug dealer", "buy drugs", "vape", "nicotine",
        },
        "Dangerous acts": {
            "dangerous challenge", "try this at home", "choking game",
            "fire challenge", "reckless stunt",
        },
        "Gambling / fraud": {
            "guaranteed win", "betting", "casino", "gambling",
            "scam", "fraud", "fake investment",
        },
        "Misinformation risk": {
            "miracle cure", "doctors are lying", "vaccine kills",
            "never take medicine", "guaranteed cure",
        },
        "Minor safety": {
            "underage sex", "child sexual", "minor nude",
            "sexualize children",
        },
        "Privacy / doxxing": {
            "home address", "phone number", "doxx", "private information",
        },
    }

    promotion_markers = {
        "how to", "step by step", "you should", "go do", "try this",
        "buy", "sell", "where to get", "tutorial", "instructions",
        "i encourage", "we should kill", "deserves to die",
    }
    contextual_markers = {
        "news", "reported", "documentary", "discuss", "discussion",
        "survivor", "warning", "awareness", "history", "explaining",
        "against", "condemn", "victim",
    }

    reasons: List[str] = []
    evidence: List[str] = []
    raw_score = 0.0

    for label, terms in categories.items():
        hits = [term for term in terms if term in text_lower]
        if not hits:
            continue
        raw_score += min(18.0, 5.0 + len(hits) * 3.0)
        reasons.append(label)
        evidence.extend(_analyzer_evidence(text_value, hits, 1))

    promotion_count = sum(
        1 for marker in promotion_markers if marker in text_lower
    )
    context_count = sum(
        1 for marker in contextual_markers if marker in text_lower
    )

    if raw_score > 0:
        raw_score += min(28.0, promotion_count * 7.0)
        raw_score -= min(18.0, context_count * 3.0)

    return (
        max(1, min(96, int(round(raw_score)))),
        reasons[:6],
        list(dict.fromkeys(evidence))[:4],
    )


def _local_viral_analysis(
    text_value: str,
    title: str = "",
) -> Tuple[int, List[str], List[str]]:
    clean = normalize_transcript_text(text_value)
    lower = clean.lower()
    word_count = len(clean.split())
    score = 28.0
    signals: List[str] = []
    evidence: List[str] = []

    curiosity_terms = [
        "the truth", "nobody tells", "what happened", "i never knew",
        "secret", "shocking", "you won't believe", "here's why",
        "the reason", "worst", "best", "biggest mistake",
    ]
    curiosity_hits = [term for term in curiosity_terms if term in lower]
    if curiosity_hits:
        score += min(18, 5 + len(curiosity_hits) * 4)
        signals.append("strong curiosity / hook language")
        evidence.extend(_analyzer_evidence(clean, curiosity_hits, 1))

    emotional_terms = [
        "love", "hate", "angry", "terrified", "cry", "cried", "death",
        "pain", "scared", "risk", "warning", "crazy", "insane",
        "unbelievable", "exposed", "lied", "truth",
    ]
    emotional_hits = [term for term in emotional_terms if term in lower]
    if emotional_hits:
        score += min(17, len(emotional_hits) * 2.7)
        signals.append("emotional or high-stakes language")
        evidence.extend(_analyzer_evidence(clean, emotional_hits, 1))

    numbers = len(re.findall(r"\b\d+(?:\.\d+)?%?\b", clean))
    quotable = sum(
        1
        for phrase in (
            "i realized", "i learned", "the truth is", "the problem is",
            "you have to", "you need to", "this is why",
        )
        if phrase in lower
    )
    if numbers:
        score += min(8, numbers * 1.5)
        signals.append("specific facts/numbers")
    if quotable:
        score += min(13, quotable * 4)
        signals.append("quotable statements")

    sentences = [
        value.strip()
        for value in re.split(r"(?<=[.!?])\s+", clean)
        if value.strip()
    ]
    avg_sentence_words = word_count / max(1, len(sentences))
    if 7 <= avg_sentence_words <= 24:
        score += 8
        signals.append("good short-form speaking density")
    if "?" in clean:
        score += min(6, clean.count("?") * 1.5)
        signals.append("question-driven curiosity")
    if word_count > 1500:
        score -= 7

    if title and any(
        term in title.lower()
        for term in curiosity_terms
    ):
        score += 4

    return (
        max(5, min(95, int(round(score)))),
        signals[:6],
        list(dict.fromkeys(evidence))[:4],
    )


def analyze_text_report(
    text_value: str,
    title: str = "",
    description: str = "",
) -> str:
    """
    Content-specific analysis. Uses semantic LLM review when configured and
    contextual local analysis as fallback/sanity check.
    """
    text_value = normalize_transcript_text(text_value)
    if not text_value:
        raise RuntimeError("No readable transcript text was found")

    local_viral, local_signals, local_evidence = _local_viral_analysis(
        text_value,
        title,
    )
    local_risk, local_risk_reasons, local_risk_evidence = (
        community_guideline_risk(text_value)
    )

    viral = local_viral
    risk = local_risk
    viral_reasons = list(local_signals)
    risk_reasons = list(local_risk_reasons)
    evidence = list(
        dict.fromkeys(local_evidence + local_risk_evidence)
    )
    analysis_mode = "Local contextual analysis"

    if (
        _bot_ai_text_ready()
    ):
        prompt = f"""
Analyze this content as a TikTok short-form strategist and safety reviewer.

Return ONLY one JSON object:
{{
  "viral_score": integer 1-100,
  "guideline_risk": integer 1-100,
  "viral_reasons": ["specific reason", ...],
  "risk_categories": ["specific category", ...],
  "evidence": ["short content-specific evidence", ...],
  "confidence": integer 1-100
}}

Rules:
- Give content-specific scores, never a generic fixed score.
- Viral potential: hook, curiosity, emotion, surprise, quotability, conflict,
  clarity, payoff, standalone value and likely retention.
- Safety: distinguish reporting/education/discussion from promotion,
  instruction, glorification, threats, graphic detail or solicitation.
- Consider violence, threats, hate/harassment, sexual/adult content,
  self-harm, dangerous acts, regulated goods/drugs/weapons, gambling/fraud,
  misinformation, minor safety and privacy.
- A risky word mentioned neutrally is not automatically high risk.
- Do not claim certainty about TikTok enforcement or future views.

TITLE:
{title[:500]}

DESCRIPTION:
{description[:1200]}

TRANSCRIPT:
{text_value[:16000]}
""".strip()
        try:
            payload = _call_clipper_llm_sync(prompt)
            if isinstance(payload, dict):
                llm_viral = max(
                    1,
                    min(100, int(float(payload.get("viral_score", viral)))),
                )
                llm_risk = max(
                    1,
                    min(100, int(float(payload.get("guideline_risk", risk)))),
                )
                confidence = max(
                    1,
                    min(100, int(float(payload.get("confidence", 70)))),
                )

                viral = int(round(llm_viral * 0.72 + local_viral * 0.28))
                risk = int(round(llm_risk * 0.76 + local_risk * 0.24))
                viral_reasons = [
                    _clipper_clean_text(str(v), 120)
                    for v in payload.get("viral_reasons", [])
                    if str(v).strip()
                ][:5] or viral_reasons
                risk_reasons = [
                    _clipper_clean_text(str(v), 120)
                    for v in payload.get("risk_categories", [])
                    if str(v).strip()
                ][:5] or risk_reasons
                evidence = [
                    _clipper_clean_text(str(v), 180)
                    for v in payload.get("evidence", [])
                    if str(v).strip()
                ][:4] or evidence
                analysis_mode = (
                    "Semantic AI + local contextual analysis "
                    f"(confidence {confidence}%)"
                )
        except Exception as exc:
            logger.warning(
                "Semantic analyzer failed; local fallback used: %s",
                exc,
            )

    evidence_block = (
        "\n".join(f"• {item}" for item in evidence[:4])
        if evidence
        else "• No single high-confidence evidence snippet identified."
    )

    return (
        "🔎 TikTok Content Analysis\n\n"
        f"🔥 Estimated Viral Potential: {viral}/100 ({round(viral / 10, 1)}/10)\n"
        f"⚠️ Estimated Community Guidelines Risk: {risk}/100 ({round(risk / 10, 1)}/10)\n"
        f"🧠 Analysis: {analysis_mode}\n\n"
        "WHY IT MAY PERFORM:\n"
        f"{', '.join(viral_reasons[:5]) or 'No unusually strong viral signal detected.'}\n\n"
        "POLICY/RISK AREAS:\n"
        f"{', '.join(risk_reasons[:5]) or 'No strong transcript-level risk area detected.'}\n\n"
        "CONTENT EVIDENCE:\n"
        f"{evidence_block}\n\n"
        f"Transcript words analyzed: {len(text_value.split())}\n\n"
        "Important: This is a content-specific estimate, not an official TikTok "
        "pre-approval and not a guarantee of views or moderation outcome."
    )


async def handle_analyzer_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()
    action = (query.data or "").split(":", 1)[-1]
    if action == "youtube":
        context.user_data["mode"] = "analyzer_youtube"
        await safe_edit_message(
            query.message,
            "Send a YouTube link. The bot will extract/analyze its transcript.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="menu:analyzer")]]
            ),
        )
    elif action == "file":
        context.user_data["mode"] = "analyzer_file"
        await safe_edit_message(
            query.message,
            "Upload a TXT, PDF, or SRT transcript file.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="menu:analyzer")]]
            ),
        )


async def analyze_youtube_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
) -> None:
    if not update.message or not update.effective_user:
        return
    status = await update.message.reply_text("🔎 Preparing transcript for analysis...")
    cancel_event = threading.Event()
    try:
        info = await extract_info(url)
        job = {
            "job_id": uuid.uuid4().hex[:10],
            "type": "youtube",
            "owner_id": update.effective_user.id,
            "url": url,
            "title": str(info.get("title") or "YouTube Video"),
            "duration": optional_int(info.get("duration")),
            "created_at": time.time(),
            "choices": {},
        }
        async def progress(message: str) -> None:
            await safe_edit_message(status, message)
        output, error, cleanup = await create_transcript(
            job, "en", "txt", progress, cancel_event
        )
        if error or not output:
            await safe_edit_message(status, error or "Could not create transcript.")
            return
        value = output.read_text(encoding="utf-8", errors="ignore")
        report = await asyncio.to_thread(
            analyze_text_report,
            value,
            str(info.get("title") or ""),
            str(info.get("description") or ""),
        )
        await safe_edit_message(status, report)
        await cleanup_manager.schedule(cleanup + [output])
    except Exception as exc:
        logger.exception("YouTube analyzer failed")
        await safe_edit_message(status, f"Analyzer error: {str(exc)[:300]}")



def avclabs_ready() -> bool:
    return bool(AVCLABS_API_KEY and shutil.which(AVCLABS_MCP_COMMAND))


def _mcp_extract_payload(result: Any) -> Any:
    """Return useful JSON/text from a tools/call MCP result."""
    if not isinstance(result, dict):
        return result
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    content = result.get("content")
    if not isinstance(content, list):
        return result
    texts: List[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            texts.append(str(item.get("text") or ""))
        elif "data" in item:
            return item.get("data")
    text = "\n".join(value for value in texts if value).strip()
    if not text:
        return result
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Some MCP tools wrap JSON inside explanatory text.
        match = re.search(r"(\{.*\}|\[.*\])", text, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        return {"text": text}


class _AVCLabsMCPClient:
    """Small synchronous MCP stdio client used inside asyncio.to_thread()."""

    def __init__(self) -> None:
        env = os.environ.copy()
        # media-mcp uses API_KEY; enhance-mcp builds use HTTP_API_KEY.
        # Set both so package-version changes do not silently lose auth.
        env["API_KEY"] = AVCLABS_API_KEY
        env["HTTP_API_KEY"] = AVCLABS_API_KEY
        env["HTTP_API_BASE_URL"] = AVCLABS_HTTP_API_BASE_URL
        env["SAM3_API_BASE_URL"] = AVCLABS_SAM3_API_BASE_URL
        env["SAM3_POLL_INTERVAL"] = str(AVCLABS_SAM3_POLL_INTERVAL)
        env["SAM3_POLL_MAX_ATTEMPTS"] = str(AVCLABS_SAM3_POLL_MAX_ATTEMPTS)
        command = [AVCLABS_MCP_COMMAND]
        if Path(AVCLABS_MCP_COMMAND).name in {"npx", "npx.cmd"}:
            command.extend([
                "-y", AVCLABS_MCP_PACKAGE,
                "--api-key", AVCLABS_API_KEY,
                "--base-url", AVCLABS_HTTP_API_BASE_URL,
                "--sam3-base-url", AVCLABS_SAM3_API_BASE_URL,
            ])
        else:
            command.extend([
                "--api-key", AVCLABS_API_KEY,
                "--base-url", AVCLABS_HTTP_API_BASE_URL,
                "--sam3-base-url", AVCLABS_SAM3_API_BASE_URL,
            ])
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        self._next_id = 1

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def _send(self, payload: Dict[str, Any]) -> None:
        if not self.process.stdin:
            raise RuntimeError("AVCLabs MCP stdin is unavailable")
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def _read_response(self, request_id: int, timeout: int) -> Dict[str, Any]:
        if not self.process.stdout:
            raise RuntimeError("AVCLabs MCP stdout is unavailable")
        deadline = time.monotonic() + timeout
        import select
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                error = ""
                if self.process.stderr:
                    error = self.process.stderr.read()[-1200:]
                raise RuntimeError(f"AVCLabs MCP exited unexpectedly: {error or self.process.returncode}")
            remaining = max(0.1, deadline - time.monotonic())
            ready, _, _ = select.select([self.process.stdout], [], [], min(1.0, remaining))
            if not ready:
                continue
            line = self.process.stdout.readline()
            if not line:
                continue
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message.get("error") or {}
                raise RuntimeError(str(error.get("message") or error)[:1000])
            return message.get("result") or {}
        raise TimeoutError("AVCLabs MCP call timed out")

    def request(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._send({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        })
        return self._read_response(request_id, timeout or AVCLABS_MCP_CALL_TIMEOUT_SECONDS)

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "media-utility-telegram-bot", "version": "8.1"},
            },
        )
        self.notify("notifications/initialized")

    def call_tool(self, name: str, arguments: Dict[str, Any], timeout: Optional[int] = None) -> Any:
        result = self.request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout=timeout,
        )
        if result.get("isError"):
            raise RuntimeError(str(_mcp_extract_payload(result))[:1200])
        return _mcp_extract_payload(result)


def _nested_value(data: Any, *keys: str) -> Any:
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _find_first(data: Any, names: Iterable[str]) -> Any:
    wanted = {name.lower() for name in names}
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key).lower() in wanted and value not in (None, ""):
                return value
        for value in data.values():
            found = _find_first(value, wanted)
            if found not in (None, ""):
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_first(value, wanted)
            if found not in (None, ""):
                return found
    return None


def _download_avclabs_result(result_url: str, operation: str, input_path: Path) -> Path:
    suffix = Path(urlsplit(result_url).path).suffix or input_path.suffix or ".mp4"
    output = DOWNLOAD_DIR / f"avclabs_{operation}_{uuid.uuid4().hex[:8]}{suffix}"
    request = urllib.request.Request(result_url, headers={"User-Agent": "MediaUtilityBot/8.1"})
    with urllib.request.urlopen(request, timeout=1200) as response, output.open("wb") as fh:
        shutil.copyfileobj(response, fh)
    if not output.exists() or output.stat().st_size <= 0:
        raise RuntimeError("AVCLabs returned an empty output file")
    return output


def _avclabs_request_sync(input_path: Path, operation: str) -> Path:
    if not avclabs_ready():
        raise RuntimeError("AVCLABS_API_KEY is missing or npx is unavailable")
    if input_path.stat().st_size > AVCLABS_MAX_LOCAL_BYTES:
        raise RuntimeError(
            f"AVCLabs local upload limit is 100MB. File is {input_path.stat().st_size / 1024 / 1024:.1f}MB. "
            "Use the timestamp option to extract a smaller part first."
        )

    client = _AVCLabsMCPClient()
    try:
        client.initialize()
        if operation.startswith("video_"):
            resolution = operation.split("_", 1)[1]
            created = client.call_tool(
                "create_task",
                {
                    "video_source": str(input_path.resolve()),
                    "type": "local",
                    "resolution": resolution,
                },
                timeout=300,
            )
            if not bool(_find_first(created, {"success"}) if isinstance(created, dict) else True):
                raise RuntimeError(str(_find_first(created, {"error", "error_message", "message"}) or created))
            task_id = str(_find_first(created, {"task_id", "taskId", "id"}) or "").strip()
            result_url = str(_find_first(created, {"video_url", "output_url", "result_url", "url"}) or "").strip()
            if not task_id and not result_url:
                raise RuntimeError(f"AVCLabs create_task returned no task_id: {created}")
            started = time.monotonic()
            while not result_url:
                if time.monotonic() - started > AVCLABS_TIMEOUT_SECONDS:
                    raise TimeoutError("AVCLabs video enhancement timed out")
                time.sleep(AVCLABS_POLL_SECONDS)
                status = client.call_tool("get_task_status", {"task_id": task_id}, timeout=120)
                state = str(_find_first(status, {"status", "state"}) or "").lower()
                if state in {"failed", "error", "cancelled", "canceled"}:
                    raise RuntimeError(str(_find_first(status, {"error_message", "error", "message"}) or status))
                result_url = str(_find_first(status, {"video_url", "output_url", "result_url", "url"}) or "").strip()
            return _download_avclabs_result(result_url, operation, input_path)

        if operation == "sam3_objects":
            result = client.call_tool(
                "sam3_predict",
                {
                    "imagePath": str(input_path.resolve()),
                    "prompt": "find and segment all visible objects",
                },
                timeout=max(90, (AVCLABS_SAM3_POLL_INTERVAL * AVCLABS_SAM3_POLL_MAX_ATTEMPTS // 1000) + 30),
            )
            output = DOWNLOAD_DIR / f"avclabs_sam3_{uuid.uuid4().hex[:8]}.json"
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return output

        raise RuntimeError("Unsupported AVCLabs MCP operation")
    finally:
        client.close()


async def handle_avclabs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()
    operation = (query.data or "").split(":", 1)[-1]
    if operation not in AVCLABS_OPERATIONS:
        return
    context.user_data["mode"] = f"avclabs:{operation}"
    media_type = "image" if operation == "sam3_objects" else "video"
    status_text = "configured" if avclabs_ready() else "missing API key or npx"
    note = "Maximum local upload: 100MB." if operation.startswith("video_") else "PNG/JPG/WebP recommended."
    await safe_edit_message(
        query.message,
        f"✨ {AVCLABS_OPERATIONS[operation]}\n\n"
        f"Upload the {media_type} file now.\n{note}\n\nMCP status: {status_text}",
    )


def romanize_urdu(value: str) -> str:
    table = str.maketrans({"ا":"a","آ":"aa","ب":"b","پ":"p","ت":"t","ٹ":"t","ث":"s","ج":"j","چ":"ch","ح":"h","خ":"kh","د":"d","ڈ":"d","ذ":"z","ر":"r","ڑ":"r","ز":"z","ژ":"zh","س":"s","ش":"sh","ص":"s","ض":"z","ط":"t","ظ":"z","ع":"a","غ":"gh","ف":"f","ق":"q","ک":"k","گ":"g","ل":"l","م":"m","ن":"n","ں":"n","و":"o","ؤ":"o","ہ":"h","ھ":"h","ء":"","ی":"y","ے":"e","ئ":"y","َ":"a","ِ":"i","ُ":"u","ّ":"","ْ":""})
    return re.sub(r"\s+", " ", value.translate(table)).strip()


def create_roman_transcript_txt(title: str, language_code: str, english: List[TranscriptParagraph], urdu: List[TranscriptParagraph]) -> Path:
    path = DOWNLOAD_DIR / f"{safe_filename(title)}_{language_code}_{uuid.uuid4().hex[:8]}.txt"
    lines = [title, ""]
    for i, paragraph in enumerate(english):
        u = urdu[i].text if i < len(urdu) else ""
        r = romanize_urdu(u)
        lines.append(f"[{timestamp_range(paragraph.start, paragraph.end)}]")
        if language_code == "ru": lines.append(r)
        elif language_code == "er": lines.extend([paragraph.text, r])
        else: lines.extend([u, r])
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path



# =============================================================================
# Admin Channel Studio - private per-channel editing/viral profiles
# =============================================================================

ADMIN_CHANNELS_FILE = AUTH_DIR / "admin_channels.json"
ADMIN_CHANNELS_LOCK = threading.RLock()

def _read_admin_channels() -> Dict[str, Any]:
    with ADMIN_CHANNELS_LOCK:
        try:
            if not ADMIN_CHANNELS_FILE.exists():
                return {"channels": {}}
            data = json.loads(ADMIN_CHANNELS_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"channels": {}}
            data.setdefault("channels", {})
            return data
        except Exception:
            logger.exception("Could not read Admin Channel profiles")
            return {"channels": {}}

def _write_admin_channels(data: Dict[str, Any]) -> None:
    with ADMIN_CHANNELS_LOCK:
        ADMIN_CHANNELS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = ADMIN_CHANNELS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(ADMIN_CHANNELS_FILE)
        os.chmod(ADMIN_CHANNELS_FILE, 0o600)

def get_admin_channel(channel_id: str) -> Optional[Dict[str, Any]]:
    item = _read_admin_channels().get("channels", {}).get(str(channel_id))
    return dict(item) if isinstance(item, dict) else None

def create_admin_channel(name: str) -> Dict[str, Any]:
    clean = re.sub(r"\s+", " ", str(name or "")).strip()[:80]
    if not clean:
        raise ValueError("Channel name is empty")
    data = _read_admin_channels()
    channel_id = uuid.uuid4().hex[:8]
    now = time.time()
    profile = {
        "id": channel_id,
        "name": clean,
        "created_at": now,
        "updated_at": now,
        "reference_count": 0,
        "ready": False,
        "caption_template": "channel_reference",
        "editing_style": {
            "speaker_crop": "tight_clean",
            "movement": "minimal_smooth",
            "split_screen": False,
            "caption_words": 3,
            "caption_size": 96,
            "caption_position": "lower_middle",
            "hook_card": "white",
            "hook_lines": 2,
            "hard_cut_on_speaker_change": True,
        },
        # Baseline learned from the successful reference set supplied by owner.
        # Further uploaded training references expand these terms privately.
        "viral_terms": [
            "police","truth","wrong","fight","fought","death","died","love",
            "hate","scared","childhood","immigrant","immigration","gender",
            "interview","embarrassing","bullshit","evidence","family","club",
        ],
        "viral_phrases": [
            "passed away","broke down","do you know","right now","if you are",
            "i can't","i remember","the truth","what happened","you know",
        ],
        "reference_metrics": [],
    }
    data["channels"][channel_id] = profile
    _write_admin_channels(data)
    return profile

def update_admin_channel(profile: Dict[str, Any]) -> None:
    data = _read_admin_channels()
    profile = dict(profile)
    profile["updated_at"] = time.time()
    data.setdefault("channels", {})[str(profile["id"])] = profile
    _write_admin_channels(data)

def delete_admin_channel(channel_id: str) -> bool:
    data = _read_admin_channels()
    removed = data.setdefault("channels", {}).pop(str(channel_id), None)
    if removed is not None:
        _write_admin_channels(data)
        return True
    return False

def admin_channel_menu() -> InlineKeyboardMarkup:
    channels = list(_read_admin_channels().get("channels", {}).values())
    channels = [x for x in channels if isinstance(x, dict)]
    channels.sort(key=lambda x: str(x.get("name") or "").lower())
    rows = [[InlineKeyboardButton("➕ Add New Channel", callback_data="channel:add")]]
    for item in channels:
        icon = "✅" if item.get("ready") else "🧪"
        rows.append([
            InlineKeyboardButton(
                f"{icon} {str(item.get('name') or 'Channel')[:38]}",
                callback_data=f"channel:open:{item.get('id')}",
            )
        ])
    rows.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)

def admin_channel_detail_menu(channel_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✂️ AI Clipper", callback_data=f"channel:clipper:{channel_id}"),
            InlineKeyboardButton("⏱ Timestamp Editor", callback_data=f"channel:editor:{channel_id}"),
        ],
        [
            InlineKeyboardButton("🎓 Add Reference Videos", callback_data=f"channel:train:{channel_id}"),
            InlineKeyboardButton("📊 Profile", callback_data=f"channel:profile:{channel_id}"),
        ],
        [
            InlineKeyboardButton("🗑 Delete", callback_data=f"channel:delete:{channel_id}"),
            InlineKeyboardButton("⬅️ Channels", callback_data="channel:home"),
        ],
    ])

def channel_editor_source_menu(channel_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 File + Timestamp", callback_data=f"channel:edfile:{channel_id}")],
        [InlineKeyboardButton("🔗 YouTube + Timestamp", callback_data=f"channel:edyoutube:{channel_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"channel:open:{channel_id}")],
    ])

def _extract_reference_terms(text_value: str) -> Tuple[List[str], List[str]]:
    low = normalize_transcript_text(text_value).lower()
    words = re.findall(r"[a-z][a-z']{3,}", low)
    stop = {
        "that","this","with","have","from","they","there","their","what","when",
        "where","would","could","should","about","because","just","really","like",
        "were","been","being","your","you're","youre","then","than","into","some",
        "them","very","also","only","going","know","think","thing","things",
    }
    counts: Dict[str,int] = {}
    for word in words:
        if word in stop:
            continue
        counts[word] = counts.get(word, 0) + 1
    terms = [k for k,v in sorted(counts.items(), key=lambda item:item[1], reverse=True) if v >= 2][:24]

    phrase_candidates = re.findall(r"\b(?:i|we|you|they|she|he|my|our)\s+[a-z']+(?:\s+[a-z']+){1,3}", low)
    phrase_counts: Dict[str,int] = {}
    for phrase in phrase_candidates:
        phrase = re.sub(r"\s+", " ", phrase).strip()
        phrase_counts[phrase] = phrase_counts.get(phrase,0)+1
    phrases = [k for k,v in sorted(phrase_counts.items(), key=lambda item:item[1], reverse=True)][:12]
    return terms, phrases

def analyze_admin_channel_reference(path: Path, cancel_event: threading.Event) -> Dict[str, Any]:
    """Analyze reference edit rhythm + transcript. No OCR or external cloud AI required."""
    media = probe_media(path)
    result: Dict[str, Any] = {
        "duration": float(media.get("duration") or 0),
        "width": int(media.get("width") or 0),
        "height": int(media.get("height") or 0),
        "fps": 0.0,
        "cut_interval": 0.0,
        "face_area_ratio": 0.0,
        "face_y_ratio": 0.0,
        "terms": [],
        "phrases": [],
    }

    # Visual edit fingerprint.
    try:
        import cv2  # type: ignore
        cap = cv2.VideoCapture(str(path))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        result["fps"] = round(fps, 2)
        duration = max(1.0, result["duration"])
        sample_step = 0.8
        last_gray = None
        cut_times: List[float] = []
        face_areas: List[float] = []
        face_ys: List[float] = []
        detector, detector_kind = _create_face_detector(cv2)

        t = 0.0
        while t < duration:
            ensure_not_cancelled(cancel_event)
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                t += sample_step
                continue
            h, w = frame.shape[:2]
            small = cv2.resize(frame, (160, 284), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            if last_gray is not None:
                change = float(cv2.absdiff(gray, last_gray).mean())
                if change >= 28.0:
                    cut_times.append(t)
            last_gray = gray

            try:
                faces = _detect_faces_for_reframe(frame, detector, detector_kind, cv2)
                if faces:
                    # Detector returns normalized center-x, center-y and face area.
                    cx, cy, area = max(faces, key=lambda item: item[2])
                    face_areas.append(float(area))
                    face_ys.append(float(cy))
            except Exception:
                pass
            t += sample_step
        cap.release()
        if len(cut_times) >= 2:
            intervals=[b-a for a,b in zip(cut_times,cut_times[1:]) if b>a]
            if intervals:
                result["cut_interval"]=round(sum(intervals)/len(intervals),2)
        if face_areas:
            result["face_area_ratio"]=round(sum(face_areas)/len(face_areas),4)
        if face_ys:
            result["face_y_ratio"]=round(sum(face_ys)/len(face_ys),4)
    except Exception as exc:
        logger.warning("Reference visual analysis fallback: %s", exc)

    # Transcript fingerprint for private viral ranking.
    audio = DOWNLOAD_DIR / f"channelref_audio_{uuid.uuid4().hex[:8]}.wav"
    try:
        run_process(
            [
                "ffmpeg","-y","-hide_banner","-loglevel","error",
                "-i",str(path),"-vn","-ac","1","-ar","16000","-c:a","pcm_s16le",str(audio),
            ],
            cancel_event=cancel_event,
            error_prefix="Reference audio extraction failed",
            timeout_seconds=1200,
        )
        paragraphs, _ = run_transcription(audio, cancel_event)
        full = normalize_transcript_text(" ".join(p.text for p in paragraphs))
        terms, phrases = _extract_reference_terms(full)
        result["terms"] = terms
        result["phrases"] = phrases
        result["transcript_sample"] = full[:1200]
    except Exception as exc:
        logger.warning("Reference transcript analysis fallback: %s", exc)
    finally:
        audio.unlink(missing_ok=True)

    return result

def merge_admin_reference_analysis(profile: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
    profile = dict(profile)
    metrics = list(profile.get("reference_metrics") or [])
    metrics.append(analysis)
    metrics = metrics[-3:]
    profile["reference_metrics"] = metrics
    profile["reference_count"] = len(metrics)
    profile["ready"] = len(metrics) >= 2

    terms = list(profile.get("viral_terms") or [])
    phrases = list(profile.get("viral_phrases") or [])
    for item in analysis.get("terms") or []:
        if item not in terms:
            terms.append(item)
    for item in analysis.get("phrases") or []:
        if item not in phrases:
            phrases.append(item)
    profile["viral_terms"] = terms[-80:]
    profile["viral_phrases"] = phrases[-40:]

    cut_values=[float(x.get("cut_interval") or 0) for x in metrics if float(x.get("cut_interval") or 0)>0]
    face_values=[float(x.get("face_area_ratio") or 0) for x in metrics if float(x.get("face_area_ratio") or 0)>0]
    if cut_values:
        profile.setdefault("editing_style", {})["reference_cut_interval"] = round(sum(cut_values)/len(cut_values),2)
    if face_values:
        profile.setdefault("editing_style", {})["reference_face_area"] = round(sum(face_values)/len(face_values),4)
    return profile

def channel_profile_text(profile: Dict[str, Any]) -> str:
    metrics = profile.get("reference_metrics") or []
    cut = (profile.get("editing_style") or {}).get("reference_cut_interval")
    face = (profile.get("editing_style") or {}).get("reference_face_area")
    return (
        f"🎛 {profile.get('name','Channel')}\\n\\n"
        f"References analyzed: {len(metrics)}/3\\n"
        f"Training: {'✅ Ready' if profile.get('ready') else '🧪 Need at least 2 reference videos'}\\n"
        "Fixed edit: close active-speaker crop, minimal smooth movement, no decorative split-screen, "
        "large 2-line uppercase captions with black outline + red emphasis, white hook card.\\n"
        f"Learned cut interval: {cut if cut else '-'} sec\\n"
        f"Learned face occupancy: {face if face else '-'}\\n"
        f"Private viral terms learned: {len(profile.get('viral_terms') or [])}\\n\\n"
        "This profile affects ONLY this Admin Channel."
    )

async def handle_admin_channel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not query.message or not user:
        return
    if not is_owner(update):
        await query.answer("Only for admin.", show_alert=True)
        return
    await query.answer()
    parts=(query.data or "").split(":")
    action=parts[1] if len(parts)>1 else "home"
    channel_id=parts[2] if len(parts)>2 else ""

    if action=="home":
        context.user_data.pop("admin_channel_profile_id",None)
        context.user_data.pop("mode",None)
        await safe_edit_message(query.message,"🎛 Admin Channel Studio\\n\\nEach channel keeps its own editing + viral profile.",reply_markup=admin_channel_menu())
        return
    if action=="add":
        context.user_data["mode"]="admin_channel_name"
        await safe_edit_message(query.message,"➕ Send the new channel name.\\n\\nExample: Liam Tuff")
        return
    profile=get_admin_channel(channel_id)
    if not profile:
        await query.answer("Channel profile not found.",show_alert=True)
        return

    if action=="open":
        await safe_edit_message(query.message,channel_profile_text(profile),reply_markup=admin_channel_detail_menu(channel_id))
    elif action=="profile":
        await safe_edit_message(query.message,channel_profile_text(profile),reply_markup=admin_channel_detail_menu(channel_id))
    elif action=="train":
        context.user_data["mode"]="admin_channel_train"
        context.user_data["admin_channel_profile_id"]=channel_id
        await safe_edit_message(
            query.message,
            f"🎓 Training {profile.get('name')}\\n\\nUpload 2–3 successful reference videos. "
            "I will analyze framing, edit rhythm, speaker crop and transcript/topic signals.\\n\\n"
            f"Already analyzed: {profile.get('reference_count',0)}/3",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back",callback_data=f"channel:open:{channel_id}")]]),
        )
    elif action=="clipper":
        if ai_service_gate is not None and not await ai_service_gate(update, context):
            return
        if not profile.get("ready"):
            await query.answer("Train with at least 2 reference videos first.",show_alert=True)
            return
        if not (VIRALFORGE_AVAILABLE and VIRALFORGE_DEEP_ENABLED):
            await query.answer("Deep Viral Research module is not installed/configured.", show_alert=True)
            return
        context.user_data["mode"]="auto_clipper"
        context.user_data["auto_clipper_analysis_mode"]="deep"
        context.user_data["admin_channel_profile_id"]=channel_id
        await safe_edit_message(
            query.message,
            f"✂️ {profile.get('name')} — Private AI Clipper\\n\\n"
            "Send a YouTube link or upload the long source video. "
            "The highest available YouTube source (up to 4K) will be used. "
            "Viral ranking + editing stay locked to this channel profile.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back",callback_data=f"channel:open:{channel_id}")]]),
        )
    elif action=="editor":
        if ai_service_gate is not None and not await ai_service_gate(update, context):
            return
        if not profile.get("ready"):
            await query.answer("Train with at least 2 reference videos first.",show_alert=True)
            return
        context.user_data["admin_channel_profile_id"]=channel_id
        await safe_edit_message(
            query.message,
            f"⏱ {profile.get('name')} — Timestamp Video Editor\\n\\nChoose source:",
            reply_markup=channel_editor_source_menu(channel_id),
        )
    elif action=="edfile":
        context.user_data["admin_channel_profile_id"]=channel_id
        context.user_data["mode"]="video_editor_file_timestamp"
        await safe_edit_message(query.message,"📤 Upload the source video. After upload I will ask for the timestamp range.")
    elif action=="edyoutube":
        context.user_data["admin_channel_profile_id"]=channel_id
        context.user_data["mode"]="video_editor_youtube"
        await safe_edit_message(query.message,"🔗 Send YouTube link + timestamp, e.g.\\nhttps://youtu.be/VIDEO_ID  00:30 - 01:20")
    elif action=="delete":
        delete_admin_channel(channel_id)
        context.user_data.pop("admin_channel_profile_id",None)
        context.user_data.pop("mode",None)
        await safe_edit_message(query.message,"✅ Channel profile deleted.",reply_markup=admin_channel_menu())


# =============================================================================
# Incoming text and media
# =============================================================================

async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message or not update.effective_user:
        return

    if context.user_data.get("mode") == "admin_channel_name":
        if not is_owner(update):
            context.user_data.pop("mode", None)
            await update.message.reply_text("Only for admin.")
            return
        try:
            profile = create_admin_channel(update.message.text or "")
        except Exception as exc:
            await update.message.reply_text(f"Channel name error: {str(exc)[:180]}")
            return
        context.user_data["mode"] = "admin_channel_train"
        context.user_data["admin_channel_profile_id"] = profile["id"]
        await update.message.reply_text(
            f"✅ Channel saved: {profile['name']}\n\n"
            "Now upload 2–3 successful videos from this channel. "
            "The profile will learn only from these references."
        )
        return

    if context.user_data.get("mode") == "support_feedback_wait":
        context.user_data.pop("mode", None)
        sent = await _send_support_to_owner(update, context, update.message.text or "")
        await update.message.reply_text(
            ("✅ Report admin ko send ho gayi. " if sent else "❌ Report send nahi ho saki. ")
            + f"Admin: @{OWNER_USERNAME}"
        )
        return

    if is_owner(update):
        action = context.user_data.get("admin_action")
        if action == "proxies":
            added, skipped = runtime_auth.add_proxies(
                update.message.text or ""
            )
            context.user_data.pop("admin_action", None)
            await delete_sensitive_message(update.message)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    f"Proxy pool updated. Added: {added}, skipped: {skipped}. "
                    f"Total: {runtime_auth.proxy_count()}."
                ),
            )
            return
        if action == "cookies":
            await update.message.reply_text(
                "Upload cookies.txt as a document, not as text."
            )
            return

    if context.user_data.get("mode") == "video_editor_file_timestamp_wait":
        pending = context.user_data.get("pending_editor_file") or {}
        match = re.search(r"(?P<start>\d+(?::\d{1,2}){1,2}|\d+(?:\.\d+)?)\s*(?:-|–|—|to)\s*(?P<end>\d+(?::\d{1,2}){1,2}|\d+(?:\.\d+)?)", update.message.text or "", flags=re.I)
        if not match:
            await update.message.reply_text("Send timestamp like 00:30 - 01:20")
            return
        start = _parse_editor_timestamp_token(match.group("start")); end = _parse_editor_timestamp_token(match.group("end"))
        if start is None or end is None or end <= start:
            await update.message.reply_text("Invalid timestamp range.")
            return
        source = Path(str(pending.get("path") or ""))
        if not source.exists():
            await update.message.reply_text("Uploaded file expired. Upload it again.")
            return
        output = DOWNLOAD_DIR / f"editor_range_{uuid.uuid4().hex[:8]}.mp4"
        status = await update.message.reply_text("✂️ Extracting requested timestamp range...")
        try:
            await asyncio.to_thread(run_process, ["ffmpeg","-y","-ss",str(start),"-to",str(end),"-i",str(source),"-map","0:v:0","-map","0:a?","-c:v","libx264","-preset","veryfast","-c:a","aac","-movflags","+faststart",str(output)], threading.Event(), "Timestamp extraction failed", 3600)
            media = await asyncio.to_thread(probe_media, output)
            job_id = save_video_editor_job(context, update.effective_user.id, output, source.name, media)
            context.user_data["mode"] = "video_editor_file"
            context.user_data.pop("pending_editor_file", None)
            await safe_edit_message(status, "Timestamp clip ready. Choose caption/edit style.", reply_markup=video_editor_template_menu(job_id))
            await cleanup_manager.schedule([source, output], minutes=360)
        except Exception as exc:
            await safe_edit_message(status, f"Timestamp extraction failed: {str(exc)[:240]}")
        return

    url = extract_youtube_url(update.message.text or "")
    if not url:
        mode = context.user_data.get("mode")
        if mode == "enhance":
            await update.message.reply_text(
                "Upload a video file, not a text message."
            )
        elif mode in {"video_editor", "video_editor_file", "video_editor_file_timestamp"}:
            await update.message.reply_text(
                "Upload a video file for AI Video Editor."
            )
        elif mode == "video_editor_youtube":
            await update.message.reply_text(
                "Send a YouTube link with a timestamp range, for example:\n"
                "https://youtu.be/VIDEO_ID  00:30 - 01:20"
            )
        elif mode == "auto_clipper":
            await update.message.reply_text(
                "Send a valid YouTube link for Auto Clipper Pro."
            )
        else:
            await update.message.reply_text(
                f"Send a valid YouTube link or use /start.\n\n{owner_footer()}"
            )
        return

    if not is_owner(update):
        allowed, remaining = request_limiter.allow(update.effective_user.id)
        if not allowed:
            await update.message.reply_text(
                f"Please wait {max(1, int(remaining + 0.999))} seconds."
            )
            return

    mode = context.user_data.get("mode", "download")

    if mode == "analyzer_youtube":
        url = extract_youtube_url(update.message.text or "")
        if not url:
            await update.message.reply_text("Send a valid YouTube link.")
            return
        await analyze_youtube_request(update, context, url)
        return

    if mode == "timestamp_download":
        parsed_url, start_value, end_value = parse_editor_youtube_request(
            update.message.text or ""
        )
        if not parsed_url or start_value is None or end_value is None:
            await update.message.reply_text(
                "Send link and range like:\n"
                "https://youtu.be/VIDEO_ID  10:30 - 11:30"
            )
            return
        await prepare_timestamp_download_job(
            update, context, parsed_url, start_value, end_value
        )
        return

    if mode == "video_editor_youtube":
        parsed_url, start_value, end_value = parse_editor_youtube_request(
            update.message.text or ""
        )
        if not parsed_url or start_value is None or end_value is None:
            await update.message.reply_text(
                "Send the YouTube link and exact range like this:\n"
                "https://youtu.be/VIDEO_ID  00:30 - 01:20"
            )
            return
        await prepare_youtube_editor_job(
            update,
            context,
            parsed_url,
            start_value,
            end_value,
        )
        return

    status = await update.message.reply_text("Reading the YouTube link...")

    if mode == "auto_clipper":
        if not is_auto_clipper_allowed(update.effective_user.id):
            await safe_edit_message(
                status,
                "Auto Clipper Pro access is currently disabled for normal users.",
            )
            return
        try:
            info = await extract_info(url)
            duration = optional_int(info.get("duration"))
            if duration and duration > MAX_TRANSCRIPT_SECONDS:
                await safe_edit_message(
                    status,
                    "This video is too long for Auto Clipper analysis. "
                    f"Maximum duration: {MAX_TRANSCRIPT_SECONDS // 60} minutes.",
                )
                return
            choices = build_quality_choices(info)
            if not choices:
                await safe_edit_message(
                    status,
                    "YouTube did not return a supported source video format.",
                )
                return
            job_id = save_youtube_job(
                context,
                update.effective_user.id,
                url,
                info,
                choices,
            )
            jobs(context)[job_id]["type"] = "auto_clipper"
            jobs(context)[job_id]["analysis_mode"] = str(context.user_data.get("auto_clipper_analysis_mode") or "quick")
            channel_id = str(context.user_data.get("admin_channel_profile_id") or "")
            channel_profile = get_admin_channel(channel_id) if channel_id and is_owner(update) else None
            if channel_profile:
                jobs(context)[job_id]["admin_channel_profile"] = channel_profile
                jobs(context)[job_id]["channel_name"] = str(channel_profile.get("name") or "")
            await safe_edit_message(
                status,
                f"✂️ {str(info.get('title') or 'YouTube Video')[:170]}\n\n"
                "Choose the clip duration. The bot will detect the best viral "
                "moments automatically and score each one out of 100.\n\n"
                f"{owner_footer()}",
                reply_markup=auto_clipper_duration_menu(job_id),
            )
        except Exception as exc:
            logger.exception("Could not prepare Auto Clipper job")
            await safe_edit_message(
                status,
                f"Could not read this video: {str(exc)[:280]}",
            )
        return

    if mode == "transcript":
        try:
            info = await extract_info(url)
            duration = optional_int(info.get("duration"))
            if duration and duration > MAX_TRANSCRIPT_SECONDS:
                await safe_edit_message(
                    status,
                    "This video is too long for the transcript service. "
                    f"Maximum duration: {MAX_TRANSCRIPT_SECONDS // 60} minutes.",
                )
                return
            job_id = save_transcript_only_job(
                context,
                update.effective_user.id,
                url,
                str(info.get("title") or "YouTube Video"),
                duration,
            )
            await safe_edit_message(
                status,
                f"{str(info.get('title') or 'YouTube Video')[:180]}\n\n"
                "Select transcript language and file format.\n\n"
                f"{owner_footer()}",
                reply_markup=transcript_menu(job_id),
            )
        except Exception as exc:
            await safe_edit_message(
                status,
                f"Could not read this video: {str(exc)[:280]}",
            )
        return

    try:
        info = await extract_info(url)
        choices = build_quality_choices(info)
        if not choices:
            await safe_edit_message(
                status,
                "YouTube did not return supported video formats. "
                "The administrator should refresh cookies or proxies.",
            )
            return

        job_id = save_youtube_job(
            context,
            update.effective_user.id,
            url,
            info,
            choices,
        )
        text, keyboard = quality_menu(job_id, choices)
        await safe_edit_message(
            status,
            f"{str(info.get('title') or 'YouTube Video')[:180]}\n\n"
            f"{text}\n\n"
            "You can also create an English or Urdu transcript.\n\n"
            f"{owner_footer()}",
            reply_markup=keyboard,
        )
    except Exception as exc:
        logger.exception("Could not read YouTube video")
        await safe_edit_message(
            status,
            f"Could not read this video: {str(exc)[:280]}",
        )


def is_video_document(document) -> bool:
    if not document:
        return False
    mime = str(document.mime_type or "").lower()
    suffix = Path(str(document.file_name or "")).suffix.lower()
    return mime.startswith("video/") or suffix in VIDEO_EXTENSIONS


async def handle_media(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message or not update.effective_user:
        return

    message = update.message
    document = message.document

    if context.user_data.get("mode") == "admin_channel_train":
        if not is_owner(update):
            context.user_data.pop("mode", None)
            await message.reply_text("Only for admin.")
            return
        channel_id = str(context.user_data.get("admin_channel_profile_id") or "")
        profile = get_admin_channel(channel_id)
        if not profile:
            context.user_data.pop("mode", None)
            await message.reply_text("Channel profile expired. Open Admin Channel Studio again.")
            return
        train_video = message.video
        train_doc = message.document
        if not train_video and not is_video_document(train_doc):
            await message.reply_text("Upload a video file for channel training.")
            return
        count = int(profile.get("reference_count") or 0)
        if count >= 3:
            context.user_data.pop("mode", None)
            await message.reply_text("This channel already has 3 reference videos.", reply_markup=admin_channel_detail_menu(channel_id))
            return
        original = str(getattr(train_doc, "file_name", "") or f"reference_{message.message_id}.mp4")
        suffix = Path(original).suffix.lower()
        if suffix not in VIDEO_EXTENSIONS:
            suffix = ".mp4"
        path = DOWNLOAD_DIR / f"channelref_{channel_id}_{uuid.uuid4().hex[:8]}{suffix}"
        status = await message.reply_text(f"🎓 Analyzing reference {count+1}/3 for {profile.get('name')}...")
        try:
            tg = await (train_video or train_doc).get_file()
            await tg.download_to_drive(custom_path=path)
            analysis = await asyncio.to_thread(analyze_admin_channel_reference, path, threading.Event())
            profile = merge_admin_reference_analysis(profile, analysis)
            update_admin_channel(profile)
            remaining = max(0, 2 - int(profile.get("reference_count") or 0))
            if profile.get("ready"):
                text = (
                    f"✅ Reference analyzed ({profile.get('reference_count')}/3).\n"
                    "Channel profile is now READY. You may upload one more reference for tighter learning, "
                    "or open the channel and start editing."
                )
            else:
                text = f"✅ Reference analyzed. Upload {remaining} more successful video(s) to activate this channel profile."
            await safe_edit_message(status, text, reply_markup=admin_channel_detail_menu(channel_id) if profile.get("ready") else None)
            if int(profile.get("reference_count") or 0) >= 3:
                context.user_data.pop("mode", None)
        except Exception as exc:
            logger.exception("Admin channel training failed")
            await safe_edit_message(status, f"Reference analysis failed: {str(exc)[:280]}")
        finally:
            path.unlink(missing_ok=True)
        return

    if (
        is_owner(update)
        and context.user_data.get("admin_action") == "cookies"
        and document
    ):
        await save_cookie_document(message, document, context)
        return

    video = message.video

    mode_now = str(context.user_data.get("mode") or "")
    if context.user_data.get("mode") == "analyzer_file" and document:
        suffix = Path(str(document.file_name or "")).suffix.lower()
        if suffix not in {".txt", ".pdf", ".srt"}:
            await message.reply_text("Upload only TXT, PDF, or SRT.")
            return
        path = DOWNLOAD_DIR / f"analyzer_{uuid.uuid4().hex[:8]}{suffix}"
        status = await message.reply_text("🔎 Reading transcript file...")
        try:
            tg_file = await document.get_file()
            await tg_file.download_to_drive(custom_path=path)
            value = await asyncio.to_thread(read_transcript_document, path)
            report = await asyncio.to_thread(analyze_text_report, value)
            await safe_edit_message(status, report)
        except Exception as exc:
            await safe_edit_message(status, f"Analyzer error: {str(exc)[:300]}")
        finally:
            path.unlink(missing_ok=True)
        return

    if not video and not is_video_document(document):
        await message.reply_text(
            f"Use /start and upload a supported video file.\n\n{owner_footer()}"
        )
        return

    mode = context.user_data.get("mode", "enhance")
    is_editor_mode = mode in {"video_editor", "video_editor_file", "video_editor_file_timestamp"}
    is_clipper_mode = mode == "auto_clipper"
    if (is_editor_mode or is_clipper_mode) and ai_service_gate is not None:
        if not await ai_service_gate(update, context):
            return
    if is_editor_mode:
        record_user_activity(update.effective_user, "AI Video Editor", "Uploaded video file")
    elif is_clipper_mode:
        record_user_activity(update.effective_user, "Auto Clipper Pro", "Uploaded long video file")

    if is_editor_mode and not is_video_editor_allowed(
        update.effective_user.id
    ):
        await message.reply_text(
            paid_access_text("editor", update.effective_user.id)
        )
        return

    file_size = (
        video.file_size if video else document.file_size
    ) or 0
    max_input_bytes = (
        MAX_VIDEO_EDITOR_INPUT_BYTES
        if (is_editor_mode or is_clipper_mode)
        else MAX_ENHANCE_INPUT_BYTES
    )
    if file_size > max_input_bytes:
        await message.reply_text(
            "The input video is too large. "
            f"Maximum input: {human_bytes(max_input_bytes)}."
        )
        return

    status = await message.reply_text(
        "Receiving the video for AI editing..."
        if is_editor_mode
        else ("Receiving the long video for Auto Clipper..." if is_clipper_mode else "Receiving the video...")
    )
    original_name = (
        str(document.file_name)
        if document and document.file_name
        else f"telegram_video_{message.message_id}.mp4"
    )
    suffix = Path(original_name).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        suffix = ".mp4"
    input_path = DOWNLOAD_DIR / (
        f"input_{update.effective_user.id}_{uuid.uuid4().hex[:8]}{suffix}"
    )

    try:
        tg_file = await (video or document).get_file()
        await tg_file.download_to_drive(custom_path=input_path)
        media = await asyncio.to_thread(probe_media, input_path)
        duration = float(media.get("duration") or 0)
        if duration <= 0:
            raise RuntimeError("The video duration could not be detected")

        if is_clipper_mode:
            if not is_auto_clipper_allowed(update.effective_user.id):
                input_path.unlink(missing_ok=True)
                await safe_edit_message(status, paid_access_text("clipper", update.effective_user.id))
                return
            if duration > MAX_TRANSCRIPT_SECONDS:
                input_path.unlink(missing_ok=True)
                await safe_edit_message(
                    status,
                    "This uploaded video is too long for Auto Clipper analysis. "
                    f"Maximum duration: {MAX_TRANSCRIPT_SECONDS // 60} minutes.",
                )
                return
            job_id = uuid.uuid4().hex[:10]
            jobs(context)[job_id] = {
                "job_id": job_id,
                "type": "auto_clipper",
                "source_type": "local",
                "owner_id": update.effective_user.id,
                "input_path": str(input_path.resolve()),
                "title": original_name,
                "video_id": job_id,
                "duration": int(duration),
                "created_at": time.time(),
                "choices": {},
            }
            channel_id = str(context.user_data.get("admin_channel_profile_id") or "")
            channel_profile = get_admin_channel(channel_id) if channel_id and is_owner(update) else None
            if channel_profile:
                jobs(context)[job_id]["admin_channel_profile"] = channel_profile
                jobs(context)[job_id]["channel_name"] = str(channel_profile.get("name") or "")
            jobs(context)[job_id]["analysis_mode"] = str(context.user_data.get("auto_clipper_analysis_mode") or "quick")
            await safe_edit_message(
                status,
                "✂️ Long video received for Auto Clipper.\n\n"
                f"Resolution: {media['width']}x{media['height']}\n"
                f"Duration: {format_time(duration)}\n"
                f"Size: {human_bytes(input_path.stat().st_size)}\n\n"
                "Choose clip duration. The bot will return only the 10 highest-scoring unique clips.",
                reply_markup=auto_clipper_duration_menu(job_id),
            )
            await cleanup_manager.schedule([input_path], minutes=720)
            return

        if mode == "video_editor_file_timestamp":
            context.user_data["pending_editor_file"] = {"path": str(input_path), "name": original_name, "duration": duration}
            context.user_data["mode"] = "video_editor_file_timestamp_wait"
            await safe_edit_message(status, f"File received ({format_time(duration)}). Now send the range, for example: 00:30 - 01:20")
            await cleanup_manager.schedule([input_path], minutes=360)
            return

        if is_editor_mode:
            if duration > MAX_VIDEO_EDITOR_SECONDS:
                input_path.unlink(missing_ok=True)
                await safe_edit_message(
                    status,
                    "The video is too long for AI Video Editor. "
                    f"Maximum duration: {MAX_VIDEO_EDITOR_SECONDS // 60} minutes.",
                )
                return

            job_id = save_video_editor_job(
                context,
                update.effective_user.id,
                input_path,
                original_name,
                media,
            )
            await safe_edit_message(
                status,
                "🎞 Video received for AI Video Editor.\n\n"
                f"Resolution: {media['width']}x{media['height']}\n"
                f"Duration: {format_time(duration)}\n"
                f"Size: {human_bytes(input_path.stat().st_size)}\n\n"
                "Choose a caption template. The editor will preserve original "
                "audio, create word-synced captions, smart reframe speakers, "
                "add a white Video Hook and stable speaker framing.\n\n"
                "No music or B-roll will be added.\n\n"
                f"{owner_footer()}",
                reply_markup=video_editor_template_menu(job_id),
            )
            await cleanup_manager.schedule([input_path], minutes=360)
            return

        if duration > MAX_FAST_ENHANCE_SECONDS:
            input_path.unlink(missing_ok=True)
            await safe_edit_message(
                status,
                "The video is too long for enhancement. "
                f"Maximum duration: {MAX_FAST_ENHANCE_SECONDS // 60} minutes.",
            )
            return

        job_id = save_enhance_job(
            context,
            update.effective_user.id,
            input_path,
            original_name,
            media,
        )
        context.user_data["mode"] = "enhance"
        await safe_edit_message(
            status,
            "Video received.\n\n"
            f"Resolution: {media['width']}x{media['height']}\n"
            f"Duration: {format_time(duration)}\n"
            f"Size: {human_bytes(input_path.stat().st_size)}\n\n"
            "Select an enhancement mode.\n\n"
            f"{owner_footer()}",
            reply_markup=enhance_menu(job_id),
        )
        await cleanup_manager.schedule([input_path], minutes=60)

    except Exception as exc:
        input_path.unlink(missing_ok=True)
        logger.exception("Could not receive video")
        await safe_edit_message(
            status,
            f"Could not process the uploaded video: {str(exc)[:250]}",
        )


# =============================================================================
# Task callbacks
# =============================================================================

async def handle_cancel_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not query or not query.message or not update.effective_user:
        return
    data = query.data or ""
    _, job_id = data.split(":", 1)
    job = jobs(context).get(job_id)
    if job and int(job.get("owner_id", 0)) != update.effective_user.id:
        await query.answer(
            "Only the user who created this task can cancel it.",
            show_alert=True,
        )
        return

    await query.answer()
    removed = jobs(context).pop(job_id, None) or {}
    path = removed.get("input_path")
    if path:
        Path(path).unlink(missing_ok=True)
    await safe_edit_message(query.message, "Task cancelled.")


async def handle_youtube_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if not query or not query.message or not user or not chat:
        return

    parts = (query.data or "").split(":")
    action = parts[0]
    job_id = parts[1]
    job = jobs(context).get(job_id)
    if not job:
        await query.answer()
        await safe_edit_message(
            query.message, "This selection expired. Send the link again."
        )
        return
    if int(job.get("owner_id", 0)) != user.id:
        await query.answer(
            "Only the user who sent the link can use these buttons.",
            show_alert=True,
        )
        return

    key = f"{action}:{job_id}"
    started, reason, cancel_event = active_jobs.try_start(
        user.id,
        key,
        "download",
        is_admin=is_owner(update),
        title=str(job.get("title") or "YouTube Download"),
        username=str(user.username or ""),
    )
    if not started or cancel_event is None:
        await query.answer(reason, show_alert=True)
        return
    await query.answer()

    file_path: Optional[Path] = None

    async def progress(message: str) -> None:
        active_jobs.update(key, stage=message)
        await safe_edit_message(
            query.message, message, reply_markup=stop_keyboard()
        )

    try:
        if action == "a":
            await progress("Your MP3 task is queued...")
            file_path, error = await download_mp3(
                job, progress, cancel_event
            )
            ensure_not_cancelled(cancel_event)
            if error or not file_path:
                await safe_edit_message(
                    query.message, error or "MP3 download failed."
                )
                return
            if file_path.stat().st_size > TELEGRAM_MAX_BYTES:
                await safe_edit_message(
                    query.message,
                    "The final MP3 exceeds Telegram's size limit.",
                )
                return
            await progress("Sending MP3...")
            async with cancellable_slot(UPLOAD_SEMAPHORE, cancel_event):
                ensure_not_cancelled(cancel_event)
                try:
                    await send_local_audio(
                        context,
                        chat.id,
                        file_path,
                        str(job.get("title") or "Audio"),
                        optional_int(job.get("duration")),
                    )
                except TelegramError:
                    await send_local_document(
                        context,
                        chat.id,
                        file_path,
                        f"MP3\n{owner_footer()}",
                    )
            await safe_edit_message(query.message, "MP3 sent successfully.")
            return

        label = parts[2]
        choice = (job.get("choices") or {}).get(label)
        if not choice:
            await safe_edit_message(
                query.message, "This quality is no longer available."
            )
            return
        estimate = choice.get("estimated_size")
        if estimate is not None and int(estimate) > TELEGRAM_MAX_BYTES:
            await safe_edit_message(
                query.message,
                "The estimated file is over Telegram's 2 GB limit. "
                "Choose a lower quality.",
            )
            return

        await progress(f"Your {label} download is queued...")
        file_path, error = await download_video(
            job, choice, progress, cancel_event
        )
        ensure_not_cancelled(cancel_event)
        if error or not file_path:
            await safe_edit_message(
                query.message, error or "Video download failed."
            )
            return
        if file_path.stat().st_size > TELEGRAM_MAX_BYTES:
            await safe_edit_message(
                query.message,
                "The final file is over Telegram's 2 GB limit.",
            )
            return

        await progress(f"Sending {label}...")
        ensure_not_cancelled(cancel_event)
        delivery = await send_video_with_fallback(
            context,
            chat.id,
            file_path,
            f"{label}\n{owner_footer()}",
            optional_int(job.get("duration")),
            optional_int(choice.get("width")),
            optional_int(choice.get("height")),
        )
        await safe_edit_message(
            query.message, f"Done. Sent as a {delivery}."
        )
    except JobCancelled:
        await safe_edit_message(query.message, "Task stopped successfully.")
    except (NetworkError, BadRequest, TelegramError) as exc:
        logger.exception("Telegram upload failed")
        message = (
            "Telegram rejected the file because it is too large."
            if is_too_large_error(exc)
            else f"Upload error: {str(exc)[:230]}"
        )
        await safe_edit_message(query.message, message)
    except Exception as exc:
        logger.exception("YouTube task failed")
        await safe_edit_message(
            query.message, f"Processing error: {str(exc)[:230]}"
        )
    finally:
        active_jobs.finish(user.id, key)
        await cleanup_manager.schedule([file_path])


def generate_editor_hook(text_value: str) -> str:
    """Generate a compact, content-specific hook designed for at most two lines."""
    clean = _clipper_clean_text(text_value, 1600)
    if not clean:
        return "Watch this part"

    if (
        _bot_ai_text_ready()
    ):
        prompt = f"""
Create one attractive short-form video hook for this transcript.
Return ONLY JSON: {{"hook": "..."}}
Rules:
- 5 to 10 words
- maximum 68 characters
- no fake claims or invented facts
- make it specific to the transcript
- natural English, easy to read in two short lines

TRANSCRIPT:
{clean[:2200]}
""".strip()
        try:
            payload = _call_clipper_llm_sync(prompt)
            if isinstance(payload, dict):
                value = _clipper_clean_text(str(payload.get("hook") or ""), 68)
                if value:
                    return value
        except Exception as exc:
            logger.debug("Editor hook LLM fallback: %s", exc)

    return _clipper_clean_text(generate_local_hook(clean), 68)


async def handle_video_editor_split_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()
    parts = (query.data or "").split(":")
    if len(parts) != 2:
        return
    job_id = parts[1]
    job = jobs(context).get(job_id)
    if not job or int(job.get("owner_id", 0)) != update.effective_user.id:
        await safe_edit_message(query.message, "This Video Editor job expired. Upload the video again.")
        return
    enabled = not bool(job.get("split_enabled", True))
    job["split_enabled"] = enabled
    status = "ON" if enabled else "OFF"
    await safe_edit_message(
        query.message,
        "🎞 AI Video Editor\n\n"
        f"👥 Two-speaker Auto Split: {status}\n"
        "Split is used for verified two-speaker interaction, including A/B camera cuts; each occurrence stays at least 4 seconds.\n\n"
        "Choose caption/edit style:",
        reply_markup=video_editor_template_menu(job_id),
    )


async def handle_video_editor_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if not query or not query.message or not user or not chat:
        return
    if ai_service_gate is not None and not await ai_service_gate(update, context):
        return

    try:
        await query.answer("AI Video Editor started")
    except TelegramError:
        pass

    if not is_video_editor_allowed(user.id):
        await safe_edit_message(
            query.message,
            paid_access_text("editor", update.effective_user.id),
        )
        return

    parts = (query.data or "").split(":")
    if len(parts) != 3:
        await safe_edit_message(
            query.message,
            "Invalid Video Editor selection. Upload the video again.",
        )
        return

    _, job_id, caption_template = parts
    if caption_template not in CAPTION_TEMPLATES:
        await safe_edit_message(
            query.message,
            "Unsupported caption template.",
        )
        return

    job = jobs(context).get(job_id)
    if job and job.get("admin_channel_profile"):
        # Admin Channel editing is deliberately fixed and cannot drift into
        # the public templates.
        caption_template = "channel_reference"
    if not job:
        await safe_edit_message(
            query.message,
            "This Video Editor job expired. Upload the video again.",
        )
        return

    if int(job.get("owner_id", 0)) != user.id:
        await safe_edit_message(
            query.message,
            "Only the user who uploaded the video can edit this file.",
        )
        return

    source_path = Path(str(job.get("input_path") or ""))
    split_enabled = bool(job.get("split_enabled", True))
    if not source_path.exists():
        await safe_edit_message(
            query.message,
            "The uploaded video expired. Upload it again.",
        )
        return

    key = f"videoeditor:{job_id}:{caption_template}"
    started, reason, cancel_event = active_jobs.try_start(
        user.id,
        key,
        "video_editor",
        is_admin=is_owner(update),
        title=str(job.get("original_name") or "Uploaded video"),
        username=str(user.username or ""),
    )
    if not started or cancel_event is None:
        await safe_edit_message(query.message, reason)
        return

    if not is_owner(update) and not user_has_key_access(user.id, "editor"):
        trial_ok, trial_left = consume_feature_trial(user.id, "editor")
        if not trial_ok:
            active_jobs.finish(user.id, key)
            await safe_edit_message(query.message, paid_access_text("editor", update.effective_user.id))
            return
        await safe_edit_message(query.message, f"🎁 Free AI Editor use started. Remaining after this job: {trial_left}")

    output_path: Optional[Path] = None
    temp_files: List[Path] = []
    started_at = time.monotonic()
    failure_stage = "initialization"

    async def progress(message: str) -> None:
        active_jobs.update(key, stage=message)
        await safe_edit_message(
            query.message,
            message,
            reply_markup=stop_keyboard(),
        )

    try:
        async with cancellable_slot(
            VIDEO_EDITOR_SEMAPHORE,
            cancel_event,
        ):
            failure_stage = "transcription"
            await progress(
                "🎙 Creating an accurate transcript for captions..."
            )
            (
                paragraphs,
                detected_language,
                editor_word_timings,
            ) = await asyncio.to_thread(
                run_editor_transcription,
                source_path,
                cancel_event,
            )
            if not paragraphs:
                raise RuntimeError(
                    "The editor could not create a transcript for this video"
                )

            media = await asyncio.to_thread(probe_media, source_path)
            duration = float(media.get("duration") or 0)
            if duration <= 0:
                raise RuntimeError("Video duration could not be detected")

            full_text = normalize_transcript_text(
                " ".join(paragraph.text for paragraph in paragraphs)
            )
            hook = generate_editor_hook(full_text)
            caption, hashtags = generate_local_caption_and_hashtags(full_text)

            failure_stage = "viral-score"
            editor_score, editor_reason = await asyncio.to_thread(
                pro_viral_potential_score,
                full_text,
                source_path,
                0.0,
                duration,
                cancel_event,
                None,
                job.get("admin_channel_profile"),
            )
            segment = ViralSegment(
                start=0.0,
                end=duration,
                score=editor_score,
                reason=editor_reason,
                text=full_text,
                hook=hook,
                caption=caption,
                hashtags=hashtags,
            )

            await progress(
                "🎞 Analyzing speakers and choosing the best layout...\n"
                "Face tracking, captions and keyframe motion are being prepared."
            )

            failure_stage = "render"
            output_path, temp_files, render_note = (
                await run_auto_clipper_worker_with_heartbeat(
                    render_auto_clip,
                    (
                        source_path,
                        paragraphs,
                        0.0,
                        duration,
                        segment,
                        1,
                        "Full Video",
                        caption_template,
                        cancel_event,
                        VIDEO_EDITOR_MAX_WALLCLOCK_SECONDS,
                        editor_word_timings,
                        False,
                        None,
                        split_enabled,
                    ),
                    progress,
                    "🎬 AI Video Editor is rendering the final video...",
                    cancel_event,
                    VIDEO_EDITOR_MAX_WALLCLOCK_SECONDS,
                )
            )

            ensure_not_cancelled(cancel_event)
            output_media = await asyncio.to_thread(
                probe_media,
                output_path,
            )

            failure_stage = "quality-check"
            await progress("🔍 Running 3-stage quality check before delivery...")
            qa_ok, qa_notes = await asyncio.to_thread(
                triple_quality_check, output_path, duration
            )
            if not qa_ok and any(
                ("PASS 3 failed: speaker face repeatedly cut by frame edge" in item)
                or ("PASS 3 failed: speaker repeatedly off-centre after tracking" in item)
                for item in qa_notes
            ):
                await progress(
                    "🛡 Framing QA found a cut/off-centre speaker. Re-rendering with face-centred safe framing while keeping body-follow movement..."
                )
                try:
                    output_path.unlink(missing_ok=True)
                except Exception:
                    pass
                output_path, retry_temp, render_note = await run_auto_clipper_worker_with_heartbeat(
                    render_auto_clip,
                    (
                        source_path, paragraphs, 0.0, duration, segment, 1,
                        "Full Video", caption_template, cancel_event,
                        VIDEO_EDITOR_MAX_WALLCLOCK_SECONDS, editor_word_timings,
                        True, None, split_enabled,
                    ),
                    progress,
                    "🎬 Safe face-framing retry is rendering...",
                    cancel_event,
                    VIDEO_EDITOR_MAX_WALLCLOCK_SECONDS,
                )
                temp_files.extend(retry_temp)
                output_media = await asyncio.to_thread(probe_media, output_path)
                qa_ok, qa_notes = await asyncio.to_thread(
                    triple_quality_check, output_path, duration, True
                )
            if not qa_ok:
                raise RuntimeError("Quality check failed: " + "; ".join(qa_notes))

            if viralforge_qa_render:
                ai_qa = await asyncio.to_thread(
                    viralforge_qa_render,
                    output_path,
                    {
                        "feature": "AI Video Editor",
                        "duration": duration,
                        "caption_template": caption_template,
                        "hook": segment.hook,
                        "render_note": render_note,
                    },
                )
                severe = list(ai_qa.get("actionable_high_severity") or []) if isinstance(ai_qa, dict) else []
                if severe:
                    raise RuntimeError("AI editing QA found a high-severity issue: " + _vf_text(severe, 260))

            failure_stage = "delivery"
            await progress("📤 Final edited video passed technical + AI editing checks. Sending now...")

            editor_telegram_caption = (
                f"🎬 {hook}\n"
                f"🔥 Viral Potential: {segment.score}/100\n\n"
                f"{caption}\n\n"
                f"{hashtags}\n\n"
                f"🎨 {CAPTION_TEMPLATES[caption_template]['label']}"
            )[:1000]
            upscale_token = register_upscale_job(
                user_id=user.id,
                chat_id=chat.id,
                path=output_path,
                caption=editor_telegram_caption,
                title=hook or "Edited Clip",
                hashtags=hashtags,
                viral_score=segment.score,
                source_type="video_editor",
                extra={
                    "source_path": str(source_path.resolve()),
                    "clip_start": 0.0,
                    "clip_end": duration,
                    "paragraphs": [asdict(item) for item in paragraphs],
                    "word_timings": [asdict(item) for item in editor_word_timings],
                    "segment": asdict(segment),
                    "caption_template": caption_template,
                    "transcript_text": full_text,
                },
            )
            _, sent_message = await send_video_with_upscale_button(
                context,
                chat.id,
                output_path,
                editor_telegram_caption,
                upscale_token,
                optional_int(output_media.get("duration")),
                optional_int(output_media.get("width")),
                optional_int(output_media.get("height")),
            )
            with UPSCALE_JOB_LOCK:
                if upscale_token in UPSCALE_JOBS:
                    UPSCALE_JOBS[upscale_token]["message_id"] = int(
                        getattr(sent_message, "message_id", 0) or 0
                    )

            elapsed = int(time.monotonic() - started_at)
            await safe_edit_message(
                query.message,
                "✅ AI Video Editor complete.\n\n"
                f"Processing time: {format_time(elapsed)}\n"
                f"Caption template: {CAPTION_TEMPLATES[caption_template]['label']}\n"
                f"Two-speaker Auto Split: {'ON' if split_enabled else 'OFF'}\n"
                f"{owner_footer()}",
            )
            jobs(context).pop(job_id, None)

    except JobCancelled:
        await safe_edit_message(
            query.message,
            "⛔ AI Video Editor task stopped successfully.",
        )
    except Exception as exc:
        logger.exception("AI Video Editor failed")
        await _notify_feature_failure(context, user, "AI Video Editor", exc, failure_stage, traceback.format_exc())
        await safe_edit_message(
            query.message,
            "AI Video Editor could not complete the job.\n"
            f"Error: {str(exc)[:320]}",
        )
    finally:
        active_jobs.finish(user.id, key)
        registered_paths = {
            str(value.get("path") or "")
            for value in UPSCALE_JOBS.values()
        }
        protected_sources = {
            str((value.get("extra") or {}).get("source_path") or "")
            for value in UPSCALE_JOBS.values()
        }
        await cleanup_manager.schedule(
            [
                *(
                    []
                    if str(source_path) in protected_sources
                    else [source_path]
                ),
                *[
                    path for path in [output_path, *temp_files]
                    if (
                        path is not None
                        and str(path) not in registered_paths
                        and str(path) not in protected_sources
                    )
                ],
            ]
        )


async def handle_auto_clipper_duration_callback(

    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not query.message or not user:
        return
    if ai_service_gate is not None and not await ai_service_gate(update, context):
        return

    try:
        await query.answer()
    except TelegramError:
        pass

    if not is_auto_clipper_allowed(user.id):
        await safe_edit_message(
            query.message,
            "Auto Clipper Pro access is currently disabled.",
        )
        return

    parts = (query.data or "").split(":")
    if len(parts) != 3:
        await safe_edit_message(
            query.message,
            "Invalid duration selection.",
        )
        return

    action, job_id, duration_choice = parts
    job = jobs(context).get(job_id)
    if not job or int(job.get("owner_id", 0)) != user.id:
        await safe_edit_message(
            query.message,
            "This Auto Clipper job expired. Send the YouTube link again.",
        )
        return

    if duration_choice not in {"30", "60", "90", "120", "all"}:
        await safe_edit_message(
            query.message,
            "Unsupported clip duration.",
        )
        return

    await safe_edit_message(
        query.message,
        "🎨 Choose Caption Template\n\n"
        f"Selected duration: {_clipper_duration_label(duration_choice)}\n"
        "Captions will use exact word-level Whisper timestamps for tight sync.\n\n"
        "No music or B-roll will be added.",
        reply_markup=auto_clipper_caption_template_menu(
            job_id,
            duration_choice,
        ),
    )


async def handle_auto_clipper_back_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not query.message or not user:
        return

    try:
        await query.answer()
    except TelegramError:
        pass

    parts = (query.data or "").split(":")
    if len(parts) != 2:
        return
    _, job_id = parts

    job = jobs(context).get(job_id)
    if not job or int(job.get("owner_id", 0)) != user.id:
        await safe_edit_message(
            query.message,
            "This Auto Clipper job expired. Send the YouTube link again.",
        )
        return

    await safe_edit_message(
        query.message,
        "✂️ Auto Clipper Pro\n\n"
        "Choose the exact clip duration.",
        reply_markup=auto_clipper_duration_menu(job_id),
    )


async def handle_auto_clipper_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if not query or not query.message or not user or not chat:
        return
    if ai_service_gate is not None and not await ai_service_gate(update, context):
        return

    try:
        await query.answer("Auto Clipper started")
    except TelegramError:
        pass

    if not is_auto_clipper_allowed(user.id):
        await safe_edit_message(
            query.message,
            paid_access_text("clipper", update.effective_user.id),
        )
        return

    parts = (query.data or "").split(":")
    if len(parts) != 4:
        await safe_edit_message(
            query.message,
            "Invalid Auto Clipper selection. Send the YouTube link again.",
        )
        return

    _, job_id, duration_choice, caption_template = parts
    job = jobs(context).get(job_id)
    if job and job.get("admin_channel_profile"):
        caption_template = "channel_reference"
    if not job:
        await safe_edit_message(
            query.message,
            "This Auto Clipper job expired. Send the YouTube link again.",
        )
        return
    if int(job.get("owner_id", 0)) != user.id:
        await safe_edit_message(
            query.message,
            "Only the user who submitted the link can start this job.",
        )
        return
    if duration_choice not in {"30", "60", "90", "120", "all"}:
        await safe_edit_message(
            query.message,
            "Unsupported clip duration.",
        )
        return
    if caption_template not in CAPTION_TEMPLATES:
        await safe_edit_message(
            query.message,
            "Unsupported caption template.",
        )
        return

    key = f"clipper:{job_id}:{duration_choice}:{caption_template}"
    started, reason, cancel_event = active_jobs.try_start(
        user.id,
        key,
        "clipper",
        is_admin=is_owner(update),
        title=str(job.get("title") or "Auto Clipper"),
        username=str(user.username or ""),
    )
    if not started or cancel_event is None:
        await safe_edit_message(query.message, reason)
        return

    if not is_owner(update) and not user_has_key_access(user.id, "clipper"):
        trial_ok, trial_left = consume_feature_trial(user.id, "clipper")
        if not trial_ok:
            active_jobs.finish(user.id, key)
            await safe_edit_message(query.message, paid_access_text("clipper", update.effective_user.id))
            return
        await safe_edit_message(query.message, f"🎁 Free Auto Clipper use started. Remaining after this job: {trial_left}")

    source_path: Optional[Path] = None
    transcript_cleanup: List[Path] = []
    generated_files: List[Path] = []
    started_at = time.monotonic()
    sent_count = 0

    async def progress(message: str) -> None:
        active_jobs.update(key, stage=message)
        await safe_edit_message(
            query.message,
            message,
            reply_markup=stop_keyboard(),
        )

    def check_total_timeout() -> None:
        ensure_not_cancelled(cancel_event)
        elapsed = time.monotonic() - started_at
        if elapsed >= AUTO_CLIPPER_MAX_WALLCLOCK_SECONDS:
            cancel_event.set()
            raise RuntimeError(
                "Auto Clipper reached its maximum total processing time"
            )

    try:
        async with cancellable_slot(
            AUTO_CLIPPER_SEMAPHORE,
            cancel_event,
        ):
            check_total_timeout()
            if str(job.get("source_type") or "youtube") == "local":
                await progress("📥 Preparing uploaded source video...")
                source_path = Path(str(job.get("input_path") or ""))
                if not source_path.exists() or not source_path.is_file():
                    raise RuntimeError("Uploaded source file expired or is missing")
            else:
                await progress("📥 Downloading the source video...")
                choice = choose_clipper_quality(job)
                if not choice:
                    raise RuntimeError(
                        "No suitable YouTube source quality was available"
                    )

                source_path, error = await download_video(
                    job,
                    choice,
                    progress,
                    cancel_event,
                )
                if error or not source_path:
                    raise RuntimeError(error or "Source video download failed")

            check_total_timeout()
            await progress("📝 Preparing accurate transcript timestamps...")
            paragraphs, detected_language, transcript_cleanup = (
                await get_clipper_transcript(
                    job,
                    source_path,
                    progress,
                    cancel_event,
                )
            )
            if not paragraphs:
                raise RuntimeError("No transcript was available for viral analysis")

            check_total_timeout()
            llm_mode = bool(
                _bot_ai_text_ready()
            )
            await progress(
                "🧠 Analyzing transcript for viral moments..."
                if llm_mode
                else "🧠 Scoring viral moments with local transcript + audio analysis..."
            )

            limit = (
                AUTO_CLIPPER_ALL_MAX_SEGMENTS
                if duration_choice == "all"
                else AUTO_CLIPPER_MAX_SEGMENTS
            )
            analysis_mode = str(job.get("analysis_mode") or "quick").lower()
            deep_result: Dict[str, Any] = {}
            if analysis_mode == "deep":
                if not deep_research_enabled():
                    raise RuntimeError(
                        "Deep viral selection needs an Agnes API key. "
                        "Use /addagneskey or choose Quick Clip for local fallback."
                    )
                await progress(
                    "🔬 Deep Viral Selection started...\n"
                    "Agnes selects viral moments only; editing stays 100% local."
                )
                profile = job.get("admin_channel_profile") or {}
                try:
                    proxy_candidates = (
                        runtime_auth.get_proxy_candidates(3) if runtime_auth is not None else []
                    )
                except Exception:
                    proxy_candidates = []
                research_evidence = await asyncio.to_thread(
                    collect_viral_research,
                    str(job.get("url") or ""),
                    str(job.get("video_id") or job_id),
                    str(job.get("title") or "YouTube Video"),
                    proxy_candidates,
                )
                await progress("🧠 Agnes is ranking transcript moments; local scoring verifies them...")
                segments = await asyncio.to_thread(
                    detect_viral_segments, paragraphs, source_path, cancel_event,
                    VIRALFORGE_DEEP_FINAL_CLIPS, profile,
                )
                segments = boost_segments_with_research(segments, research_evidence)
                deep_result = {
                    "mode": "agnes_selection_local_editor",
                    "agent_trace": [
                        {"agent": "Agnes Viral Selection", "status": "ok"},
                        {"agent": "Local Podcast Editor", "status": "ok"},
                    ],
                }
                if not segments:
                    raise RuntimeError("Agnes/local scoring returned no valid viral candidates")
                limit = VIRALFORGE_DEEP_FINAL_CLIPS
            else:
                segments = await asyncio.to_thread(
                    detect_viral_segments,
                    paragraphs,
                    source_path,
                    cancel_event,
                    limit,
                    job.get("admin_channel_profile"),
                )
            if not segments:
                raise RuntimeError("No suitable viral moments were found")

            best_score = max(segment.score for segment in segments)
            elite_count = sum(1 for segment in segments if segment.elite_pick)
            await progress(
                f"🔥 Found {len(segments)} final viral moments.\n"
                f"🎯 Best Viral Potential Score: {best_score}/100\n"
                + (f"🏆 Elite Picks: {elite_count}\n" if analysis_mode == "deep" else "")
                + "Starting progressive clip delivery..."
            )

            media = await asyncio.to_thread(probe_media, source_path)
            video_duration = float(media.get("duration") or 0)
            if video_duration <= 0:
                video_duration = max(
                    paragraph.end for paragraph in paragraphs
                )

            duration_values = [duration_choice]

            # Build the complete deduplicated plan first. This prevents the bot
            # from sending the same/near-identical clip repeatedly.
            if str(job.get("analysis_mode") or "quick").lower() == "deep":
                clip_plan = []
                existing_ranges: List[Tuple[float, float]] = []
                for segment in sorted(segments, key=lambda item: (item.rank or 999, -item.score)):
                    clip_start = max(0.0, min(video_duration, segment.start))
                    clip_end = max(clip_start + 0.5, min(video_duration, segment.end))
                    # If deep timing returned an unrealistically tiny range, preserve the
                    # existing natural-boundary expansion as a safe fallback.
                    clip_start, clip_end, clip_text = _deep_expand_from_hook(
                        segment, paragraphs, duration_choice, video_duration
                    )
                    if _planned_clip_is_duplicate(clip_start, clip_end, existing_ranges):
                        continue
                    existing_ranges.append((clip_start, clip_end))
                    clip_plan.append((segment, duration_choice, clip_start, clip_end, clip_text))
                    if len(clip_plan) >= VIRALFORGE_DEEP_FINAL_CLIPS:
                        break
            else:
                clip_plan = plan_unique_auto_clips(
                    segments,
                    paragraphs,
                    duration_values,
                    video_duration,
                )
            if not clip_plan:
                raise RuntimeError(
                    "No unique clip ranges remained after duplicate filtering"
                )
            clip_plan = sorted(clip_plan, key=lambda item: item[0].score, reverse=True)[:10]

            total_outputs = len(clip_plan)

            for output_number, (
                segment,
                duration_value,
                clip_start,
                clip_end,
                clip_text,
            ) in enumerate(clip_plan, 1):
                check_total_timeout()
                ensure_not_cancelled(cancel_event)

                render_segment = ViralSegment(
                    start=segment.start,
                    end=segment.end,
                    score=segment.score,
                    reason=segment.reason,
                    text=clip_text,
                    hook=segment.hook or generate_local_hook(clip_text),
                    caption=segment.caption,
                    hashtags=segment.hashtags,
                    rank=segment.rank,
                    elite_pick=segment.elite_pick,
                    hook_duration=segment.hook_duration,
                    deep_meta=dict(segment.deep_meta or {}),
                )
                if not render_segment.caption or not render_segment.hashtags:
                    cap, tags = generate_local_caption_and_hashtags(
                        clip_text
                    )
                    render_segment.caption = (
                        render_segment.caption or cap
                    )
                    render_segment.hashtags = (
                        render_segment.hashtags or tags
                    )

                duration_label = _clipper_duration_label(
                    duration_value
                )
                exact_seconds = max(0.0, clip_end - clip_start)

                await progress(
                    f"🎬 Generating clip {output_number} of {total_outputs}...\n"
                    + ("🏆 ELITE PICK\n" if render_segment.elite_pick else "")
                    + f"🔥 Viral Potential: {segment.score}/100\n"
                    f"⏱ Exact duration: {exact_seconds:.1f}s\n"
                    f"🎨 Captions: {CAPTION_TEMPLATES[caption_template]['label']}"
                )

                await progress(
                    f"🎙 Preparing exact word-sync captions "
                    f"for clip {output_number}/{total_outputs}..."
                )
                clip_word_timings, word_temp_files, word_timing_note = (
                    await asyncio.to_thread(
                        transcribe_clip_word_timings,
                        source_path,
                        clip_start,
                        clip_end,
                        paragraphs,
                        cancel_event,
                    )
                )
                generated_files.extend(word_temp_files)

                await progress(
                    f"👁 Clip {output_number}/{total_outputs}: live speaker/face/body "
                    "tracking analysis is preparing exact frame movement..."
                )
                local_edit_plan: Dict[str, Any] = {}
                if False and viralforge_plan_clip_edit:
                    await progress(
                        f"🤖 Local Podcast Editor: visually planning split/zoom emphasis "
                        f"for clip {output_number}/{total_outputs}..."
                    )
                    local_edit_plan = await asyncio.to_thread(
                        viralforge_plan_clip_edit,
                        source_path,
                        clip_start,
                        clip_end,
                        {
                            "feature": "Auto Clipper",
                            "analysis_mode": str(job.get("analysis_mode") or "quick"),
                            "rank": int(render_segment.rank or output_number),
                            "hook": render_segment.hook,
                            "caption_template": caption_template,
                        },
                    )
                    await progress(
                        f"✅ Local edit plan ready for clip {output_number}/{total_outputs}. "
                        "Local tracker is executing smooth frame motion now."
                    )

                clip_path, temp_files, render_note = (
                    await run_auto_clipper_worker_with_heartbeat(
                        render_auto_clip,
                        (
                            source_path,
                            paragraphs,
                            clip_start,
                            clip_end,
                            render_segment,
                            output_number,
                            duration_label,
                            caption_template,
                            cancel_event,
                            AUTO_CLIPPER_RENDER_TIMEOUT_SECONDS,
                            clip_word_timings,
                            False,
                            local_edit_plan,
                        ),
                        progress,
                        (
                            f"🎬 Live adaptive reframe + word-sync captions "
                            f"for clip {output_number} of {total_outputs}..."
                        ),
                        cancel_event,
                        AUTO_CLIPPER_RENDER_TIMEOUT_SECONDS,
                    )
                )
                generated_files.extend(temp_files)
                generated_files.append(clip_path)

                ensure_not_cancelled(cancel_event)
                clip_media = await asyncio.to_thread(
                    probe_media,
                    clip_path,
                )
                qa_ok, qa_notes = await asyncio.to_thread(
                    triple_quality_check,
                    clip_path,
                    max(0.1, clip_end - clip_start),
                )
                if not qa_ok:
                    raise RuntimeError(
                        "Clip quality check failed: " + "; ".join(qa_notes)
                    )

                await progress(
                    f"🤖 Local Podcast Editor: checking clip {output_number}/{total_outputs} "
                    "for speaker framing, split timing, captions and visual errors..."
                )
                # Preserve existing QA and add ViralForge as a second, optional gate.
                vf_qa: Dict[str, Any] = {}
                if False and viralforge_qa_render:
                    vf_qa = await asyncio.to_thread(
                        viralforge_qa_render,
                        clip_path,
                        {
                            "feature": "Auto Clipper",
                            "analysis_mode": str(job.get("analysis_mode") or "quick"),
                            "rank": int(render_segment.rank or output_number),
                            "elite_pick": bool(render_segment.elite_pick),
                            "clip_start": clip_start,
                            "clip_end": clip_end,
                            "hook_duration": render_segment.hook_duration,
                            "hook": render_segment.hook,
                            "caption_template": caption_template,
                            "render_note": render_note,
                            "visual_evidence": (render_segment.deep_meta or {}).get("visual") or {},
                            "timing_doctor": (render_segment.deep_meta or {}).get("timing_doctor") or {},
                            "hook_lab": (render_segment.deep_meta or {}).get("hook_lab") or {},
                        },
                    )
                    if not bool(vf_qa.get("pass", True)):
                        issues = (vf_qa.get("editorial") or {}).get("issues") or []
                        await progress(
                            f"🔁 Local QA rejected clip {output_number}/{total_outputs}. "
                            "Automatic Re-edit is correcting the framing before delivery..."
                        )
                        try:
                            clip_path.unlink(missing_ok=True)
                        except Exception:
                            pass
                        clip_path, retry_temp, render_note = await run_auto_clipper_worker_with_heartbeat(
                            render_auto_clip,
                            (
                                source_path, paragraphs, clip_start, clip_end,
                                render_segment, output_number, duration_label,
                                caption_template, cancel_event,
                                AUTO_CLIPPER_RENDER_TIMEOUT_SECONDS,
                                clip_word_timings, True,
                                local_edit_plan,
                            ),
                            progress,
                            f"🎬 Re-editing clip {output_number}/{total_outputs} with adaptive scene-driven framing...",
                            cancel_event,
                            AUTO_CLIPPER_RENDER_TIMEOUT_SECONDS,
                        )
                        generated_files.extend(retry_temp)
                        generated_files.append(clip_path)
                        qa_ok, qa_notes = await asyncio.to_thread(
                            triple_quality_check,
                            clip_path,
                            max(0.1, clip_end - clip_start),
                        )
                        if not qa_ok:
                            raise RuntimeError(
                                "Automatic Re-edit technical QA failed: " + "; ".join(qa_notes)
                            )
                        await progress(
                            f"🤖 Local QA: checking corrected clip {output_number}/{total_outputs}..."
                        )
                        vf_qa = await asyncio.to_thread(
                            viralforge_qa_render,
                            clip_path,
                            {
                                "feature": "Auto Clipper Re-edit",
                                "analysis_mode": str(job.get("analysis_mode") or "quick"),
                                "rank": int(render_segment.rank or output_number),
                                "clip_start": clip_start,
                                "clip_end": clip_end,
                                "caption_template": caption_template,
                                "render_note": render_note,
                                "automatic_reedit": True,
                            },
                        )
                        if not bool(vf_qa.get("pass", True)):
                            retry_issues = (vf_qa.get("editorial") or {}).get("issues") or []
                            raise RuntimeError(
                                "Local QA blocked corrected clip delivery: "
                                + _vf_text(retry_issues, 280)
                            )

                telegram_caption = (
                    build_auto_clipper_telegram_caption(
                        render_segment,
                        clip_start,
                        clip_end,
                        duration_label,
                    )
                )

                await progress(
                    f"📤 Clip {output_number} of {total_outputs} ready. "
                    "Sending now..."
                )
                await progress(
                    f"🧩 Preserving clean source for Re-edit "
                    f"({output_number}/{total_outputs})..."
                )
                reedit_source = await asyncio.to_thread(
                    create_clean_reedit_source,
                    source_path,
                    clip_start,
                    clip_end,
                    cancel_event,
                )
                relative_paragraphs = relative_clip_paragraphs(
                    paragraphs,
                    clip_start,
                    clip_end,
                )
                reedit_words = _clean_clipper_word_timings(
                    clip_word_timings
                )

                upscale_token = register_upscale_job(
                    user_id=user.id,
                    chat_id=chat.id,
                    path=clip_path,
                    caption=telegram_caption,
                    title=render_segment.hook or "Viral Clip",
                    hashtags=render_segment.hashtags,
                    viral_score=render_segment.score,
                    source_type="auto_clipper",
                    extra={
                        "source_path": str(reedit_source.resolve()),
                        "clip_start": 0.0,
                        "clip_end": max(0.1, clip_end - clip_start),
                        "paragraphs": [
                            asdict(item) for item in relative_paragraphs
                        ],
                        "word_timings": [
                            asdict(item) for item in reedit_words
                        ],
                        "segment": asdict(render_segment),
                        "caption_template": caption_template,
                        "transcript_text": clip_text,
                        "viralforge_clip_id": f"VF-{job_id}-{output_number:02d}" if str(job.get("analysis_mode") or "") == "deep" else "",
                        "rank": int(render_segment.rank or output_number),
                        "elite_pick": bool(render_segment.elite_pick),
                        "research_summary": (render_segment.deep_meta or {}).get("research_summary") or {},
                        "audience_evidence": (render_segment.deep_meta or {}).get("audience_evidence") or (render_segment.deep_meta or {}).get("audience_profile") or {},
                        "visual_evidence": (render_segment.deep_meta or {}).get("visual_evidence") or (render_segment.deep_meta or {}).get("visual") or {},
                        "hook_type": (render_segment.deep_meta or {}).get("hook_type") or ((render_segment.deep_meta or {}).get("hook_lab") or {}).get("recommended_hook_type") or "",
                        "first5_hook": (render_segment.deep_meta or {}).get("video_hook_first5") or "",
                        "platform_risk": (render_segment.deep_meta or {}).get("platform_risk"),
                        "manual_review": bool((render_segment.deep_meta or {}).get("manual_review")),
                        "viralforge_qa": vf_qa,
                        "repair_attempts": 0,
                    },
                )
                if viralforge_store_prediction and str(job.get("analysis_mode") or "") == "deep":
                    try:
                        await asyncio.to_thread(
                            viralforge_store_prediction,
                            f"VF-{job_id}-{output_number:02d}",
                            str((job.get("admin_channel_profile") or {}).get("id") or ""),
                            str(job.get("title") or ""),
                            dict(render_segment.deep_meta or {}),
                            float(render_segment.score),
                        )
                    except Exception:
                        logger.exception("Could not store ViralForge clip prediction")

                _, sent_message = await send_video_with_upscale_button(
                    context,
                    chat.id,
                    clip_path,
                    telegram_caption,
                    upscale_token,
                    optional_int(clip_media.get("duration")),
                    optional_int(clip_media.get("width")),
                    optional_int(clip_media.get("height")),
                )
                with UPSCALE_JOB_LOCK:
                    if upscale_token in UPSCALE_JOBS:
                        UPSCALE_JOBS[upscale_token]["message_id"] = int(
                            getattr(sent_message, "message_id", 0) or 0
                        )
                sent_count += 1

                # Keep the delivered clip for the optional Upscale button.
                # Temporary render assets can be removed immediately.
                for temp_path in temp_files:
                    try:
                        temp_path.unlink(missing_ok=True)
                    except OSError:
                        pass

            elapsed = int(time.monotonic() - started_at)
            await safe_edit_message(
                query.message,
                "✅ Auto Clipper Pro complete.\n\n"
                f"Generated and sent: {sent_count} clips\n"
                + (f"🏆 Elite Picks: {sum(1 for x in segments if x.elite_pick)}\n" if str(job.get("analysis_mode") or "") == "deep" else "")
                + f"Viral moments analyzed: {len(segments)}\n"
                f"Best Viral Potential Score: {best_score}/100\n"
                f"Total processing time: {format_time(elapsed)}\n\n"
                f"{owner_footer()}",
            )

    except JobCancelled:
        await safe_edit_message(
            query.message,
            "⛔ Auto Clipper task stopped successfully.",
        )
    except Exception as exc:
        logger.exception("Auto Clipper failed")
        await _notify_feature_failure(context, user, "AI Clipper", exc, "analysis/render/delivery", traceback.format_exc())
        await safe_edit_message(
            query.message,
            "Auto Clipper could not complete the job.\n"
            f"Error: {str(exc)[:300]}",
        )
    finally:
        active_jobs.finish(user.id, key)
        registered_paths = {
            str(value.get("path") or "")
            for value in UPSCALE_JOBS.values()
        }
        cleanup_targets: List[Optional[Path]] = [
            source_path,
            *transcript_cleanup,
            *[
                path for path in generated_files
                if str(path) not in registered_paths
            ],
        ]
        await cleanup_manager.schedule(cleanup_targets)


async def handle_transcript_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if not query or not query.message or not user or not chat:
        return

    _, job_id, language_code, output_format = (query.data or "").split(":")
    job = jobs(context).get(job_id)
    if not job:
        await query.answer()
        await safe_edit_message(
            query.message,
            "This transcript selection expired. Send the link again.",
        )
        return
    if int(job.get("owner_id", 0)) != user.id:
        await query.answer(
            "Only the user who sent the link can use these buttons.",
            show_alert=True,
        )
        return

    duration = optional_int(job.get("duration"))
    if duration and duration > MAX_TRANSCRIPT_SECONDS:
        await query.answer()
        await safe_edit_message(
            query.message, "This video is too long for transcription."
        )
        return

    key = f"transcript:{job_id}:{language_code}:{output_format}"
    started, reason, cancel_event = active_jobs.try_start(
        user.id,
        key,
        "transcript",
        is_admin=is_owner(update),
        title=(
            f"{str(job.get('title') or 'Transcript')} | "
            f"{language_name(language_code)} {output_format.upper()}"
        ),
        username=str(user.username or ""),
    )
    if not started or cancel_event is None:
        await query.answer(reason, show_alert=True)
        return
    await query.answer()

    output: Optional[Path] = None
    cleanup_files: List[Path] = []

    async def progress(message: str) -> None:
        active_jobs.update(key, stage=message)
        await safe_edit_message(
            query.message, message, reply_markup=stop_keyboard()
        )

    try:
        await progress("⏳ Your transcript task is queued...")
        output, error, cleanup_files = await create_transcript(
            job,
            language_code,
            output_format,
            progress,
            cancel_event,
        )
        ensure_not_cancelled(cancel_event)
        if error or not output:
            await safe_edit_message(
                query.message, error or "Transcript creation failed."
            )
            return
        if output.stat().st_size > TELEGRAM_MAX_BYTES:
            await safe_edit_message(
                query.message, "The transcript file exceeds Telegram's size limit."
            )
            return

        name = language_name(language_code)
        await progress(
            f"📤 Sending {name} {output_format.upper()} transcript..."
        )
        # Transcript files are small. Send immediately instead of waiting
        # behind potentially huge video uploads in UPLOAD_SEMAPHORE.
        ensure_not_cancelled(cancel_event)
        await send_local_document(
            context,
            chat.id,
            output,
            f"{name} Transcript\n{owner_footer()}",
        )
        await safe_edit_message(
            query.message, "✅ Transcript sent successfully."
        )
    except JobCancelled:
        await safe_edit_message(query.message, "Task stopped successfully.")
    except Exception as exc:
        logger.exception("Transcript callback failed")
        await safe_edit_message(
            query.message, f"Transcript error: {str(exc)[:240]}"
        )
    finally:
        active_jobs.finish(user.id, key)
        await cleanup_manager.schedule(cleanup_files + [output])


async def run_enhance_with_heartbeat(
    worker: Callable[..., Any],
    worker_args: Tuple[Any, ...],
    progress: Callable[[str], Awaitable[None]],
    label: str,
) -> Any:
    """
    Run enhancement work in a thread with progress updates and an overall cap.

    When the wall-clock limit is reached, the shared threading.Event is set so
    run_process()/CPU AI can terminate their subprocesses and exit cleanly.
    """
    task = asyncio.create_task(asyncio.to_thread(worker, *worker_args))
    started_at = time.monotonic()
    cancel_event = next(
        (
            arg
            for arg in worker_args
            if isinstance(arg, threading.Event)
        ),
        None,
    )

    while True:
        elapsed = time.monotonic() - started_at
        remaining = MAX_AI_ENHANCE_WALLCLOCK_SECONDS - elapsed

        if remaining <= 0:
            if cancel_event is not None:
                cancel_event.set()

            await progress(
                "⏱ Enhancement timed out after "
                f"{format_time(MAX_AI_ENHANCE_WALLCLOCK_SECONDS)}. "
                "The task has been stopped automatically."
            )

            # Give the worker a short grace period to kill FFmpeg/AI processes.
            try:
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=10,
                )
            except BaseException:
                pass

            if not task.done():
                task.cancel()

            raise RuntimeError(
                "AI enhancement exceeded the maximum processing time "
                f"({MAX_AI_ENHANCE_WALLCLOCK_SECONDS}s)"
            )

        wait_for = min(
            ENHANCE_PROGRESS_INTERVAL_SECONDS,
            max(0.1, remaining),
        )

        try:
            return await asyncio.wait_for(
                asyncio.shield(task),
                timeout=wait_for,
            )
        except asyncio.TimeoutError:
            elapsed_seconds = int(time.monotonic() - started_at)
            await progress(
                f"{label}\n"
                f"⏱ Processing: {format_time(elapsed_seconds)}\n"
                f"Maximum: {format_time(MAX_AI_ENHANCE_WALLCLOCK_SECONDS)}"
            )


async def handle_enhance_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if not query or not query.message or not user or not chat:
        return

    try:
        await query.answer("Enhancement started")
    except TelegramError:
        pass

    try:
        parts = (query.data or "").split(":")
        if len(parts) != 3:
            raise ValueError("Invalid enhancement button data")
        _, job_id, mode = parts
    except Exception:
        await safe_edit_message(
            query.message,
            "This enhancement button is invalid. Upload the video again.",
        )
        return

    job = jobs(context).get(job_id)
    if not job:
        await safe_edit_message(
            query.message,
            "This enhancement selection expired. Upload the video again.",
        )
        return

    if int(job.get("owner_id", 0)) != user.id:
        try:
            await query.answer(
                "Only the user who uploaded the video can use these buttons.",
                show_alert=True,
            )
        except TelegramError:
            pass
        return

    input_path = Path(str(job.get("input_path") or ""))
    if not input_path.exists():
        await safe_edit_message(
            query.message,
            "The input video expired. Upload it again.",
        )
        return

    if mode not in {"studio720", "studio1080", "ai2x"}:
        await safe_edit_message(
            query.message,
            "This enhancement mode is not available.",
        )
        return

    key = f"enhance:{job_id}:{mode}"
    started, reason, cancel_event = active_jobs.try_start(
        user.id,
        key,
        "enhance",
        is_admin=is_owner(update),
        username=str(user.username or ""),
        title=str(job.get("original_name") or "Uploaded video"),
    )
    if not started or cancel_event is None:
        try:
            await query.answer(reason, show_alert=True)
        except TelegramError:
            await safe_edit_message(query.message, reason)
        return

    output: Optional[Path] = None

    async def progress(message: str) -> None:
        active_jobs.update(key, stage=message)
        await safe_edit_message(
            query.message,
            message,
            reply_markup=stop_keyboard(),
        )

    try:
        enhance_note = ""
        used_ai = False

        if mode == "ai2x":
            await progress("🤖 AI HD 2x: checking the AI engine...")
            async with cancellable_slot(
                AI_ENHANCE_SEMAPHORE,
                cancel_event,
            ):
                output, used_ai, enhance_note = await run_enhance_with_heartbeat(
                    ai_enhance_video,
                    (input_path, cancel_event),
                    progress,
                    "🤖 AI HD 2x enhancement is running.",
                )
        else:
            target = 720 if mode == "studio720" else 1080
            await progress(f"⚡ Fast HD {target}p: starting...")
            async with cancellable_slot(
                FAST_ENHANCE_SEMAPHORE,
                cancel_event,
            ):
                output = await run_enhance_with_heartbeat(
                    fast_enhance_video,
                    (input_path, target, cancel_event),
                    progress,
                    f"⚡ Fast HD {target}p enhancement is running.",
                )
            enhance_note = f"Fast HD {target}p"

        ensure_not_cancelled(cancel_event)
        if not output or not output.exists():
            raise RuntimeError("The enhanced output file was not created")
        if output.stat().st_size <= 0:
            raise RuntimeError("The enhanced output file is empty")
        if output.stat().st_size > TELEGRAM_MAX_BYTES:
            await safe_edit_message(
                query.message,
                "The enhanced file is over Telegram's 2 GB limit.",
            )
            return

        media = await asyncio.to_thread(probe_media, output)
        await progress(
            f"📤 Sending enhanced video ({human_bytes(output.stat().st_size)})..."
        )
        ensure_not_cancelled(cancel_event)

        delivery = await send_video_with_fallback(
            context,
            chat.id,
            output,
            f"Enhanced Video\n{owner_footer()}",
            optional_int(media.get("duration")),
            optional_int(media.get("width")),
            optional_int(media.get("height")),
        )

        final_message = f"✅ Enhancement complete. Sent as a {delivery}."
        if mode == "ai2x":
            if used_ai:
                final_message += (
                    f"\n🤖 AI upscale: "
                    f"{enhance_note or '2x AI super-resolution'}"
                )
            else:
                final_message += (
                    "\n⚡ AI engine was unavailable or too slow on this VPS, "
                    "so Fast HD 1080p fallback was used automatically."
                )
                if enhance_note:
                    final_message += f"\nReason: {enhance_note[:160]}"

        await safe_edit_message(query.message, final_message)
        jobs(context).pop(job_id, None)

    except JobCancelled:
        if output:
            output.unlink(missing_ok=True)
        await safe_edit_message(query.message, "Task stopped successfully.")
    except Exception as exc:
        logger.exception("Video enhancement failed")
        await safe_edit_message(
            query.message,
            "Enhancement failed, but the bot is still running.\n"
            f"Error: {str(exc)[:240]}",
        )
    finally:
        active_jobs.finish(user.id, key)
        await cleanup_manager.schedule([input_path, output])


# =============================================================================
# Startup and error handling
# =============================================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    logger.error(
        "Unhandled bot error",
        exc_info=(
            type(context.error),
            context.error,
            context.error.__traceback__,
        ) if context.error else None,
    )
    # Runtime guardian: preserve the process, capture context and escalate.
    # It deliberately never rewrites source code automatically.
    if OWNER_ID <= 0 or context.error is None:
        return
    try:
        user = getattr(update, "effective_user", None)
        chat = getattr(update, "effective_chat", None)
        message = (
            "🛡 RUNTIME GUARDIAN ALERT\n\n"
            f"Error: {type(context.error).__name__}: {str(context.error)[:900]}\n"
            f"User: {getattr(user, 'id', '-')} @{getattr(user, 'username', '') or '-'}\n"
            f"Chat: {getattr(chat, 'id', '-')}\n"
            "The bot process remains protected by systemd auto-restart. "
            "Check /jobs and journal logs before changing code."
        )
        await context.bot.send_message(OWNER_ID, message[:4090])
    except Exception:
        logger.exception("Runtime guardian could not notify owner")


def check_local_bot_api() -> None:
    endpoint = f"{LOCAL_BOT_API_URL}/bot{BOT_TOKEN}/getMe"
    try:
        with urllib.request.urlopen(endpoint, timeout=15) as response:
            payload = json.loads(
                response.read().decode("utf-8", errors="replace")
            )
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        raise RuntimeError(
            "Telegram Local Bot API is not reachable. "
            "Start telegram-bot-api before this bot. "
            f"Original error: {exc}"
        ) from exc
    if not payload.get("ok"):
        raise RuntimeError(f"Local Bot API rejected getMe: {payload}")


async def post_shutdown(application: Application) -> None:
    task=application.bot_data.get("ai_backend_monitor_task")
    if task and not task.done():
        task.cancel()
    await cleanup_manager.shutdown()


async def post_init(application: Application) -> None:
    if start_ai_backend_monitor is not None:
        try:
            start_ai_backend_monitor(application)
        except Exception:
            logger.exception("Could not start AI backend health monitor")
    public_commands = [
        BotCommand("start", "Open the main menu"),
        BotCommand("help", "How to use the bot"),
        BotCommand("f", "Report a problem to admin"),
        BotCommand("status", "Check bot status"),
        BotCommand("stop", "Stop your active task"),
        BotCommand("redeem", "Redeem an access key"),
        BotCommand("keystatus", "Check clipper/editor access"),
    ]
    admin_commands = public_commands + [
        BotCommand("setcookies", "Set YouTube cookies"),
        BotCommand("addproxies", "Add rotating proxies"),
        BotCommand("setproxy", "Add a rotating proxy"),
        BotCommand("listproxies", "List rotating proxies"),
        BotCommand("testproxies", "Benchmark and rank proxies"),
        BotCommand("clearproxies", "Remove all proxies"),
        BotCommand("clearproxy", "Remove all proxies"),
        BotCommand("clearcookies", "Remove YouTube cookies"),
        BotCommand("authstatus", "Check cookies and proxies"),
        BotCommand("transcriptstatus", "Check local transcript engine"),
        BotCommand("jobs", "Live processing dashboard"),
        BotCommand("history", "User start history and details"),
        BotCommand("broadcast", "Broadcast message to all users"),
        BotCommand("last10", "Last 10 users and their latest action"),
        BotCommand("genkey", "Generate clipper/editor access key"),
        BotCommand("keys", "List access keys"),
        BotCommand("keystatus", "Check your access expiry"),
        BotCommand("stopall", "Stop every active task"),
        BotCommand("stopuser", "Stop tasks for a user ID"),
        BotCommand("myid", "Show administrator account ID"),
        BotCommand("cancel", "Cancel an admin action"),
        BotCommand("allow", "Enable Auto Clipper for all users"),
        BotCommand("remove", "Disable Auto Clipper for normal users"),
        BotCommand("clipperstatus", "Check Auto Clipper access/status"),
        BotCommand("alloweditor", "Enable AI Video Editor for all users"),
        BotCommand("removeeditor", "Disable AI Video Editor for normal users"),
        BotCommand("editorstatus", "Check AI Video Editor access/status"),
    ]

    await application.bot.set_my_commands(public_commands)
    await application.bot.set_my_commands(
        public_commands,
        scope=BotCommandScopeAllPrivateChats(),
    )
    if OWNER_ID > 0:
        try:
            await application.bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(chat_id=OWNER_ID),
            )
        except TelegramError as exc:
            logger.warning(
                "Could not set administrator command scope: %s",
                exc,
            )


def build_application() -> Application:
    return (
        Application.builder()
        .token(BOT_TOKEN)
        .base_url(f"{LOCAL_BOT_API_URL}/bot")
        .base_file_url(f"{LOCAL_BOT_API_URL}/file/bot")
        .local_mode(True)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .concurrent_updates(64)
        .connect_timeout(60)
        .read_timeout(3600)
        .write_timeout(3600)
        .media_write_timeout(7200)
        .pool_timeout(180)
        .connection_pool_size(128)
        .get_updates_connect_timeout(30)
        .get_updates_read_timeout(60)
        .get_updates_write_timeout(60)
        .get_updates_connection_pool_size(8)
        .build()
    )


def self_test() -> None:
    print("Python syntax and standard imports: OK")
    print(f"Base directory: {BASE_DIR}")
    print(f"FFmpeg: {shutil.which('ffmpeg') or 'not found'}")
    print(f"FFprobe: {shutil.which('ffprobe') or 'not found'}")
    print(
        f"Whisper model: {WHISPER_MODEL_NAME} "
        f"({WHISPER_DEVICE}/{WHISPER_COMPUTE_TYPE})"
    )
    print(
        f"English PDF font: {'found' if DEJAVU_FONT.exists() else 'not found'}"
    )

    try:
        print(f"Urdu PDF font: {find_urdu_font()}")
    except Exception as exc:
        print(f"Urdu PDF font: not found ({exc})")

    sample_rows = [
        (1.0, 4.2, "Hello everyone."),
        (4.3, 9.0, "Today we are discussing this topic."),
        (9.5, 13.0, "Thanks for inviting me."),
    ]
    paragraphs = build_segment_paragraphs(sample_rows)
    if not paragraphs:
        raise RuntimeError("Local transcript paragraph self-test failed")
    if paragraphs[0].start < 0.99 or paragraphs[-1].end < 12.99:
        raise RuntimeError("Local transcript timestamp self-test failed")

    try:
        from deep_translator import GoogleTranslator  # noqa: F401
        print("Google Translator module: OK")
    except Exception as exc:
        print(f"Google Translator module: not available ({exc})")

    gpu_ai_ok, gpu_ai_reason = realesrgan_engine_status()
    cpu_ai_ok, cpu_ai_reason = cpu_ai_engine_status()
    ai_ok = gpu_ai_ok or cpu_ai_ok
    ai_reason = (
        "Real-ESRGAN GPU AI ready"
        if gpu_ai_ok
        else (
            "OpenCV FSRCNN CPU AI ready"
            if cpu_ai_ok
            else f"GPU: {gpu_ai_reason}; CPU: {cpu_ai_reason}"
        )
    )
    print(
        "Local upscale engine: "
        + ("Real-ESRGAN ready" if ai_ok else f"Fast HD fallback ({ai_reason})")
    )
    print(
        "Local transcript parsing, natural paragraphs, timestamps, "
        "and translation integration: OK"
    )
    print("Enhance callbacks: adaptive Fast HD / AI upscale + post-delivery Upscale = registered")
    print(
        "Auto Clipper Pro: exact durations, duplicate filtering, Smart "
        "Reframe V2, selectable word-sync caption templates, white hook, "
        "keyframe zoom and progressive delivery = registered"
    )
    print(
        "AI Video Editor: file + YouTube timestamp workflows, active-speaker "
        "dynamic Smart Reframe and word-synced captions = registered"
    )


def install_direct_owner_ai_key_handlers(app) -> None:
    """Register Agnes key commands. Agnes is viral-selection only."""
    if app.bot_data.get("_direct_owner_ai_keys"):
        return
    for name, callback in (
        ("addagneskey", command_add_agnes_key),
        ("clearagneskey", command_clear_agnes_key),
        ("agnesstatus", command_agnes_status),
        ("agnestest", command_agnes_test),
    ):
        if callback is not None:
            app.add_handler(CommandHandler(name, callback), group=-20)
    app.bot_data["_direct_owner_ai_keys"] = True


def main() -> None:
    if "--self-test" in sys.argv:
        self_test()
        return

    clear_old_downloads()
    check_local_bot_api()

    app = build_application()
    try:
        from master_engine.telegram_bridge import install_master_engine
        install_master_engine(app, sys.modules[__name__])
        logger.info("Master Editor 7.8 bridge installed")
    except Exception:
        logger.exception("Master Editor bridge failed to install")
        raise
    install_direct_owner_ai_key_handlers(app)
    # Agnes key management is independent of the local video editing engine.
    if install_viralforge_handlers is not None:
        try:
            install_viralforge_handlers(app)
        except Exception:
            logger.exception("Could not install ViralForge Telegram handlers")
    app.add_handler(CommandHandler("start", command_start))
    app.add_handler(CommandHandler("help", command_help))
    app.add_handler(CommandHandler("addbot", command_addbot))
    app.add_handler(CommandHandler("status", command_status))
    app.add_handler(CommandHandler(["stop", "canceljob"], command_stop))

    app.add_handler(CommandHandler(["myid", "whoami"], command_myid))
    app.add_handler(
        CommandHandler(["setcookies", "signin"], command_setcookies)
    )
    app.add_handler(
        CommandHandler(["addproxies", "setproxy"], command_addproxies)
    )
    app.add_handler(CommandHandler("listproxies", command_listproxies))
    app.add_handler(CommandHandler("testproxies", command_testproxies))
    app.add_handler(
        CommandHandler(
            ["clearproxies", "clearproxy"],
            command_clearproxies,
        )
    )
    app.add_handler(CommandHandler("clearcookies", command_clearcookies))
    app.add_handler(CommandHandler("authstatus", command_authstatus))
    app.add_handler(CommandHandler(["transcriptstatus", "apicheck", "transcriptapi"], command_apicheck))
    app.add_handler(CommandHandler("jobs", command_live))
    app.add_handler(CommandHandler("history", command_history))
    app.add_handler(CommandHandler(["broadcast", "bc"], command_broadcast))
    app.add_handler(CommandHandler("last10", command_last10))
    app.add_handler(CommandHandler(["f", "feedback", "support"], command_feedback))
    app.add_handler(CommandHandler("genkey", command_genkey))
    app.add_handler(CommandHandler("redeem", command_redeem))
    app.add_handler(CommandHandler("keystatus", command_keystatus))
    app.add_handler(CommandHandler("keys", command_keys))
    app.add_handler(CommandHandler("stopall", command_stopall))
    app.add_handler(CommandHandler("stopuser", command_stopuser))
    app.add_handler(CommandHandler("cancel", command_cancel))
    app.add_handler(
        CommandHandler(["allow", "allowclipper"], command_allow_clipper)
    )
    app.add_handler(
        CommandHandler(["remove", "removeclipper"], command_remove_clipper)
    )
    app.add_handler(
        CommandHandler(["clipperstatus"], command_clipper_status)
    )
    app.add_handler(
        CommandHandler(
            ["alloweditor", "allowvideoeditor"],
            command_allow_editor,
        )
    )
    app.add_handler(
        CommandHandler(
            ["removeeditor", "removevideoeditor"],
            command_remove_editor,
        )
    )
    app.add_handler(
        CommandHandler(["editorstatus"], command_editor_status)
    )

    app.add_handler(
        CallbackQueryHandler(
            handle_admin_channel_callback,
            pattern=r"^channel:(home|add|open|profile|train|clipper|editor|edfile|edyoutube|delete)(?::[a-f0-9]{8})?$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_menu_callback,
            pattern=r"^menu:(home|download|enhance|transcript|clipper|editor|analyzer|status|help|admin|channelstudio)$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_admin_callback,
            pattern=r"^admin:(cookies|proxies|listproxies|testproxies|status|apicheck|clearcookies|clearproxies|live|deepviral|deepviral_on|deepviral_off|deepviral_status|stopall)$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_stop_callback,
            pattern=r"^stop:active$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_cancel_callback,
            pattern=r"^c:[a-f0-9]{10}$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_youtube_callback,
            pattern=r"^(q:[a-f0-9]{10}:[^:]+|a:[a-f0-9]{10})$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_download_flow_callback,
            pattern=r"^downloadflow:(full|timestamp)$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_analyzer_callback,
            pattern=r"^analyzer:(youtube|file)$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_editor_flow_callback,
            pattern=r"^editorflow:(file|filetimestamp|youtube)$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_video_editor_split_toggle,
            pattern=r"^vedsplit:[a-f0-9]{10}$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_video_editor_callback,
            pattern=r"^vedit:[a-f0-9]{10}:[a-z0-9_]+$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_auto_clipper_mode_callback,
            pattern=r"^clipmode:(quick|deep)$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_auto_clipper_duration_callback,
            pattern=r"^clipdur:[a-f0-9]{10}:(30|60|90|120|all)$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_auto_clipper_back_callback,
            pattern=r"^clipback:[a-f0-9]{10}$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_auto_clipper_callback,
            pattern=r"^clip:[a-f0-9]{10}:(30|60|90|120|all):[a-z0-9_]+$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_transcript_callback,
            pattern=r"^t:[a-f0-9]{10}:(en|ur|bi|ru|er|ur_ru):(txt|pdf|srt)$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_enhance_callback,
            pattern=r"^e:[a-f0-9]{10}:(studio720|studio1080|ai2x)$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_result_quality_menu,
            pattern=r"^resultq:[a-f0-9]{12}$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_result_quality_set,
            pattern=r"^resultqset:[a-f0-9]{12}:(1080|720|back)$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_upscale_callback,
            pattern=r"^upscale:[a-f0-9]{12}$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_reedit_callback,
            pattern=r"^reedit:[a-f0-9]{12}$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_result_transcript_callback,
            pattern=r"^resulttrans:[a-f0-9]{12}$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_reedit_option_callback,
            pattern=r"^reopt:[a-f0-9]{12}:(nohook|none|reference_green|reference_yellow|viral_mix|back)$",
        )
    )

    app.add_handler(
        MessageHandler(
            filters.VIDEO | filters.Document.ALL,
            handle_media,
        )
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )
    app.add_error_handler(error_handler)

    logger.info(
        "Bot started: downloads=%d, transcripts=%d, studio_hd=%d, clipper=%d, editor=%d, uploads=%d",
        MAX_CONCURRENT_DOWNLOADS,
        MAX_CONCURRENT_TRANSCRIPTS,
        MAX_CONCURRENT_FAST_ENHANCE,
        MAX_CONCURRENT_AUTO_CLIPPER,
        MAX_CONCURRENT_VIDEO_EDITOR,
        MAX_CONCURRENT_UPLOADS,
    )

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
