from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Sequence

from .errors import (
    ErrorKind,
    MediaTransportError,
    TransportCancelled,
    classify_error,
    is_fast_fallback_error,
)
from .manifest import (
    BrokerConfig,
    DownloadPlan,
    FormatCandidate,
    MediaProbe,
    RouteSpec,
    TransportResult,
)
from .pot_bgutil import check_compatibility, require_compatible
from .pytubefix_route import (
    available as pytubefix_available,
    download_audio as pytubefix_download_audio,
    download_video as pytubefix_download_video,
    synthetic_info as pytubefix_synthetic_info,
)
from .route_health import RouteHealthStore
from .yt_native import (
    build_audio_command,
    build_download_command,
    build_extract_command,
    build_subtitle_command,
)

logger = logging.getLogger("media_transport")

ProgressCallback = Callable[[str], Awaitable[None]]
CookieGetter = Callable[[], Optional[Path]]
ProxyGetter = Callable[[], Sequence[str]]
ProxyOK = Callable[..., None]
ProxyFail = Callable[[Optional[str]], None]


@dataclass
class _RunningAttempt:
    route: RouteSpec
    prefix: str
    command: list[str]
    process: asyncio.subprocess.Process
    started: float
    transfer_event: asyncio.Event
    fatal_event: asyncio.Event
    lines: deque[str]
    reader_task: asyncio.Task
    monitor_task: asyncio.Task
    result_task: asyncio.Task
    last_progress: float = 0.0


