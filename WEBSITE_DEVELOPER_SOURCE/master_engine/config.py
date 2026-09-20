from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from .models import CaptionStyle


PACKAGE_DIR = Path(__file__).resolve().parent
ASSET_DIR = Path(os.getenv("MASTER_ENGINE_ASSETS", str(PACKAGE_DIR / "assets")))
WORK_DIR = Path(os.getenv("MASTER_ENGINE_WORK", "/root/editingbot_v78/master_engine_jobs"))
CACHE_DIR = Path(os.getenv("MASTER_ENGINE_CACHE", "/root/editingbot_v78/master_engine_cache"))


@dataclass(frozen=True)
class Settings:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    preserve_source_fps: bool = True
    sample_seconds: float = 0.20
    split_min_hold_seconds: float = 3.0
    speaker_confirm_seconds: float = 0.80
    layout_min_hold_seconds: float = 3.0
    camera_min_hold_seconds: float = 2.40
    crop_deadband: float = 0.055
    scene_cut_release_seconds: float = 0.65
    chunk_seconds: float = 24.0
    crf: int = 15
    preset: str = "fast"
    audio_bitrate: str = "256k"
    whisper_model: str = "distil-large-v3"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    enable_stock_broll: bool = False
    enable_silence_tightening: bool = True
    enable_graphics: bool = True
    enable_audio_polish: bool = True
    enable_colour_polish: bool = True
    captions_enabled: bool = True
    split_enabled: bool = True
    motion_enabled: bool = True
    motion_profile: str = "smooth"
    caption_scope: str = "default"
    ffmpeg_compatibility: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        def flag(name: str, default: bool) -> bool:
            raw = os.getenv(name)
            if raw is None:
                return default
            return raw.strip().lower() not in {"0", "false", "no", "off"}

        return cls(
            # Fixed high-quality mobile master. The engine never silently
            # exports a 720p/low-resolution TikTok variant.
            width=1080,
            height=1920,
            fps=max(24, min(60, int(os.getenv("MASTER_FPS", "30")))),
            preserve_source_fps=flag("MASTER_PRESERVE_SOURCE_FPS", True),
            sample_seconds=max(0.12, min(.30,float(os.getenv("MASTER_SAMPLE_SECONDS", "0.20")))),
            split_min_hold_seconds=3.0,
            speaker_confirm_seconds=max(0.5, float(os.getenv("MASTER_SPEAKER_CONFIRM_SECONDS", "0.80"))),
            layout_min_hold_seconds=3.0,
            camera_min_hold_seconds=max(1.8, float(os.getenv("MASTER_CAMERA_HOLD_SECONDS", "2.40"))),
            crop_deadband=max(0.035, min(.14, float(os.getenv("MASTER_CROP_DEADBAND", "0.055")))),
            scene_cut_release_seconds=max(0.35, float(os.getenv("MASTER_SCENE_RELEASE_SECONDS", "0.65"))),
            chunk_seconds=max(8.0, min(120.0,float(os.getenv("MASTER_CHUNK_SECONDS", "24")))),
            crf=max(14, min(22, int(os.getenv("MASTER_CRF", "15")))),
            preset=os.getenv("MASTER_PRESET", "fast").strip() or "fast",
            audio_bitrate=os.getenv("MASTER_AUDIO_BITRATE", "256k").strip() or "256k",
            whisper_model=os.getenv("MASTER_WHISPER_MODEL", "distil-large-v3").strip(),
            whisper_device=os.getenv("MASTER_WHISPER_DEVICE", "cpu").strip(),
            whisper_compute_type=os.getenv("MASTER_WHISPER_COMPUTE", "int8").strip(),
            enable_stock_broll=False,
            enable_silence_tightening=flag("MASTER_SILENCE_TIGHTEN", True),
            enable_graphics=flag("MASTER_GRAPHICS", True),
            enable_audio_polish=flag("MASTER_AUDIO_POLISH", True),
            enable_colour_polish=flag("MASTER_COLOUR_POLISH", True),
            captions_enabled=flag("MASTER_CAPTIONS", True),
            split_enabled=flag("MASTER_AUTO_SPLIT", True),
            motion_enabled=flag("MASTER_SMOOTH_FOLLOW", True),
            caption_scope=os.getenv("MASTER_CAPTION_SCOPE","default"),
            ffmpeg_compatibility=flag("MASTER_FFMPEG_SAFE_MODE", False),
        )


def load_caption_styles() -> Dict[str, CaptionStyle]:
    from .caption_catalog import catalog
    return catalog()


def prepare_directories() -> None:
    for path in (
        ASSET_DIR,
        WORK_DIR,
        CACHE_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
