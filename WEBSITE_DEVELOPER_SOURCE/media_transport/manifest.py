from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence


@dataclass(frozen=True)
class RouteSpec:
    name: str
    label: str
    player_client: Optional[str] = None
    proxy: Optional[str] = None
    use_cookies: bool = False
    require_bgutil: bool = False
    engine: str = "yt-dlp"


@dataclass
class MediaProbe:
    path: Path
    duration: float
    width: int
    height: int
    video_streams: int
    audio_streams: int
    format_name: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TransportResult:
    path: Path
    route: RouteSpec
    probe: Optional[MediaProbe] = None
    selector_index: int = 0
    selector: str = ""
    output_container: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class BrokerConfig:
    download_dir: Path
    health_file: Path
    bgutil_base_url: str = "http://127.0.0.1:4416"
    hedge_delay_seconds: float = 1.5
    hedge_transfer_bytes: int = 131072
    route_timeout_seconds: float = 1800.0
    extract_timeout_seconds: float = 45.0
    verify_full_range_max_seconds: float = 180.0
    verify_sample_seconds: float = 5.0
    force_ipv4: bool = True
    concurrent_fragments: int = 8
    http_chunk_size: int = 10 * 1024 * 1024
    socket_timeout: int = 30
    retries: int = 3
    fragment_retries: int = 3
    ejs_enabled: bool = True
    deno_path: str = ""
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    ytdlp_bin: str = ""
    pytubefix_enabled: bool = True


@dataclass(frozen=True)
class FormatCandidate:
    selector: str
    container: str
    telegram_playable: bool = False


@dataclass
class DownloadPlan:
    url: str
    stem: str
    mode: str
    formats: Sequence[FormatCandidate] = ()
    target_height: Optional[int] = None
    start: Optional[float] = None
    end: Optional[float] = None
    audio_quality: str = "160K"
    subtitle_lang: str = ""
    require_video: bool = False
    require_audio: bool = False
    final_full_then_trim: bool = False
