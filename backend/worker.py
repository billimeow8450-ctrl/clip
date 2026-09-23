from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import uuid
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Optional

# Add WEBSITE_DEVELOPER_SOURCE to sys.path so we can import master_engine, media_transport, etc.
SOURCE_DIR = Path(__file__).resolve().parent.parent / "WEBSITE_DEVELOPER_SOURCE"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

from .database import get_db
from .utils.security import get_local_upload_id, sign_file_url
from .utils.storage import download_file as download_stored_file, upload_file as upload_stored_file, using_object_storage

logger = logging.getLogger("clip_studio.worker")

# Flag to avoid overheating laptop during local testing.
# When set to 1/true on production server, it executes full MasterEngine/FFmpeg/Whisper renders.
ENABLE_HEAVY_RENDERING = os.getenv("ENABLE_HEAVY_RENDERING", "0").strip().lower() in ("1", "true", "yes")

OUTPUT_DIR = Path(__file__).resolve().parent / "storage" / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR = Path(__file__).resolve().parent / "storage" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Bounded job concurrency (CODE_REVIEW.md finding M4): previously every job was
# a fire-and-forget asyncio.create_task with no cap and no strong reference.
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))
JOB_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
_BG_TASKS: set = set()

FALLBACK_THUMBNAIL = (
    "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=600&auto=format&fit=crop&q=80"
)
SIMULATION_NOTICE = (
    "Simulation mode: these results are placeholders, not derived from your media. "
    "Set ENABLE_HEAVY_RENDERING=1 on the server for real processing."
)


def spawn_job(coro) -> None:
    """Launch a background job with a strong reference and the global concurrency cap."""

    async def _run() -> None:
        async with JOB_SEMAPHORE:
            await coro

    task = asyncio.create_task(_run())
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


async def _download_youtube_with_broker(
    source_url: str, work_dir: Path, job_id: str, *, target_height: int = 480
) -> Path:
    """Use the Telegram bot's multi-route YouTube transport for web jobs.

    A single yt-dlp request is brittle from cloud IP addresses. The established
    bot transport retries independent public clients, configured proxy/cookie
    routes, and pytubefix before declaring a source unavailable.
    """
    from media_transport import BrokerConfig, FormatCandidate, MediaTransportBroker

    proxy_values = os.getenv("YOUTUBE_PROXY_URLS", "")
    proxies = [value.strip() for value in proxy_values.replace("\n", ",").split(",") if value.strip()]
    cookie_path = Path(os.getenv("YOUTUBE_COOKIES_PATH", "/etc/secrets/youtube_cookies.txt"))

    broker = MediaTransportBroker(
        BrokerConfig(
            download_dir=work_dir,
            health_file=work_dir / "route-health.json",
            force_ipv4=True,
            concurrent_fragments=max(1, int(os.getenv("YTDLP_CONCURRENT_FRAGMENTS", "4"))),
            http_chunk_size=max(0, int(os.getenv("YTDLP_HTTP_CHUNK_SIZE", str(5 * 1024 * 1024)))),
            route_timeout_seconds=float(os.getenv("YOUTUBE_ROUTE_TIMEOUT_SECONDS", "900")),
            pytubefix_enabled=True,
        ),
        cookie_getter=lambda: cookie_path if cookie_path.is_file() else None,
        proxy_getter=lambda: proxies,
    )
    result = await broker.download_video(
        source_url,
        stem=f"source_{job_id}",
        formats=(FormatCandidate(
            f"bestvideo[height<={target_height}]+bestaudio/best[height<={target_height}]/best",
            "mp4",
            True,
        ),),
        target_height=target_height,
        progress_cb=None,
        cancel_event=None,
    )
    return result.path.resolve()


