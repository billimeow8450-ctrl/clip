from __future__ import annotations

import asyncio
import json
import math
import uuid
from typing import Dict, Any, Optional, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..database import get_db
from ..auth import get_current_user
from ..worker import process_editor_job, spawn_job
from ..utils.sources import validate_owned_source

router = APIRouter(prefix="/api/editor", tags=["Editor"])


class EditorProcessRequest(BaseModel):
    source_type: str = Field(min_length=1, max_length=20)
    source_url: str = Field(min_length=1, max_length=2048)
    title: Optional[str] = Field(default="Untitled Clip Project", max_length=140)
    thumbnail_url: Optional[str] = Field(default=None, max_length=2048)
    start_seconds: float
    end_seconds: float
    caption_style: Literal["hormozi", "minimal", "bold"] = "hormozi"
    layout_mode: Literal["focus", "split", "auto"] = "focus"


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
    if req.source_type not in {"youtube", "file"}:
        raise HTTPException(status_code=400, detail="Source type must be 'youtube' or 'file'.")

    if not math.isfinite(req.start_seconds) or not math.isfinite(req.end_seconds) or req.start_seconds < 0:
        raise HTTPException(status_code=400, detail="Timestamps must be finite, non-negative values.")

    if req.end_seconds <= req.start_seconds:
        raise HTTPException(status_code=400, detail="End timestamp must be greater than start timestamp.")

    duration = req.end_seconds - req.start_seconds
    if duration > 600:  # 10 min cap per single clip
        raise HTTPException(status_code=400, detail="Maximum clip duration is 10 minutes.")

    clean_source = await validate_owned_source(req.source_url, user)
    req.source_url = clean_source

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
