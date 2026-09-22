from __future__ import annotations

import re
import asyncio
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
import logging
from pydantic import BaseModel, Field

logger = logging.getLogger("clip_studio.youtube")

try:
    import yt_dlp
except ImportError:
    yt_dlp = None
    logger.warning("yt_dlp is not installed; YouTube metadata will use heuristic fallbacks.")

router = APIRouter(prefix="/api/youtube", tags=["YouTube"])


class MetadataRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class MetadataResponse(BaseModel):
    url: str
    video_id: str
    title: str
    thumbnail: str
    duration: float
    duration_formatted: str
    channel: Optional[str] = None


def format_seconds(seconds: float) -> str:
    s = int(seconds)
    hours = s // 3600
    minutes = (s % 3600) // 60
    secs = s % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def extract_video_id(url: str) -> Optional[str]:
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11}).*",
        r"(?:youtu\.be\/)([0-9A-Za-z_-]{11})",
        r"(?:shorts\/)([0-9A-Za-z_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def fetch_yt_metadata_sync(url: str) -> dict:
    if yt_dlp is None:
        return {}
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info or {}


from ..utils.security import validate_source_url
from ..auth import get_current_user
from ..utils.rate_limit import check_rate_limit, get_client_ip


@router.post("/metadata", response_model=MetadataResponse)
async def get_metadata(req: MetadataRequest, request: Request, _user=Depends(get_current_user)) -> MetadataResponse:
    await check_rate_limit(
        key=f"youtube_metadata:{get_client_ip(request)}",
        max_requests=20,
        window_seconds=3600,
        error_message="Too many metadata requests. Please try again later.",
    )
    # SSRF check
    req.url = validate_source_url(req.url)
    video_id = extract_video_id(req.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL. Please provide a valid video link.")

    try:
        # Run yt-dlp metadata fetch in thread pool
        info = await asyncio.to_thread(fetch_yt_metadata_sync, req.url)
        duration = float(info.get("duration") or 0)
        title = info.get("title") or f"YouTube Video ({video_id})"
        thumbnail = info.get("thumbnail") or f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
        channel = info.get("uploader") or info.get("channel") or "YouTube Creator"

        return MetadataResponse(
            url=req.url,
            video_id=video_id,
            title=title,
            thumbnail=thumbnail,
            duration=duration,
            duration_formatted=format_seconds(duration),
            channel=channel,
        )
    except Exception as exc:
        # Fallback to high-res thumbnail and standard placeholder if yt-dlp blocks
        return MetadataResponse(
            url=req.url,
            video_id=video_id,
            title=f"YouTube Video ({video_id})",
            thumbnail=f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
            duration=600.0,
            duration_formatted="10:00",
            channel="YouTube Video",
        )