async def update_job_status(
    job_id: str,
    status: str,
    progress: float,
    stage: str,
    result_data: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
) -> None:
    async with await get_db() as db:
        await db.execute(
            """
            UPDATE jobs
            SET status = ?, progress = ?, stage = ?, result_data = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                status,
                progress,
                stage,
                json.dumps(result_data) if result_data else None,
                error_message,
                job_id,
            ),
        )
        await db.commit()


async def claim_job(job_id: str) -> bool:
    """Atomically claim a queued job across all web processes.

    The database is the source of truth; duplicate startup recovery calls and
    concurrent HTTP workers cannot both transition the same row to processing.
    """
    async with await get_db() as db:
        cursor = await db.execute(
            """
            UPDATE jobs
            SET status = 'processing', progress = 1.0, stage = 'Preparing worker', updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'queued'
            RETURNING id
            """,
            (job_id,),
        )
        row = await cursor.fetchone()
        await db.commit()
        return row is not None


async def _resolve_input_file(
    source_url: Optional[str], job_id: str, *, youtube_height: int = 480
) -> Optional[Path]:
    """Materialize an owned upload into an isolated short-lived work path."""
    upload_id = get_local_upload_id(source_url or "")
    if not upload_id:
        if not source_url:
            return None
        # External URLs have already passed the platform allowlist and DNS
        # checks in the API layer. yt-dlp is called through its Python API, not
        # a shell, and downloads into an isolated per-job workspace.
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("External source ingestion is unavailable") from exc
        work_dir = UPLOAD_DIR / ".work" / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        template = str(work_dir / "source.%(ext)s")

        def _download() -> Path:
            options = {
                "outtmpl": template,
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "max_filesize": int(os.getenv("MAX_EXTERNAL_SOURCE_MB", "1024")) * 1024 * 1024,
            }
            is_youtube = any(host in source_url.lower() for host in ("youtube.com", "youtu.be"))
            if is_youtube:
                # YouTube increasingly challenges high-quality web-client
                # streams from datacenter IPs. Its Android client exposes a
                # combined H.264/AAC MP4 (itag 18) for this class of public
                # video without requiring a user's browser cookies. Prefer it
                # for reliable server-side ingestion, then fall back to other
                # <=480p MP4 streams if a particular video lacks it.
                options.update({
                    "format": "18/best[height<=480][ext=mp4]/best[height<=480]",
                    "extractor_args": {"youtube": {"player_client": ["android"]}},
                })
            else:
                options["format"] = "bestvideo[height<=1080]+bestaudio/best[height<=1080]"
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(source_url, download=True)
                filename = Path(ydl.prepare_filename(info))
                if filename.exists():
                    return filename
                candidates = [p for p in work_dir.iterdir() if p.is_file()]
                if not candidates:
                    raise RuntimeError("No media file was downloaded")
                return max(candidates, key=lambda p: p.stat().st_size)

        if any(host in source_url.lower() for host in ("youtube.com", "youtu.be")):
            return await _download_youtube_with_broker(
                source_url, work_dir, job_id, target_height=youtube_height
            )
        return await asyncio.to_thread(_download)
    if using_object_storage():
        work_dir = UPLOAD_DIR / ".work"
        work_dir.mkdir(parents=True, exist_ok=True)
        candidate = work_dir / f"{job_id}_{upload_id}"
        try:
            await download_stored_file(f"uploads/{upload_id}", candidate)
            return candidate
        except Exception:
            candidate.unlink(missing_ok=True)
            raise RuntimeError("The uploaded source could not be retrieved from durable storage")
    candidate = (UPLOAD_DIR / upload_id).resolve()
    if candidate.parent == UPLOAD_DIR.resolve() and candidate.is_file():
        return candidate
    return None


async def _persist_output(path: Path, filename: str) -> None:
    """Move generated artifacts to durable storage before publishing job results."""
    if using_object_storage():
        content_type = "video/mp4" if filename.endswith(".mp4") else "application/octet-stream"
        await upload_stored_file(f"outputs/{filename}", path, content_type)
        path.unlink(missing_ok=True)


def _source_duration_seconds(source: Path) -> float:
    """Read a media duration with ffprobe without invoking a shell."""
    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    duration = float(completed.stdout.strip())
    if duration <= 0:
        raise RuntimeError("The uploaded video has no usable duration")
    return duration


def _render_vertical_clip(
    source: Path, destination: Path, start: float, duration: float, *, output_width: int = 1080
) -> None:
    """Render a genuine centre-cropped 9:16 H.264/AAC clip using ffmpeg."""
    if output_width not in (720, 1080):
        raise ValueError("Unsupported output width")
    output_height = round(output_width * 16 / 9)
    destination.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
            "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
            "-vf", f"scale={output_width}:{output_height}:force_original_aspect_ratio=increase,crop={output_width}:{output_height}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=max(120, int(duration * 12)),
    )
    if completed.returncode != 0 or not destination.is_file() or destination.stat().st_size < 4096:
        destination.unlink(missing_ok=True)
        detail = completed.stderr.strip()[-500:] or "ffmpeg did not produce an output file"
        raise RuntimeError(f"Clip rendering failed: {detail}")


def _cleanup_workfile(path: Optional[Path]) -> None:
    if not path:
        return
    work_root = (UPLOAD_DIR / ".work").resolve()
    try:
        if path.resolve().is_relative_to(work_root):
            if path.is_file():
                path.unlink(missing_ok=True)
            if path.parent != work_root:
                shutil.rmtree(path.parent, ignore_errors=True)
    except (OSError, ValueError):
        return


def _parse_target_len(target_duration: Optional[str]) -> float:
    """Robustly parse the requested clip length; always returns a positive float."""
    try:
        value = float(str(target_duration or "60").strip())
    except (TypeError, ValueError):
        return 60.0
    if value <= 0:
        return 60.0
    return value


def _resolve_target_len(target_duration: Optional[str], source_duration: float) -> float:
    """Choose a bounded duration for the bot-compatible Auto Best Length mode."""
    if str(target_duration or "").strip().lower() != "auto":
        return _parse_target_len(target_duration)
    # Short sources work better as a concise hook, while longer conversations
    # have enough context to justify a 60s delivery clip.
    if source_duration <= 180:
        return min(30.0, source_duration)
    if source_duration <= 600:
        return 45.0
    return 60.0


def _clipper_error_message(exc: Exception, source_url: Optional[str]) -> str:
    """Return a safe, actionable user-facing failure for source ingestion.

    The full provider exception remains in server logs for diagnosis. Exposing it
    to the client would be both confusing and potentially reveal implementation
    details, while a generic error made the landing-page button appear broken.
    """
    source = (source_url or "").lower()
    detail = str(exc).lower()
    is_external = source.startswith(("http://", "https://"))
    if is_external and ("sign in to confirm" in detail or "bot" in detail or "youtube" in source):
        return (
            "This YouTube video cannot be downloaded automatically (YouTube blocked the server request). "
            "Please upload the original MP4, MOV, or MKV file to create clips."
        )
    if is_external:
        return (
            "We could not retrieve that public video. Make sure it is public and playable, "
            "or upload the original MP4, MOV, or MKV file instead."
        )
    return "We could not process this upload. Please try a valid MP4, MOV, or MKV video file."


async def recover_stuck_jobs() -> int:
    """Requeue interrupted work; every execution still requires an atomic claim."""
    recovered = 0
    async with await get_db() as db:
        cursor = await db.execute(
            "UPDATE jobs SET status = 'queued', progress = 0.0, stage = 'Recovered after restart', updated_at = CURRENT_TIMESTAMP WHERE status = 'processing'"
        )
        await db.commit()
        cursor = await db.execute("SELECT id, type, input_params, user_id FROM jobs WHERE status = 'queued'")
        rows = await cursor.fetchall()

    for row in rows:
        try:
            params = json.loads(row["input_params"] or "{}")
        except Exception:
            params = {}

        if row["type"] == "clipper":
            spawn_job(process_clipper_job(row["id"], params, row["user_id"]))
            recovered += 1
        elif row["type"] == "editor":
            spawn_job(process_editor_job(row["id"], params, row["user_id"]))
            recovered += 1
        elif row["type"] == "transcript":
            spawn_job(process_transcript_job(row["id"], params, row["user_id"]))
            recovered += 1
        else:
            await update_job_status(row["id"], "failed", 100.0, "Unknown job type")
            recovered += 1

    return recovered


async def cleanup_finished_jobs(max_age_hours: float = 24.0) -> int:
    """Delete generated output artifacts past the retention window to bound disk use."""
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for path in OUTPUT_DIR.iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


async def process_editor_job(job_id: str, params: Dict[str, Any], user_id: int) -> None:
    """Process an AI Video Editor job (File or YouTube Range + MasterEngine reframing)."""
    try:
        if not await claim_job(job_id):
            return
        await update_job_status(job_id, "processing", 10.0, "Preparing video source...")

        source_type = params.get("source_type")  # 'youtube' or 'file'
        source_url = params.get("source_url")
        start_seconds = float(params.get("start_seconds", 0) or 0)
        end_seconds = float(params.get("end_seconds", 60) or 60)
        caption_style = params.get("caption_style", "auto")
        layout_mode = params.get("layout_mode", "auto")

        duration = max(1.0, end_seconds - start_seconds)

        if ENABLE_HEAVY_RENDERING:
            await update_job_status(job_id, "processing", 25.0, "Initializing MasterEngine pipeline...")
            try:
                from master_engine.engine import MasterEngine
                from master_engine.config import Settings
                from master_engine.media import extract_range

                # The previous website integration sent the whole source to the
                # editor, silently ignoring the timeline.  The bot's range-first
                # editor is the correct model: materialize a bounded source range,
                # then run the production editor over that exact media.
                # Use the same resilient source tier as the proven auto-clipper
                # path.  Requesting a high-resolution YouTube stream first can
                # spend many minutes cycling bot-challenged routes on cloud IPs;
                # the production editor still produces its 1080x1920 delivery
                # master, but fails fast only when no playable source exists.
                input_file = await _resolve_input_file(source_url, job_id, youtube_height=480)
                output_file = OUTPUT_DIR / f"edited_{job_id}.mp4"

                if input_file and input_file.exists():
                    source_duration = await asyncio.to_thread(_source_duration_seconds, input_file)
                    if start_seconds >= source_duration:
                        raise RuntimeError("The selected start time is outside the source video")
                    bounded_end = min(end_seconds, source_duration)
                    if bounded_end - start_seconds < 1.0:
                        raise RuntimeError("The selected range is too short after matching the source video")
                    range_file = UPLOAD_DIR / ".work" / job_id / "editor_range.mp4"
                    await update_job_status(job_id, "processing", 35.0, "Extracting selected timeline range...")
                    await asyncio.to_thread(extract_range, input_file, range_file, start_seconds, bounded_end)
                    await update_job_status(job_id, "processing", 55.0, "Applying smart framing and word-synced captions...")
                    settings = Settings.from_env()
                    settings = replace(
                        settings,
                        width=1080,
                        height=1920,
                        captions_enabled=caption_style != "none",
                        # The bot's engine makes safe multi-speaker decisions
                        # automatically.  "focus" is retained as a conservative
                        # single-subject preference by disabling split layouts.
                        split_enabled=layout_mode != "focus",
                        caption_scope=f"web-user-{user_id}",
                    )
                    engine = MasterEngine(settings=settings)
                    engine_result = await asyncio.to_thread(engine.run_job, range_file, output_file)
                    await _persist_output(output_file, output_file.name)
                    _cleanup_workfile(input_file)
                    result = {
                        "output_video": sign_file_url(f"edited_{job_id}.mp4", user_id),
                        "duration": round(bounded_end - start_seconds, 2),
                        "start_seconds": round(start_seconds, 2),
                        "end_seconds": round(bounded_end, 2),
                        "caption_style": caption_style,
                        "layout": layout_mode,
                        "engine": "Master Editor",
                        "captionless_output_available": bool(engine_result.get("captionless_output")),
                        "is_simulation": False,
                    }
                else:
                    result = {
                        "output_video": None,
                        "duration": duration,
                        "caption_style": caption_style,
                        "layout": layout_mode,
                        "is_simulation": True,
                        "message": "Source video file was not found on the server; nothing was rendered.",
                    }
            except Exception as engine_err:
                logger.exception("MasterEngine execution failed")
                raise RuntimeError("The editor render failed") from engine_err
        else:
            # Laptop-Safe Simulation mode for UI/UX testing without cooking hardware
            await asyncio.sleep(1.0)
            await update_job_status(job_id, "processing", 35.0, "Analyzing audio & dialogue with Whisper...")
            await asyncio.sleep(1.0)
            await update_job_status(job_id, "processing", 60.0, "Detecting speakers & computing 9:16 reframing...")
            await asyncio.sleep(1.0)
            await update_job_status(job_id, "processing", 85.0, f"Rendering animated captions ({caption_style})...")
            await asyncio.sleep(0.8)

            result = {
                "output_video": None,
                "title": params.get("title", "Edited Clip"),
                "duration": duration,
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "caption_style": caption_style,
                "layout": layout_mode,
                "is_simulation": True,
                "message": SIMULATION_NOTICE,
            }

        await update_job_status(job_id, "completed", 100.0, "Completed", result_data=result)

    except Exception as exc:
        logger.exception("Editor job failed")
        await update_job_status(job_id, "failed", 100.0, "Failed", error_message="Processing failed. Please retry or contact support with the job ID.")


async def process_clipper_job(job_id: str, params: Dict[str, Any], user_id: int) -> None:
    """Process an AI Clipper job (find viral hooks, segment, and render vertical shorts)."""
    try:
        if not await claim_job(job_id):
            return
        await update_job_status(job_id, "processing", 10.0, "Ingesting video & research signals...")

        url = params.get("url")
        target_duration = str(params.get("target_duration", "60"))
        requested_clip_count = int(params.get("clip_count", 3))
        clip_count = requested_clip_count if requested_clip_count in (1, 3, 5) else 3
        output_width = int(params.get("output_quality", "1080"))
        if output_width not in (720, 1080):
            output_width = 1080
        analysis_mode = params.get("analysis_mode", "quick")

        clips: list = []
        input_file: Optional[Path] = None

        if ENABLE_HEAVY_RENDERING:
            if analysis_mode == "deep":
                await update_job_status(job_id, "processing", 30.0, "Analyzing public comment signals for viral moments...")
                try:
                    from viral_research_engine import collect_research
                    from .routers.youtube import extract_video_id

                    video_id = extract_video_id(url) if url else None
                    research_data: Dict[str, Any] = {}
                    if video_id:
                        research_data = await asyncio.to_thread(
                            collect_research, url, video_id, params.get("title", "Video")
                        )

                    comment_scores = research_data.get("comment_scores", {})
                    if comment_scores:
                        sorted_moments = sorted(comment_scores.items(), key=lambda x: x[1], reverse=True)[:clip_count]
                        for idx, (ts, score) in enumerate(sorted_moments):
                            st = max(0.0, float(ts) - 3.0)
                            clip_id = f"clip_{uuid.uuid4().hex[:8]}"
                            clips.append({
                                "id": clip_id,
                                "title": f"Viral Moment #{idx + 1} ({int(score)}% engagement)",
                                "start_time": st,
                                "viral_score": round(score, 1),
                                "hook_text": f"High engagement signal near {int(ts)}s from public comments analysis",
                                "thumbnail_url": params.get("thumbnail_url") or FALLBACK_THUMBNAIL,
                                "is_sample": False,
                            })
                except Exception as research_err:
                    logger.warning("Viral research engine warning: %s", research_err)
            else:
                await update_job_status(job_id, "processing", 30.0, "Preparing fast source-based clip candidates...")

            await update_job_status(job_id, "processing", 55.0, "Downloading and preparing source video...")
            input_file = await _resolve_input_file(url, job_id)
            if not input_file or not input_file.exists():
                raise RuntimeError("The source video could not be retrieved for clip rendering")
            source_duration = await asyncio.to_thread(_source_duration_seconds, input_file)
            target_len = _resolve_target_len(target_duration, source_duration)

            # Public comment data is not reliably available for every video. In that
            # case create real, evenly distributed excerpts and say so plainly rather
            # than fabricating retention scores or placeholder URLs.
            if not clips:
                max_start = max(0.0, source_duration - min(target_len, source_duration))
                if max_start == 0:
                    starts = [0.0]
                else:
                    starts = [round(max_start * index / max(clip_count - 1, 1), 1) for index in range(clip_count)]
                clips = [
                    {
                        "id": f"clip_{uuid.uuid4().hex[:8]}",
                        "title": f"Video Segment #{index + 1}",
                        "start_time": start,
                        "viral_score": None,
                        "hook_text": "Real source excerpt; audience metrics were unavailable for this video.",
                        "thumbnail_url": params.get("thumbnail_url") or FALLBACK_THUMBNAIL,
                        "is_sample": False,
                    }
                    for index, start in enumerate(dict.fromkeys(starts))
                ]

            await update_job_status(job_id, "processing", 75.0, "Rendering vertical 9:16 clips...")
            rendered_clips = []
            for index, clip in enumerate(clips, start=1):
                start = min(max(0.0, float(clip["start_time"])), max(0.0, source_duration - 0.1))
                duration = min(target_len, source_duration - start)
                if duration < 0.5:
                    continue
                filename = f"clip_{job_id}_{index}.mp4"
                output_file = OUTPUT_DIR / filename
                await asyncio.to_thread(
                    _render_vertical_clip, input_file, output_file, start, duration, output_width=output_width
                )
                await _persist_output(output_file, filename)
                clip.update({
                    "start_time": round(start, 1),
                    "end_time": round(start + duration, 1),
                    "duration": round(duration, 1),
                    "video_url": sign_file_url(filename, user_id),
                    "output_quality": f"{output_width}p",
                    "output_resolution": f"{output_width}×{round(output_width * 16 / 9)}",
                })
                rendered_clips.append(clip)
            clips = rendered_clips
            if not clips:
                raise RuntimeError("No playable source segments could be rendered")
            _cleanup_workfile(input_file)
        else:
            # Preview mode is deliberately explicit; it must never pretend to have
            # created media or completed an analysis of a user's source.
            await asyncio.sleep(0.3)
            clips = []

        # Save clips in DB
        async with await get_db() as db:
            for c in clips:
                await db.execute(
                    """
                    INSERT INTO clips (id, job_id, user_id, title, start_time, end_time, duration, viral_score, hook_text, video_url, thumbnail_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        c["id"],
                        job_id,
                        user_id,
                        c["title"],
                        c["start_time"],
                        c["end_time"],
                        c["duration"],
                        c["viral_score"],
                        c["hook_text"],
                        c["video_url"],
                        c.get("thumbnail_url"),
                    ),
                )
            await db.commit()

        result = {
            "clips_count": len(clips),
            "clips": clips,
            "analysis_mode": analysis_mode,
            "target_duration": "Auto best length" if target_duration == "auto" else f"{target_len:.0f}s",
            "output_quality": f"{output_width}p",
            "is_simulation": not ENABLE_HEAVY_RENDERING,
            "simulation_notice": SIMULATION_NOTICE if not ENABLE_HEAVY_RENDERING else None,
        }
        await update_job_status(job_id, "completed", 100.0, "Completed", result_data=result)

    except Exception as exc:
        _cleanup_workfile(input_file if 'input_file' in locals() else None)
        logger.exception("Clipper job failed")
        await update_job_status(
            job_id,
            "failed",
            100.0,
            "Failed",
            error_message=_clipper_error_message(exc, params.get("url")),
        )


