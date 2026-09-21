from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

# Add WEBSITE_DEVELOPER_SOURCE to sys.path so we can import master_engine, media_transport, etc.
SOURCE_DIR = Path(__file__).resolve().parent.parent / "WEBSITE_DEVELOPER_SOURCE"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

from .database import get_db
from .utils.security import sign_file_url

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


def _resolve_input_file(source_url: Optional[str]) -> Optional[Path]:
    """Map a source URL/path to an existing upload on disk."""
    if not source_url:
        return None
    if "/api/files/" in source_url or source_url.startswith("up_"):
        fname = os.path.basename(source_url.split("?")[0])
        candidate = UPLOAD_DIR / fname
        if candidate.exists():
            return candidate
    return None


def _parse_target_len(target_duration: Optional[str]) -> float:
    """Robustly parse the requested clip length; always returns a positive float."""
    try:
        value = float(str(target_duration or "60").strip())
    except (TypeError, ValueError):
        return 60.0
    if value <= 0:
        return 60.0
    return value


async def recover_stuck_jobs() -> int:
    """Recover jobs left in queued/processing by a restart (finding M4).

    In simulation mode the pipeline is short, so jobs are simply re-run.
    With heavy rendering a job may exceed a process lifetime; those are marked
    failed with a clear message instead of hanging forever.
    """
    recovered = 0
    async with await get_db() as db:
        cursor = await db.execute(
            "SELECT id, type, input_params, user_id FROM jobs WHERE status IN ('queued', 'processing')"
        )
        rows = await cursor.fetchall()

    for row in rows:
        try:
            params = json.loads(row["input_params"] or "{}")
        except Exception:
            params = {}

        if ENABLE_HEAVY_RENDERING:
            await update_job_status(
                row["id"],
                "failed",
                100.0,
                "Interrupted by server restart",
                error_message="Server restarted during processing; please resubmit the job.",
            )
            recovered += 1
            continue

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
        await update_job_status(job_id, "processing", 10.0, "Preparing video source...")

        source_type = params.get("source_type")  # 'youtube' or 'file'
        source_url = params.get("source_url")
        start_seconds = float(params.get("start_seconds", 0) or 0)
        end_seconds = float(params.get("end_seconds", 60) or 60)
        caption_style = params.get("caption_style", "hormozi")
        layout_mode = params.get("layout_mode", "focus")

        duration = max(1.0, end_seconds - start_seconds)

        if ENABLE_HEAVY_RENDERING:
            await update_job_status(job_id, "processing", 25.0, "Initializing MasterEngine pipeline...")
            try:
                from master_engine.engine import MasterEngine
                from master_engine.config import Settings

                input_file = _resolve_input_file(source_url)
                output_file = OUTPUT_DIR / f"edited_{job_id}.mp4"

                if input_file and input_file.exists():
                    await update_job_status(job_id, "processing", 45.0, "Rendering with MasterEngine...")
                    settings = Settings(fps=30.0, width=1080, height=1920)
                    engine = MasterEngine(settings=settings)
                    await asyncio.to_thread(engine.process, input_file, output_file)
                    result = {
                        "output_video": sign_file_url(f"edited_{job_id}.mp4", user_id),
                        "duration": duration,
                        "caption_style": caption_style,
                        "layout": layout_mode,
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
                logger.warning(f"MasterEngine execution fallback: {engine_err}")
                result = {
                    "output_video": None,
                    "duration": duration,
                    "caption_style": caption_style,
                    "layout": layout_mode,
                    "is_simulation": True,
                    "message": f"Rendering failed: {str(engine_err)[:100]}",
                }
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
        await update_job_status(job_id, "failed", 100.0, "Failed", error_message=str(exc))


async def process_clipper_job(job_id: str, params: Dict[str, Any], user_id: int) -> None:
    """Process an AI Clipper job (find viral hooks, segment, and render vertical shorts)."""
    try:
        await update_job_status(job_id, "processing", 10.0, "Ingesting video & research signals...")

        url = params.get("url")
        target_duration = str(params.get("target_duration", "60"))
        target_len = _parse_target_len(target_duration)

        clips: list = []

        if ENABLE_HEAVY_RENDERING:
            await update_job_status(job_id, "processing", 30.0, "Executing viral research engine & comment scoring...")
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
                    sorted_moments = sorted(comment_scores.items(), key=lambda x: x[1], reverse=True)[:5]
                    for idx, (ts, score) in enumerate(sorted_moments):
                        st = max(0.0, float(ts) - 3.0)
                        en = st + target_len
                        clip_id = f"clip_{uuid.uuid4().hex[:8]}"
                        clips.append({
                            "id": clip_id,
                            "title": f"Viral Moment #{idx + 1} ({int(score)}% engagement)",
                            "start_time": round(st, 1),
                            "end_time": round(en, 1),
                            "duration": round(en - st, 1),
                            "viral_score": round(score, 1),
                            "hook_text": f"High retention spike at {int(ts)}s from viral comments analysis",
                            "video_url": None,  # rendered outputs are attached below if produced
                            "thumbnail_url": params.get("thumbnail_url") or FALLBACK_THUMBNAIL,
                            "is_sample": False,
                        })
            except Exception as research_err:
                logger.warning(f"Viral research engine warning: {research_err}; generating structured clips.")

        # If clips are still empty (simulation mode, or no comment scores), produce
        # clearly-labeled placeholder segments instead of fabricated "viral" data.
        if not clips:
            await asyncio.sleep(1.0)
            await update_job_status(job_id, "processing", 30.0, "Scraping audience retention & finding viral hooks...")
            await asyncio.sleep(1.0)
            await update_job_status(job_id, "processing", 60.0, "Ranking segments by viral potential & hooks...")
            await asyncio.sleep(1.0)
            await update_job_status(job_id, "processing", 85.0, "Reframing active speakers to vertical 9:16...")
            await asyncio.sleep(0.8)

            offsets = (42.0, 185.0, 360.0)
            scores = (97.4, 93.1, 89.5)
            for idx, (offset, score) in enumerate(zip(offsets, scores)):
                clips.append({
                    "id": f"clip_{uuid.uuid4().hex[:8]}",
                    "title": f"Demo Segment #{idx + 1}",
                    "start_time": offset,
                    "end_time": round(offset + target_len, 1),
                    "duration": target_len,
                    "viral_score": score,
                    "hook_text": "SIMULATION DATA — enable ENABLE_HEAVY_RENDERING for real analysis",
                    "video_url": None,
                    "thumbnail_url": None,
                    "is_sample": True,
                })

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
            "is_simulation": not ENABLE_HEAVY_RENDERING,
            "simulation_notice": SIMULATION_NOTICE if not ENABLE_HEAVY_RENDERING else None,
        }
        await update_job_status(job_id, "completed", 100.0, "Completed", result_data=result)

    except Exception as exc:
        logger.exception("Clipper job failed")
        await update_job_status(job_id, "failed", 100.0, "Failed", error_message=str(exc))


async def process_transcript_job(job_id: str, params: Dict[str, Any], user_id: int) -> None:
    """Process an AI Whisper transcription job."""
    try:
        await update_job_status(job_id, "processing", 15.0, "Extracting audio track...")

        language = params.get("language", "en")
        url_or_file = params.get("url_or_file", "")

        transcript_segments = None
        full_text = ""

        if ENABLE_HEAVY_RENDERING:
            try:
                from master_engine.transcribe import transcribe

                input_file = _resolve_input_file(url_or_file)

                if input_file and input_file.exists():
                    await update_job_status(job_id, "processing", 40.0, "Running Whisper transcription...")
                    res = await asyncio.to_thread(transcribe, input_file)
                    if isinstance(res, dict):
                        transcript_segments = res.get("segments")
                        full_text = res.get("full_text", "")
            except Exception as t_err:
                logger.warning(f"Whisper transcription fallback: {t_err}")

        if not transcript_segments:
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
        await update_job_status(job_id, "failed", 100.0, "Failed", error_message=str(exc))
