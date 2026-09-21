from __future__ import annotations

import asyncio
import json
import uuid
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..database import get_db
from ..auth import get_current_user
from ..worker import process_editor_job, spawn_job

router = APIRouter(prefix="/api/editor", tags=["Editor"])


class EditorProcessRequest(BaseModel):
    source_type: str  # 'youtube' or 'file'
    source_url: str
    title: Optional[str] = "Untitled Clip Project"
    thumbnail_url: Optional[str] = None
    start_seconds: float
    end_seconds: float
    caption_style: Optional[str] = "hormozi"
    layout_mode: Optional[str] = "focus"


class EditorProcessResponse(BaseModel):
    job_id: str
    project_id: str
    status: str
    message: str


@router.post("/process", response_model=EditorProcessResponse)
async def process_editor(
    req: EditorProcessRequest,
    user: Dict[str, Any] = Depends(get_current_user),
) -> EditorProcessResponse:
    if req.end_seconds <= req.start_seconds:
        raise HTTPException(status_code=400, detail="End timestamp must be greater than start timestamp.")

    duration = req.end_seconds - req.start_seconds
    if duration > 600:  # 10 min cap per single clip
        raise HTTPException(status_code=400, detail="Maximum clip duration is 10 minutes.")

    job_id = f"job_ed_{uuid.uuid4().hex[:10]}"
    project_id = f"proj_{uuid.uuid4().hex[:8]}"

    params = req.model_dump()

    async with await get_db() as db:
        # Create project record
        await db.execute(
            """
            INSERT INTO projects (id, user_id, title, source_type, source_url, duration, thumbnail_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                user["id"],
                req.title or "AI Edited Clip",
                req.source_type,
                req.source_url,
                duration,
                req.thumbnail_url,
            ),
        )

        # Create job record
        await db.execute(
            """
            INSERT INTO jobs (id, user_id, project_id, type, status, progress, stage, input_params)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                user["id"],
                project_id,
                "editor",
                "queued",
                0.0,
                "Queued for processing",
                json.dumps(params),
            ),
        )
        await db.commit()

    spawn_job(process_editor_job(job_id, params, user["id"]))

    return EditorProcessResponse(
        job_id=job_id,
        project_id=project_id,
        status="queued",
        message="Job queued successfully. You can track its progress.",
    )