async def process_transcript_job(job_id: str, params: Dict[str, Any], user_id: int) -> None:
    """Process an AI Whisper transcription job."""
    try:
        if not await claim_job(job_id):
            return
        await update_job_status(job_id, "processing", 15.0, "Extracting audio track...")

        language = params.get("language", "en")
        url_or_file = params.get("url_or_file", "")

        transcript_segments = None
        full_text = ""

        if ENABLE_HEAVY_RENDERING:
            try:
                from master_engine.transcribe import transcribe

                input_file = await _resolve_input_file(url_or_file, job_id)

                if input_file and input_file.exists():
                    await update_job_status(job_id, "processing", 40.0, "Running Whisper transcription...")
                    res = await asyncio.to_thread(transcribe, input_file)
                    _cleanup_workfile(input_file)
                    if isinstance(res, dict):
                        transcript_segments = res.get("segments")
                        full_text = res.get("full_text", "")
            except Exception as t_err:
                logger.exception("Whisper transcription failed")
                raise RuntimeError("The transcript render failed") from t_err

        if not transcript_segments and not ENABLE_HEAVY_RENDERING:
            await asyncio.sleep(1.0)
            await update_job_status(job_id, "processing", 45.0, "Running faster-whisper acoustic model...")
            await asyncio.sleep(1.0)
            await update_job_status(job_id, "processing", 80.0, "Aligning word-level timestamps & formatting...")
            await asyncio.sleep(0.8)

            sim_text = "SIMULATION — real transcription requires ENABLE_HEAVY_RENDERING=1."
            transcript_segments = [
                {"start": "00:00:01", "end": "00:00:05", "speaker": "Speaker 1", "text": sim_text},
                {"start": "00:00:05", "end": "00:00:12", "speaker": "Speaker 1", "text": sim_text},
                {"start": "00:00:12", "end": "00:00:18", "speaker": "Speaker 2", "text": sim_text},
                {"start": "00:00:18", "end": "00:00:26", "speaker": "Speaker 1", "text": sim_text},
                {"start": "00:00:26", "end": "00:00:34", "speaker": "Speaker 2", "text": sim_text},
                {"start": "00:00:34", "end": "00:00:45", "speaker": "Speaker 1", "text": sim_text},
            ]
            full_text = " ".join([s["text"] for s in transcript_segments])

        if transcript_segments is None:
            transcript_segments = []

        # Write actual export files to storage/outputs (these DO exist and are
        # served through signed URLs, unlike the old fake sample paths).
        txt_path = OUTPUT_DIR / f"transcript_{job_id}.txt"
        srt_path = OUTPUT_DIR / f"transcript_{job_id}.srt"
        txt_path.write_text(full_text, encoding="utf-8")
        srt_content = []
        for i, s in enumerate(transcript_segments, 1):
            srt_content.append(
                f"{i}\n{s.get('start', '00:00:00')} --> {s.get('end', '00:00:05')}\n{s.get('text', '')}\n"
            )
        srt_path.write_text("\n".join(srt_content), encoding="utf-8")
        await _persist_output(txt_path, txt_path.name)
        await _persist_output(srt_path, srt_path.name)

        result = {
            "language": language,
            "segments": transcript_segments,
            "full_text": full_text,
            "export_files": {
                "txt": sign_file_url(f"transcript_{job_id}.txt", user_id),
                "srt": sign_file_url(f"transcript_{job_id}.srt", user_id),
            },
            "is_simulation": not ENABLE_HEAVY_RENDERING,
            "simulation_notice": SIMULATION_NOTICE if not ENABLE_HEAVY_RENDERING else None,
        }

        await update_job_status(job_id, "completed", 100.0, "Completed", result_data=result)

    except Exception as exc:
        logger.exception("Transcript job failed")
        await update_job_status(job_id, "failed", 100.0, "Failed", error_message="Processing failed. Please retry or contact support with the job ID.")
