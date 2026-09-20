from __future__ import annotations

# MASTER_MEDIA_TRANSPORT_V11

import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from .models import MediaInfo
from .utils import CommandError, run


def require_binaries() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if not shutil.which(name)]
    if missing:
        raise CommandError("Missing required program(s): " + ", ".join(missing))


def _ratio(value: Any) -> float:
    text = str(value or "0/1")
    if "/" in text:
        left, right = text.split("/", 1)
        try:
            return float(left) / max(1e-9, float(right))
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def probe(path: Path) -> MediaInfo:
    require_binaries()
    if not path.exists() or path.stat().st_size <= 0:
        raise CommandError(f"Input video does not exist or is empty: {path}")
    result = run(
        [
            "ffprobe", "-v", "error", "-show_streams", "-show_format",
            "-of", "json", str(path),
        ],
        timeout=90,
    )
    data = json.loads(result.stdout or "{}")
    streams = list(data.get("streams") or [])
    video = next((row for row in streams if row.get("codec_type") == "video"), None)
    audio = next((row for row in streams if row.get("codec_type") == "audio"), None)
    if not video:
        raise CommandError("Input has no readable video stream")
    duration = float(
        video.get("duration")
        or (data.get("format") or {}).get("duration")
        or 0.0
    )
    if duration <= 0:
        raise CommandError("Input video duration could not be detected")
    fps = _ratio(video.get("avg_frame_rate") or video.get("r_frame_rate")) or 30.0
    # FFmpeg and OpenCV apply the display matrix before cropping/detection.
    # Plan against those displayed pixels, not the unrotated coded dimensions.
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    rotation = 0.0
    raw_rotation = next((side.get("rotation") for side in video.get("side_data_list", [])
                         if side.get("rotation") is not None),
                        (video.get("tags") or {}).get("rotate", 0))
    try:
        rotation = float(raw_rotation)
    except (TypeError, ValueError):
        rotation = 0.0
    if not math.isfinite(rotation):
        rotation = 0.0
    quarter_turns = round(rotation / 90.0)
    if abs(rotation - quarter_turns * 90.0) < 1.0 and quarter_turns % 2:
        width, height = height, width
    if width <= 0 or height <= 0:
        raise CommandError("Input video dimensions could not be detected")
    return MediaInfo(
        path=path.resolve(),
        duration=duration,
        width=width,
        height=height,
        fps=max(1.0, min(120.0, fps)),
        has_audio=audio is not None,
        video_codec=str(video.get("codec_name") or ""),
        audio_codec=str((audio or {}).get("codec_name") or ""),
        rotation=rotation,
    )


def download_source(url: str, destination: Path, cancel_check=None) -> Path:
    """Download a full YouTube source through the shared multi-route transport."""
    from .transport_adapter import download_full_source
    try:
        return download_full_source(url, destination, cancel_check, target_height=2160)
    except Exception as exc:
        if isinstance(exc, CommandError):
            raise
        raise CommandError(f"Could not download source through media transport: {str(exc)[:700]}") from exc


def download_youtube_range(
    url: str,
    destination: Path,
    start: float,
    end: float,
    cancel_check=None,
) -> Path:
    """Range-first central transport. Full source is attempted only as final fallback."""
    from .transport_adapter import download_range_source
    try:
        return download_range_source(
            url, destination, start, end, cancel_check,
            target_height=2160, full_fallback=True,
        )
    except Exception as exc:
        if isinstance(exc, CommandError):
            raise
        raise CommandError(f"Could not download YouTube range through media transport: {str(exc)[:700]}") from exc



def extract_range(source: Path, output: Path, start: float, end: float, cancel_check=None) -> Path:
    if end <= start:
        raise CommandError("End timestamp must be after start timestamp")
    media = probe(source)
    if start < 0 or start >= media.duration:
        raise CommandError("Start timestamp is outside the source duration")
    if end > media.duration + 0.5:
        raise CommandError("End timestamp is outside the source duration")
    duration = min(media.duration, end) - start
    output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
            "-fflags", "+genpts+discardcorrupt",
            "-ss", f"{max(0.0, start):.3f}", "-i", str(source),
            "-t", f"{duration:.3f}",
            "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
            "-c:v", "libx264", "-preset", "medium", "-crf", "12",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
            "-avoid_negative_ts", "make_zero", "-movflags", "+faststart",
            str(output),
        ],
        timeout=max(900, duration * 14),
        cancel_check=cancel_check,
    )
    return output


def _timestamp(value: str) -> Optional[float]:
    value = str(value or "").strip()
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
        return None if seconds >= 60 else minutes * 60 + seconds
    hours, minutes, seconds = numbers
    return None if minutes >= 60 or seconds >= 60 else hours * 3600 + minutes * 60 + seconds


def parse_timestamp_range(text: str):
    match = re.search(
        r"(?P<a>\d+(?::\d{1,2}){1,2}|\d+(?:\.\d+)?)\s*(?:-|–|—|to|until)\s*"
        r"(?P<b>\d+(?::\d{1,2}){1,2}|\d+(?:\.\d+)?)",
        text or "",
        flags=re.I,
    )
    if not match:
        return None, None
    return _timestamp(match.group("a")), _timestamp(match.group("b"))


def parse_link_and_range(text: str):
    link = re.search(r"https?://\S+", text or "")
    if not link:
        return None, None, None
    cleaned_url = link.group(0).rstrip(".,);]}")
    rest = (text or "").replace(link.group(0), " ")
    start, end = parse_timestamp_range(rest)
    return cleaned_url, start, end
