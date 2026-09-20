from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from media_transport import BrokerConfig, FormatCandidate, MediaTransportBroker

_BOUND_BROKER: Optional[MediaTransportBroker] = None
BOT_DIR = Path(os.getenv("BOT_DIR", str(Path(__file__).resolve().parents[1]))).resolve()
AUTH_JSON = BOT_DIR / "auth" / "runtime_auth.json"
HEALTH_FILE = BOT_DIR / "data" / "media_route_health.json"
DEFAULT_DOWNLOAD_DIR = BOT_DIR / "downloads"


def bind_broker(broker: Any) -> None:
    """Bind Master Editor to the exact broker instance used by bot.py."""
    global _BOUND_BROKER
    if broker is None:
        return
    if not isinstance(broker, MediaTransportBroker):
        raise TypeError("Master Editor received an incompatible media transport broker")
    _BOUND_BROKER = broker


def _load_auth() -> tuple[Optional[Path], list[str]]:
    if not AUTH_JSON.exists():
        return None, []
    try:
        raw = json.loads(AUTH_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None, []
    cookie_raw = str(raw.get("cookie_file") or "").strip()
    cookie = Path(cookie_raw).expanduser() if cookie_raw else None
    if cookie is not None and not cookie.is_file():
        cookie = None
    proxies: list[str] = []
    for item in raw.get("proxies") or []:
        if not isinstance(item, dict):
            continue
        value = str(item.get("url") or "").strip()
        disabled_until = float(item.get("disabled_until") or 0.0)
        if value and disabled_until <= __import__("time").time() and value not in proxies:
            proxies.append(value)
    return cookie, proxies[:3]


def _standalone_broker() -> MediaTransportBroker:
    """Fallback for CLI/tests before Telegram binds the live bot broker."""
    return MediaTransportBroker(
        BrokerConfig(
            download_dir=DEFAULT_DOWNLOAD_DIR,
            health_file=HEALTH_FILE,
            bgutil_base_url=os.getenv("BGUTIL_BASE_URL", "http://127.0.0.1:4417").strip() or "http://127.0.0.1:4417",
            hedge_delay_seconds=max(1.0, min(3.0, float(os.getenv("MEDIA_HEDGE_DELAY_SECONDS", "1.5")))),
            hedge_transfer_bytes=max(32768, int(os.getenv("MEDIA_HEDGE_TRANSFER_BYTES", "131072"))),
            route_timeout_seconds=max(180.0, float(os.getenv("MEDIA_ROUTE_TIMEOUT_SECONDS", "1800"))),
            extract_timeout_seconds=max(20.0, float(os.getenv("MEDIA_EXTRACT_TIMEOUT_SECONDS", "45"))),
            verify_full_range_max_seconds=max(30.0, float(os.getenv("MEDIA_VERIFY_FULL_RANGE_MAX_SECONDS", "180"))),
            verify_sample_seconds=max(2.0, float(os.getenv("MEDIA_VERIFY_SAMPLE_SECONDS", "5"))),
            force_ipv4=True,
            concurrent_fragments=max(1, int(os.getenv("YTDLP_CONCURRENT_FRAGMENTS", "4"))),
            http_chunk_size=max(0, int(os.getenv("YTDLP_HTTP_CHUNK_SIZE", str(5 * 1024 * 1024)))),
            socket_timeout=30,
            retries=3,
            fragment_retries=3,
            ejs_enabled=True,
            deno_path=os.getenv("DENO_BIN", "/usr/local/bin/deno").strip(),
            ffmpeg_bin="ffmpeg",
            ffprobe_bin="ffprobe",
            pytubefix_enabled=os.getenv("MEDIA_PYTUBEFIX", "1").strip().lower() not in {"0", "false", "no", "off"},
        ),
        cookie_getter=lambda: _load_auth()[0],
        proxy_getter=lambda: _load_auth()[1],
    )


def _broker() -> MediaTransportBroker:
    return _BOUND_BROKER or _standalone_broker()


class _CancelAdapter:
    def __init__(self, check: Optional[Callable[[], bool]]) -> None:
        self._check = check

    def is_set(self) -> bool:
        if self._check is None:
            return False
        try:
            return bool(self._check())
        except Exception:
            return False


def _formats(max_height: int) -> list[FormatCandidate]:
    height = max(144, min(2160, int(max_height or 2160)))
    return [
        FormatCandidate(
            f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best",
            "mp4",
            True,
        )
    ]


def _move_result(path: Path, destination: Path, basename: str) -> Path:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower() or ".mp4"
    target = destination / f"{basename}{suffix}"
    if target.exists():
        target.unlink()
    try:
        path.replace(target)
    except OSError:
        shutil.move(str(path), str(target))
    return target.resolve()


def download_full_source(
    url: str,
    destination: Path,
    cancel_check: Optional[Callable[[], bool]] = None,
    *,
    target_height: int = 2160,
) -> Path:
    """Full YouTube source via the central multi-route broker."""
    event = _CancelAdapter(cancel_check)
    stem = f"master_full_{uuid.uuid4().hex[:10]}"

    async def runner():
        broker = _broker()
        return await broker.download_video(
            url,
            stem,
            _formats(target_height),
            target_height=target_height,
            progress_cb=None,
            cancel_event=event,
        )

    result = asyncio.run(runner())
    return _move_result(result.path, Path(destination), "source")


def download_range_source(
    url: str,
    destination: Path,
    start: float,
    end: float,
    cancel_check: Optional[Callable[[], bool]] = None,
    *,
    target_height: int = 2160,
    full_fallback: bool = True,
) -> Path:
    """Range-first YouTube transport; full-source download is final fallback only."""
    if float(end) <= float(start):
        raise ValueError("Timestamp end must be after start")
    event = _CancelAdapter(cancel_check)
    stem = f"master_range_{uuid.uuid4().hex[:10]}"

    async def runner():
        broker = _broker()
        return await broker.download_timestamp(
            url,
            stem,
            _formats(target_height),
            start=float(start),
            end=float(end),
            target_height=target_height,
            progress_cb=None,
            cancel_event=event,
            full_fallback=bool(full_fallback),
        )

    result = asyncio.run(runner())
    return _move_result(result.path, Path(destination), "youtube_range_master")
