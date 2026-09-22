from __future__ import annotations

import asyncio
import json
import uuid
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..database import get_db
from ..auth import get_current_user
from ..worker import process_transcript_job, spawn_job
from ..utils.sources import validate_owned_source

router = APIRouter(prefix="/api/transcript", tags=["Transcript"])


class TranscriptProcessRequest(BaseModel):
    url_or_file: str
    title: Optional[str] = "Transcription Project"
    language: Optional[str] = "en"  # 'en', 'ur', 'ru', 'bilingual'
    export_format: Optional[str] = "txt"  # 'txt', 'srt', 'pdf'


class TranscriptProcessResponse(BaseModel):
    job_id: str
    project_id: str
    status: str
    message: str


@router.post("/process", response_model=TranscriptProcessResponse)
async def process_transcript(
    req: TranscriptProcessRequest,
    user: Dict[str, Any] = Depends(get_current_user),
) -> TranscriptProcessResponse:
    # SSRF prevention check
    clean_target = await validate_owned_source(req.url_or_file, user)
    req.url_or_file = clean_target

    job_id = f"job_trans_{uuid.uuid4().hex[:10]}"
    project_id = f"proj_{uuid.uuid4().hex[:8]}"

    params = req.model_dump()

    async with await get_db() as db:
        await db.execute(
            """
            INSERT INTO projects (id, user_id, title, source_type, source_url)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                project_id,
                user["id"],
                req.title or "Whisper Transcription Project",
                "youtube" if "youtube.com" in req.url_or_file or "youtu.be" in req.url_or_file else "file",
                req.url_or_file,
            ),
        )

        await db.execute(
            """
            INSERT INTO jobs (id, user_id, project_id, type, status, progress, stage, input_params)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                user["id"],
                project_id,
                "transcript",
                "queued",
                0.0,
                "Queued for transcription",
                json.dumps(params),
            ),
        )
        await db.commit()

    spawn_job(process_transcript_job(job_id, params, user["id"]))

    return TranscriptProcessResponse(
        job_id=job_id,
        project_id=project_id,
        status="queued",
        message="Transcription job queued with faster-whisper.",
    )