class MediaTransportBroker:
    """Central YouTube transport broker.

    Every route receives the original YouTube URL and creates a fresh yt-dlp
    process, so signed googlevideo URLs are never carried across route attempts.
    At most two yt-dlp attempts are alive at once, and the second attempt is only
    hedged after a short no-transfer window or an immediately classified route
    failure (403/auth/PO/EJS/provider).
    """

    def __init__(
        self,
        config: BrokerConfig,
        *,
        cookie_getter: CookieGetter,
        proxy_getter: ProxyGetter,
        mark_proxy_ok: Optional[ProxyOK] = None,
        mark_proxy_fail: Optional[ProxyFail] = None,
    ) -> None:
        self.config = config
        self.config.download_dir = Path(self.config.download_dir)
        self.config.download_dir.mkdir(parents=True, exist_ok=True)
        self.cookie_getter = cookie_getter
        self.proxy_getter = proxy_getter
        self.mark_proxy_ok = mark_proxy_ok
        self.mark_proxy_fail = mark_proxy_fail
        self.health = RouteHealthStore(Path(self.config.health_file))

    # ------------------------------------------------------------------
    # Route manifest
    # ------------------------------------------------------------------
    def routes(self) -> list[RouteSpec]:
        routes = [
            RouteSpec("native_public", "native public", use_cookies=False),
            RouteSpec(
                "mweb_bgutil",
                "mweb + BgUtil PO token",
                player_client="mweb",
                use_cookies=False,
                require_bgutil=True,
            ),
            RouteSpec(
                "web_embedded",
                "web_embedded alternate client",
                player_client="web_embedded",
                use_cookies=False,
            ),
        ]
        seen: set[str] = set()
        try:
            proxies = list(self.proxy_getter() or [])
        except Exception:
            logger.exception("Could not read ranked proxy candidates")
            proxies = []
        for index, proxy in enumerate(proxies, 1):
            value = str(proxy or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            routes.append(
                RouteSpec(
                    f"proxy_{index}",
                    f"ranked proxy #{index} public",
                    proxy=value,
                    use_cookies=False,
                )
            )

        cookie = self._cookie_file()
        if cookie:
            routes.append(
                RouteSpec(
                    "auth_cookies",
                    "authenticated cookies fallback",
                    use_cookies=True,
                )
            )
        return routes

    def _cookie_file(self) -> Optional[Path]:
        try:
            value = self.cookie_getter()
        except Exception:
            logger.exception("Cookie getter failed")
            return None
        if not value:
            return None
        path = Path(value).expanduser()
        return path if path.is_file() and path.stat().st_size > 0 else None

    # ------------------------------------------------------------------
    # Startup/provider checks
    # ------------------------------------------------------------------
    def startup_check(self) -> dict[str, Any]:
        status = check_compatibility(self.config.bgutil_base_url, timeout=2.0, cache_seconds=0)
        return {
            "bgutil_ok": status.ok,
            "bgutil_plugin_version": status.plugin_version,
            "bgutil_server_version": status.server_version,
            "bgutil_detail": status.detail,
            "pytubefix_available": pytubefix_available(),
            "routes": [route.name for route in self.routes()],
        }

    # ------------------------------------------------------------------
    # Metadata extraction (fresh process per route)
    # ------------------------------------------------------------------
    async def extract_info(
        self,
        url: str,
        *,
        cancel_event: Any = None,
    ) -> dict[str, Any]:
        failures: list[str] = []
        cookie = self._cookie_file()
        for route in self.routes():
            self._check_cancel(cancel_event)
            if route.require_bgutil:
                status = await asyncio.to_thread(
                    check_compatibility,
                    self.config.bgutil_base_url,
                    timeout=2.0,
                    cache_seconds=10.0,
                )
                if not status.ok:
                    failures.append(f"{route.label}: {status.detail}")
                    self.health.mark_failure(route.name, ErrorKind.PROVIDER_UNAVAILABLE.value, status.detail)
                    continue
            started = time.monotonic()
            command = build_extract_command(self.config, route, url, cookie)
            try:
                output = await self._run_capture(command, self.config.extract_timeout_seconds, cancel_event)
                info = self._parse_json_output(output)
                formats = info.get("formats") or []
                if not any(isinstance(item, dict) and item.get("vcodec") not in (None, "none") for item in formats):
                    raise MediaTransportError(
                        "YouTube returned no downloadable video formats",
                        kind=ErrorKind.FORMAT,
                        route=route.name,
                    )
                elapsed = time.monotonic() - started
                self.health.mark_success(route.name, elapsed)
                self._proxy_ok(route, elapsed, 0)
                info["_transport_route"] = route.name
                return info
            except TransportCancelled:
                raise
            except Exception as exc:
                text = str(exc)
                kind = exc.kind if isinstance(exc, MediaTransportError) else classify_error(text)
                failures.append(f"{route.label}: {text[:260]}")
                self.health.mark_failure(route.name, kind.value, text)
                self._proxy_fail(route)
                logger.warning("YouTube metadata route %s failed [%s]: %s", route.name, kind.value, text)

        if self.config.pytubefix_enabled and pytubefix_available():
            self._check_cancel(cancel_event)
            try:
                info = await asyncio.to_thread(pytubefix_synthetic_info, url)
                formats = info.get("formats") or []
                if formats:
                    self.health.mark_success("pytubefix", 0.0)
                    return info
            except Exception as exc:
                failures.append(f"pytubefix: {str(exc)[:260]}")
                self.health.mark_failure("pytubefix", classify_error(str(exc)).value, str(exc))

        raise MediaTransportError(self._final_error("Could not read this YouTube video", failures))

    # ------------------------------------------------------------------
    # Full video
    # ------------------------------------------------------------------
    async def download_video(
        self,
        url: str,
        stem: str,
        formats: Sequence[FormatCandidate],
        *,
        target_height: Optional[int],
        progress_cb: Optional[ProgressCallback],
        cancel_event: Any,
    ) -> TransportResult:
        plan = DownloadPlan(
            url=url,
            stem=stem,
            mode="video",
            formats=tuple(formats),
            target_height=target_height,
            require_video=True,
            require_audio=True,
        )
        failures: list[str] = []
        try:
            return await self._download_ytdlp_routes(plan, progress_cb, cancel_event, failures)
        except MediaTransportError as exc:
            failures.append(str(exc))

        if self.config.pytubefix_enabled and pytubefix_available():
            self._check_cancel(cancel_event)
            await self._emit(progress_cb, "YouTube routes exhausted; trying independent pytubefix fallback...")
            started = time.monotonic()
            pystem = f"{stem}__pytubefix_{uuid.uuid4().hex[:6]}"
            try:
                path = await asyncio.to_thread(
                    pytubefix_download_video,
                    url,
                    self.config.download_dir,
                    pystem,
                    target_height,
                    self.config.ffmpeg_bin,
                )
                probe = await self._validate_media(
                    path,
                    require_video=True,
                    require_audio=True,
                    target_height=target_height,
                )
                self.health.mark_success("pytubefix", time.monotonic() - started, path.stat().st_size)
                return TransportResult(
                    path=path,
                    route=RouteSpec("pytubefix", "pytubefix independent fallback", engine="pytubefix"),
                    probe=probe,
                    selector_index=max(0, len(formats) - 1),
                    selector="pytubefix",
                    output_container="mp4",
                )
            except Exception as exc:
                failures.append(f"pytubefix: {str(exc)[:260]}")
                self.health.mark_failure("pytubefix", classify_error(str(exc)).value, str(exc))
                self._cleanup_prefix(pystem)

        raise MediaTransportError(self._final_error("Download failed after all YouTube routes", failures))

    # ------------------------------------------------------------------
    # Audio / transcript audio
    # ------------------------------------------------------------------
    async def download_audio(
        self,
        url: str,
        stem: str,
        *,
        audio_quality: str,
        progress_cb: Optional[ProgressCallback],
        cancel_event: Any,
    ) -> TransportResult:
        plan = DownloadPlan(
            url=url,
            stem=stem,
            mode="audio",
            audio_quality=audio_quality,
            require_audio=True,
        )
        failures: list[str] = []
        try:
            return await self._download_ytdlp_routes(plan, progress_cb, cancel_event, failures)
        except MediaTransportError as exc:
            failures.append(str(exc))

        if self.config.pytubefix_enabled and pytubefix_available():
            self._check_cancel(cancel_event)
            await self._emit(progress_cb, "Trying independent audio fallback...")
            pystem = f"{stem}__pytubefix_{uuid.uuid4().hex[:6]}"
            started = time.monotonic()
            try:
                path = await asyncio.to_thread(
                    pytubefix_download_audio,
                    url,
                    self.config.download_dir,
                    pystem,
                    self.config.ffmpeg_bin,
                    audio_quality,
                )
                probe = await self._validate_media(path, require_video=False, require_audio=True)
                self.health.mark_success("pytubefix", time.monotonic() - started, path.stat().st_size)
                return TransportResult(
                    path=path,
                    route=RouteSpec("pytubefix", "pytubefix independent fallback", engine="pytubefix"),
                    probe=probe,
                    selector="pytubefix",
                    output_container="mp3",
                )
            except Exception as exc:
                failures.append(f"pytubefix: {str(exc)[:260]}")
                self.health.mark_failure("pytubefix", classify_error(str(exc)).value, str(exc))
                self._cleanup_prefix(pystem)

        raise MediaTransportError(self._final_error("Audio download failed after all routes", failures))

    # ------------------------------------------------------------------
    # Timestamp/range. Full source is only attempted after every range route.
    # ------------------------------------------------------------------
    async def download_timestamp(
        self,
        url: str,
        stem: str,
        formats: Sequence[FormatCandidate],
        *,
        start: float,
        end: float,
        target_height: Optional[int],
        progress_cb: Optional[ProgressCallback],
        cancel_event: Any,
        full_fallback: bool = True,
    ) -> TransportResult:
        if end <= start:
            raise MediaTransportError("Timestamp end must be after start")
        plan = DownloadPlan(
            url=url,
            stem=stem,
            mode="timestamp",
            formats=tuple(formats),
            target_height=target_height,
            start=float(start),
            end=float(end),
            require_video=True,
            require_audio=True,
        )
        failures: list[str] = []
        try:
            return await self._download_ytdlp_routes(plan, progress_cb, cancel_event, failures)
        except MediaTransportError as exc:
            failures.append(str(exc))

        if not full_fallback:
            raise MediaTransportError(self._final_error("Timestamp range transport failed", failures))

        # Final fallback only: acquire a complete source through the same broker,
        # including the independent pytubefix route, then cut locally.
        await self._emit(progress_cb, "Range transports failed; final fallback is downloading a full source once...")
        full_stem = f"{stem}__fullfallback_{uuid.uuid4().hex[:6]}"
        full: Optional[TransportResult] = None
        try:
            full = await self.download_video(
                url,
                full_stem,
                formats,
                target_height=target_height,
                progress_cb=progress_cb,
                cancel_event=cancel_event,
            )
            self._check_cancel(cancel_event)
            out = self.config.download_dir / f"{stem}__localtrim.mp4"
            duration = max(0.1, float(end) - float(start))
            cmd = [
                self.config.ffmpeg_bin,
                "-y", "-v", "error",
                "-ss", f"{float(start):.3f}",
                "-i", str(full.path),
                "-t", f"{duration:.3f}",
                "-map", "0:v:0", "-map", "0:a:0",
                "-c:v", "libx264", "-preset", "medium", "-crf", "15",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                str(out),
            ]
            await self._run_ffmpeg(cmd, timeout=max(180.0, duration * 6.0), cancel_event=cancel_event)
            probe = await self._validate_media(
                out,
                require_video=True,
                require_audio=True,
                target_height=target_height,
                expected_duration=duration,
                strict_range=True,
            )
            return TransportResult(
                path=out.resolve(),
                route=RouteSpec("full_then_trim", f"full source via {full.route.name} + local trim"),
                probe=probe,
                selector_index=full.selector_index,
                selector=full.selector,
                output_container="mp4",
                extra={"full_source_route": full.route.name},
            )
        except TransportCancelled:
            raise
        except Exception as exc:
            failures.append(f"full source + local trim: {str(exc)[:260]}")
            out = self.config.download_dir / f"{stem}__localtrim.mp4"
            out.unlink(missing_ok=True)
            raise MediaTransportError(self._final_error("Timestamp download failed after all routes", failures))
        finally:
            if full is not None:
                try:
                    full.path.unlink(missing_ok=True)
                except OSError:
                    pass
                self._cleanup_prefix(full_stem)

    # ------------------------------------------------------------------
    # Subtitles/captions use the same route manifest and fresh extraction.
    # ------------------------------------------------------------------
    async def download_subtitles(
        self,
        url: str,
        stem: str,
        language: str,
        *,
        cancel_event: Any,
    ) -> Path:
        failures: list[str] = []
        cookie = self._cookie_file()
        for route in self.routes():
            self._check_cancel(cancel_event)
            if route.require_bgutil:
                status = await asyncio.to_thread(
                    check_compatibility,
                    self.config.bgutil_base_url,
                    timeout=2.0,
                    cache_seconds=10.0,
                )
                if not status.ok:
                    failures.append(f"{route.label}: {status.detail}")
                    continue
            prefix = f"{stem}__{route.name}_{uuid.uuid4().hex[:6]}"
            outtmpl = str(self.config.download_dir / f"{prefix}.%(ext)s")
            command = build_subtitle_command(self.config, route, url, outtmpl, language, cookie)
            started = time.monotonic()
            try:
                await self._run_capture(command, self.config.extract_timeout_seconds * 2, cancel_event)
                candidates = sorted(self.config.download_dir.glob(f"{prefix}*.vtt"))
                if not candidates:
                    raise MediaTransportError("No VTT subtitle file was produced", kind=ErrorKind.FORMAT, route=route.name)
                path = max(candidates, key=lambda p: p.stat().st_size)
                self.health.mark_success(route.name, time.monotonic() - started, path.stat().st_size)
                self._proxy_ok(route, time.monotonic() - started, path.stat().st_size)
                return path.resolve()
            except TransportCancelled:
                self._cleanup_prefix(prefix)
                raise
            except Exception as exc:
                kind = exc.kind if isinstance(exc, MediaTransportError) else classify_error(str(exc))
                failures.append(f"{route.label}: {str(exc)[:220]}")
                self.health.mark_failure(route.name, kind.value, str(exc))
                self._proxy_fail(route)
                self._cleanup_prefix(prefix)
        raise MediaTransportError(self._final_error("YouTube captions unavailable", failures))

    # ------------------------------------------------------------------
    # yt-dlp route execution with max-two hedging
    # ------------------------------------------------------------------
    async def _download_ytdlp_routes(
        self,
        plan: DownloadPlan,
        progress_cb: Optional[ProgressCallback],
        cancel_event: Any,
        failures: list[str],
    ) -> TransportResult:
        routes = self.routes()
        if not routes:
            raise MediaTransportError("No YouTube transport routes are configured")

        active: list[_RunningAttempt] = []
        next_index = 0
        attempt_counter = 0

        async def launch(route: RouteSpec) -> Optional[_RunningAttempt]:
            nonlocal attempt_counter
            self._check_cancel(cancel_event)
            if route.require_bgutil:
                status = await asyncio.to_thread(
                    check_compatibility,
                    self.config.bgutil_base_url,
                    timeout=2.0,
                    cache_seconds=10.0,
                )
                if not status.ok:
                    failures.append(f"{route.label}: {status.detail}")
                    self.health.mark_failure(route.name, ErrorKind.PROVIDER_UNAVAILABLE.value, status.detail)
                    return None
            attempt_counter += 1
            await self._emit(progress_cb, f"YouTube transport: {route.label}...")
            try:
                return await self._start_attempt(plan, route, attempt_counter, progress_cb, cancel_event)
            except Exception as exc:
                kind = exc.kind if isinstance(exc, MediaTransportError) else classify_error(str(exc))
                failures.append(f"{route.label}: {str(exc)[:260]}")
                self.health.mark_failure(route.name, kind.value, str(exc))
                self._proxy_fail(route)
                return None

        try:
            while active or next_index < len(routes):
                self._check_cancel(cancel_event)

                if not active and next_index < len(routes):
                    handle = await launch(routes[next_index])
                    next_index += 1
                    if handle is not None:
                        active.append(handle)
                    continue

                # Consume completed attempts first. A validated success wins and
                # immediately cancels every loser/partial attempt.
                completed = [item for item in active if item.result_task.done()]
                if completed:
                    for item in list(completed):
                        active.remove(item)
                        try:
                            result = item.result_task.result()
                            elapsed = time.monotonic() - item.started
                            self.health.mark_success(item.route.name, elapsed, result.path.stat().st_size)
                            self._proxy_ok(item.route, elapsed, result.path.stat().st_size)
                            await self._cancel_many(active, cleanup=True)
                            active.clear()
                            await self._emit(progress_cb, f"YouTube route OK: {item.route.label}")
                            return result
                        except TransportCancelled:
                            raise
                        except Exception as exc:
                            kind = exc.kind if isinstance(exc, MediaTransportError) else classify_error(str(exc))
                            failures.append(f"{item.route.label}: {str(exc)[:260]}")
                            self.health.mark_failure(item.route.name, kind.value, str(exc))
                            self._proxy_fail(item.route)
                            await self._finalize_handle(item, cleanup=True)
                    continue

                # If a route has entered a known fatal state, hedge immediately.
                fatal = any(item.fatal_event.is_set() for item in active)
                transfer = any(item.transfer_event.is_set() for item in active)
                now = time.monotonic()
                oldest_age = max((now - item.started for item in active), default=0.0)

                if next_index < len(routes) and len(active) < 2:
                    if fatal or (not transfer and oldest_age >= self.config.hedge_delay_seconds):
                        handle = await launch(routes[next_index])
                        next_index += 1
                        if handle is not None:
                            active.append(handle)
                        continue

                # Never allow 3+ full downloads. If two attempts are both stuck
                # before valid transfer, retire the oldest before advancing.
                if (
                    next_index < len(routes)
                    and len(active) >= 2
                    and not transfer
                    and oldest_age >= self.config.hedge_delay_seconds * 2.0
                ):
                    oldest = min(active, key=lambda item: item.started)
                    active.remove(oldest)
                    failures.append(f"{oldest.route.label}: no valid transfer during hedge window")
                    await self._cancel_handle(oldest, cleanup=True)
                    handle = await launch(routes[next_index])
                    next_index += 1
                    if handle is not None:
                        active.append(handle)
                    continue

                await asyncio.sleep(0.15)

            raise MediaTransportError(self._final_error("All yt-dlp transport routes failed", failures))
        finally:
            if active:
                await self._cancel_many(active, cleanup=True)

    async def _start_attempt(
        self,
        plan: DownloadPlan,
        route: RouteSpec,
        attempt_number: int,
        progress_cb: Optional[ProgressCallback],
        cancel_event: Any,
    ) -> _RunningAttempt:
        cookie = self._cookie_file()
        route_token = re.sub(r"[^A-Za-z0-9_.-]+", "_", route.name)
        prefix = f"{plan.stem}__{attempt_number:02d}_{route_token}_{uuid.uuid4().hex[:5]}"
        outtmpl = str(self.config.download_dir / f"{prefix}.%(ext)s")

        candidates = list(plan.formats) or [FormatCandidate("bestvideo+bestaudio/best", "mp4", True)]
        # One subprocess can only try one selector. Use yt-dlp's slash syntax to
        # keep format fallback inside the same fresh extraction/route attempt.
        selector = "/".join(item.selector for item in candidates if item.selector)
        container = candidates[0].container if candidates else "mp4"
        if plan.mode == "video":
            command = build_download_command(
                self.config, route, plan.url, outtmpl, selector, container, cookie
            )
        elif plan.mode == "timestamp":
            command = build_download_command(
                self.config,
                route,
                plan.url,
                outtmpl,
                selector,
                "mp4",
                cookie,
                start=plan.start,
                end=plan.end,
            )
            container = "mp4"
        elif plan.mode == "audio":
            command = build_audio_command(
                self.config,
                route,
                plan.url,
                outtmpl,
                cookie,
                audio_quality=plan.audio_quality,
            )
            selector = "bestaudio/best"
            container = "mp3"
        else:
            raise MediaTransportError(f"Unsupported transport mode: {plan.mode}")

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        transfer_event = asyncio.Event()
        fatal_event = asyncio.Event()
        lines: deque[str] = deque(maxlen=160)
        started = time.monotonic()
        holder: dict[str, Any] = {"last_progress": 0.0}

        async def reader() -> None:
            if process.stdout is None:
                return
            while True:
                raw = await process.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip()
                lines.append(line)
                lower = line.lower()
                if ("error:" in lower or "http error 403" in lower) and is_fast_fallback_error(classify_error(line)):
                    fatal_event.set()
                if progress_cb and "[download]" in line and "%" in line:
                    now = time.monotonic()
                    if now - float(holder["last_progress"]) >= 2.0:
                        holder["last_progress"] = now
                        short = re.sub(r"\s+", " ", line).strip()
                        await self._emit(progress_cb, f"{route.label}: {short[-180:]}")

        async def monitor() -> None:
            while process.returncode is None:
                if self._cancelled(cancel_event):
                    return
                if self._prefix_size(prefix) >= self.config.hedge_transfer_bytes:
                    transfer_event.set()
                    return
                await asyncio.sleep(0.20)

        reader_task = asyncio.create_task(reader())
        monitor_task = asyncio.create_task(monitor())

        async def wait_result() -> TransportResult:
            try:
                await asyncio.wait_for(process.wait(), timeout=self.config.route_timeout_seconds)
            except asyncio.TimeoutError as exc:
                await self._terminate_process(process)
                raise MediaTransportError(
                    f"{route.label} timed out after {self.config.route_timeout_seconds:.0f}s",
                    kind=ErrorKind.NETWORK,
                    route=route.name,
                ) from exc
            finally:
                try:
                    await asyncio.wait_for(reader_task, timeout=2.0)
                except Exception:
                    reader_task.cancel()
                monitor_task.cancel()

            self._check_cancel(cancel_event)
            text = "\n".join(lines)
            if process.returncode != 0:
                kind = classify_error(text)
                tail = self._useful_error(text)
                raise MediaTransportError(
                    tail or f"yt-dlp exited with code {process.returncode}",
                    kind=kind,
                    route=route.name,
                )

            path = self._find_output(prefix, plan.mode)
            if path is None:
                raise MediaTransportError(
                    "yt-dlp completed but no final media file was produced",
                    kind=ErrorKind.MEDIA_INVALID,
                    route=route.name,
                )
            expected_duration = None
            strict_range = False
            if plan.mode == "timestamp" and plan.start is not None and plan.end is not None:
                expected_duration = float(plan.end) - float(plan.start)
                strict_range = True
            probe = await self._validate_media(
                path,
                require_video=plan.require_video,
                require_audio=plan.require_audio,
                target_height=plan.target_height,
                expected_duration=expected_duration,
                strict_range=strict_range,
            )

            # Determine which candidate yt-dlp likely used from resulting height.
            selector_index = 0
            if plan.target_height and candidates:
                for idx, candidate in enumerate(candidates):
                    if candidate.selector and str(plan.target_height) in candidate.selector:
                        selector_index = idx
                        break
            return TransportResult(
                path=path.resolve(),
                route=route,
                probe=probe,
                selector_index=selector_index,
                selector=selector,
                output_container=container,
            )

        result_task = asyncio.create_task(wait_result())
        return _RunningAttempt(
            route=route,
            prefix=prefix,
            command=command,
            process=process,
            started=started,
            transfer_event=transfer_event,
            fatal_event=fatal_event,
            lines=lines,
            reader_task=reader_task,
            monitor_task=monitor_task,
            result_task=result_task,
        )

    # ------------------------------------------------------------------
    # Media validation
    # ------------------------------------------------------------------
    async def _validate_media(
        self,
        path: Path,
        *,
        require_video: bool,
        require_audio: bool,
        target_height: Optional[int] = None,
        expected_duration: Optional[float] = None,
        strict_range: bool = False,
    ) -> MediaProbe:
        path = Path(path)
        if not path.is_file() or path.stat().st_size < 16 * 1024:
            raise MediaTransportError(
                f"Downloaded file is missing or too small: {path.name}",
                kind=ErrorKind.MEDIA_INVALID,
            )
        probe = await asyncio.to_thread(self._probe_media_sync, path)
        if require_video and probe.video_streams < 1:
            raise MediaTransportError("Downloaded output has no video stream", kind=ErrorKind.MEDIA_INVALID)
        if require_audio and probe.audio_streams < 1:
            raise MediaTransportError("Downloaded output has no audio stream", kind=ErrorKind.MEDIA_INVALID)
        if probe.duration <= 0:
            raise MediaTransportError("Downloaded output has no valid duration", kind=ErrorKind.MEDIA_INVALID)
        if target_height and require_video and probe.height > int(target_height) + 8:
            raise MediaTransportError(
                f"Output height {probe.height} exceeds requested ceiling {target_height}",
                kind=ErrorKind.MEDIA_INVALID,
            )
        if expected_duration and strict_range:
            if probe.duration > expected_duration + 8.0:
                raise MediaTransportError(
                    f"Timestamp output is too long ({probe.duration:.2f}s for {expected_duration:.2f}s request)",
                    kind=ErrorKind.MEDIA_INVALID,
                )
            min_ok = max(0.25, expected_duration - min(5.0, expected_duration * 0.20))
            if probe.duration < min_ok:
                raise MediaTransportError(
                    f"Timestamp output is too short ({probe.duration:.2f}s for {expected_duration:.2f}s request)",
                    kind=ErrorKind.MEDIA_INVALID,
                )

        await asyncio.to_thread(
            self._decode_verify_sync,
            path,
            probe,
            require_video,
            require_audio,
            bool(strict_range),
        )
        return probe

    def _probe_media_sync(self, path: Path) -> MediaProbe:
        cmd = [
            self.config.ffprobe_bin,
            "-v", "error",
            "-print_format", "json",
            "-show_streams",
            "-show_format",
            str(path),
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if completed.returncode != 0:
            raise MediaTransportError(
                completed.stderr[-600:] or "ffprobe failed",
                kind=ErrorKind.MEDIA_INVALID,
            )
        try:
            raw = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise MediaTransportError("ffprobe returned invalid JSON", kind=ErrorKind.MEDIA_INVALID) from exc
        streams = raw.get("streams") or []
        videos = [s for s in streams if s.get("codec_type") == "video"]
        audios = [s for s in streams if s.get("codec_type") == "audio"]
        video = videos[0] if videos else {}
        duration = 0.0
        for value in (
            (raw.get("format") or {}).get("duration"),
            video.get("duration"),
            (audios[0] if audios else {}).get("duration"),
        ):
            try:
                if value is not None and float(value) > 0:
                    duration = float(value)
                    break
            except (TypeError, ValueError):
                pass
        return MediaProbe(
            path=path,
            duration=duration,
            width=int(video.get("width") or 0),
            height=int(video.get("height") or 0),
            video_streams=len(videos),
            audio_streams=len(audios),
            format_name=str((raw.get("format") or {}).get("format_name") or ""),
            raw=raw,
        )

    def _decode_verify_sync(
        self,
        path: Path,
        probe: MediaProbe,
        require_video: bool,
        require_audio: bool,
        strict_range: bool,
    ) -> None:
        maps: list[str] = []
        if require_video:
            maps += ["-map", "0:v:0"]
        if require_audio:
            maps += ["-map", "0:a:0"]

        # Range clips are normally short; fully decode them up to the configured
        # limit. Long full videos get head+tail decode probes to avoid doubling
        # multi-hour job runtime while still rejecting corrupt media.
        if strict_range and probe.duration <= self.config.verify_full_range_max_seconds:
            cmd = [self.config.ffmpeg_bin, "-v", "error", "-xerror", "-i", str(path), *maps, "-f", "null", "-"]
            completed = subprocess.run(cmd, capture_output=True, text=True, timeout=max(60, int(probe.duration * 4 + 30)))
            if completed.returncode != 0:
                raise MediaTransportError(
                    completed.stderr[-800:] or "Full range decode validation failed",
                    kind=ErrorKind.MEDIA_INVALID,
                )
            return

        sample = max(1.0, min(self.config.verify_sample_seconds, probe.duration))
        positions = [0.0]
        if probe.duration > sample * 3:
            positions.append(max(0.0, probe.duration - sample - 0.5))
        for position in positions:
            cmd = [self.config.ffmpeg_bin, "-v", "error", "-xerror"]
            if position > 0:
                cmd += ["-ss", f"{position:.3f}"]
            cmd += ["-i", str(path), "-t", f"{sample:.3f}", *maps, "-f", "null", "-"]
            completed = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            if completed.returncode != 0:
                raise MediaTransportError(
                    completed.stderr[-800:] or "Decode validation failed",
                    kind=ErrorKind.MEDIA_INVALID,
                )

    # ------------------------------------------------------------------
    # Process helpers
    # ------------------------------------------------------------------
    async def _run_capture(self, command: list[str], timeout: float, cancel_event: Any) -> str:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        task = asyncio.create_task(process.communicate())
        started = time.monotonic()
        try:
            while not task.done():
                self._check_cancel(cancel_event)
                if time.monotonic() - started > timeout:
                    await self._terminate_process(process)
                    raise MediaTransportError("yt-dlp request timed out", kind=ErrorKind.NETWORK)
                await asyncio.sleep(0.15)
            output, _ = await task
            text = (output or b"").decode("utf-8", errors="replace")
            if process.returncode != 0:
                raise MediaTransportError(
                    self._useful_error(text) or f"yt-dlp exited with code {process.returncode}",
                    kind=classify_error(text),
                )
            return text
        except TransportCancelled:
            await self._terminate_process(process)
            raise
        finally:
            if not task.done():
                task.cancel()

    async def _run_ffmpeg(self, command: list[str], timeout: float, cancel_event: Any) -> None:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        task = asyncio.create_task(process.communicate())
        started = time.monotonic()
        try:
            while not task.done():
                self._check_cancel(cancel_event)
                if time.monotonic() - started > timeout:
                    await self._terminate_process(process)
                    raise MediaTransportError("ffmpeg final fallback timed out", kind=ErrorKind.MEDIA_INVALID)
                await asyncio.sleep(0.2)
            stdout, stderr = await task
            if process.returncode != 0:
                raise MediaTransportError(
                    (stderr or b"").decode("utf-8", errors="replace")[-1000:] or "ffmpeg failed",
                    kind=ErrorKind.MEDIA_INVALID,
                )
        except TransportCancelled:
            await self._terminate_process(process)
            raise
        finally:
            if not task.done():
                task.cancel()

    async def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            if process.pid:
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except (ProcessLookupError, PermissionError):
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=1.5)
            return
        except asyncio.TimeoutError:
            pass
        try:
            if process.pid:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (ProcessLookupError, PermissionError):
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass

    async def _cancel_handle(self, item: _RunningAttempt, *, cleanup: bool) -> None:
        if not item.result_task.done():
            await self._terminate_process(item.process)
            item.result_task.cancel()
        item.reader_task.cancel()
        item.monitor_task.cancel()
        if cleanup:
            self._cleanup_prefix(item.prefix)

    async def _finalize_handle(self, item: _RunningAttempt, *, cleanup: bool) -> None:
        item.reader_task.cancel()
        item.monitor_task.cancel()
        if cleanup:
            self._cleanup_prefix(item.prefix)

    async def _cancel_many(self, items: Sequence[_RunningAttempt], *, cleanup: bool) -> None:
        for item in list(items):
            try:
                await self._cancel_handle(item, cleanup=cleanup)
            except Exception:
                logger.exception("Could not cancel loser route %s", item.route.name)

    # ------------------------------------------------------------------
    # Files, errors, callbacks
    # ------------------------------------------------------------------
    def _find_output(self, prefix: str, mode: str) -> Optional[Path]:
        ignored = {".part", ".ytdl", ".tmp", ".temp", ".json", ".description", ".vtt"}
        candidates = []
        for path in self.config.download_dir.glob(f"{prefix}*"):
            if not path.is_file():
                continue
            if path.suffix.lower() in ignored or path.name.endswith(".info.json"):
                continue
            if ".f" in path.stem and mode in {"video", "timestamp"}:
                # Usually an intermediate adaptive stream. Prefer merged output.
                continue
            candidates.append(path)
        if not candidates:
            # If yt-dlp left only an adaptive component for some edge case, let
            # validation decide rather than pretending the route produced nothing.
            candidates = [
                path for path in self.config.download_dir.glob(f"{prefix}*")
                if path.is_file() and path.suffix.lower() not in ignored
            ]
        return max(candidates, key=lambda p: p.stat().st_size) if candidates else None

    def _prefix_size(self, prefix: str) -> int:
        total = 0
        try:
            for path in self.config.download_dir.glob(f"{prefix}*"):
                if path.is_file():
                    total += int(path.stat().st_size)
        except OSError:
            pass
        return total

    def _cleanup_prefix(self, prefix: str) -> None:
        for path in self.config.download_dir.glob(f"{prefix}*"):
            try:
                if path.is_file():
                    path.unlink(missing_ok=True)
            except OSError:
                pass

    def _parse_json_output(self, text: str) -> dict[str, Any]:
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        for line in reversed(lines):
            if not line.startswith("{"):
                continue
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue
        raise MediaTransportError(
            self._useful_error(text) or "yt-dlp returned no metadata JSON",
            kind=classify_error(text),
        )

    def _useful_error(self, text: str) -> str:
        lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines() if line.strip()]
        for line in reversed(lines):
            if "ERROR:" in line or "error:" in line.lower():
                return line[-900:]
        return (lines[-1][-900:] if lines else "")

    def _final_error(self, title: str, failures: Sequence[str]) -> str:
        if not failures:
            return title
        # Keep the user-facing error concise; detailed route health persists in JSON/logs.
        tail = list(failures)[-3:]
        return title + ". Last routes: " + " | ".join(item[:260] for item in tail)

    async def _emit(self, cb: Optional[ProgressCallback], message: str) -> None:
        if cb is None:
            return
        try:
            await cb(message)
        except Exception:
            logger.debug("Progress callback failed", exc_info=True)

    def _cancelled(self, cancel_event: Any) -> bool:
        return bool(cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)())

    def _check_cancel(self, cancel_event: Any) -> None:
        if self._cancelled(cancel_event):
            raise TransportCancelled()

    def _proxy_ok(self, route: RouteSpec, elapsed: float, size: int) -> None:
        if not route.proxy or self.mark_proxy_ok is None:
            return
        try:
            self.mark_proxy_ok(route.proxy, elapsed_seconds=elapsed, bytes_transferred=size)
        except TypeError:
            try:
                self.mark_proxy_ok(route.proxy)
            except Exception:
                pass
        except Exception:
            pass

    def _proxy_fail(self, route: RouteSpec) -> None:
        if not route.proxy or self.mark_proxy_fail is None:
            return
        try:
            self.mark_proxy_fail(route.proxy)
        except Exception:
            pass
