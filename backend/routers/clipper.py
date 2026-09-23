from __future__ import annotations

import asyncio
import json
import uuid
from typing import Dict, Any, Optional, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..database import get_db
from ..auth import get_current_user
from ..worker import process_clipper_job, spawn_job
from ..utils.sources import validate_owned_source

router = APIRouter(prefix="/api/clipper", tags=["Clipper"])


class ClipperProcessRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    title: Optional[str] = Field(default="Auto Clipper Project", max_length=140)
    thumbnail_url: Optional[str] = Field(default=None, max_length=2048)
    analysis_mode: Literal["quick", "deep"] = "quick"
    # Keep this in sync with the public selector.  "auto" mirrors the bot's
    # Auto Best Length option while keeping the final render duration bounded.
    target_duration: Literal["auto", "30", "60", "90", "120"] = "auto"
    output_quality: Literal["720", "1080"] = "1080"
    clip_count: Literal[1, 3, 5] = 3


class ClipperProcessResponse(BaseModel):
    job_id: str
    project_id: str
    status: str
    message: str


@router.post("/process", response_model=ClipperProcessResponse)
async def process_clipper(
    req: ClipperProcessRequest,
    user: Dict[str, Any] = Depends(get_current_user),
) -> ClipperProcessResponse:
    # SSRF prevention check
    clean_url = await validate_owned_source(req.url, user)
    req.url = clean_url

    job_id = f"job_clip_{uuid.uuid4().hex[:10]}"
    project_id = f"proj_{uuid.uuid4().hex[:8]}"

    params = req.model_dump()

    async with await get_db() as db:
        await db.execute(
            """
            INSERT INTO projects (id, user_id, title, source_type, source_url, thumbnail_url)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                user["id"],
                req.title or "AI Viral Clipper Project",
                "youtube" if "youtube.com" in req.url or "youtu.be" in req.url else "file",
                req.url,
                req.thumbnail_url,
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
                "clipper",
                "queued",
                0.0,
                "Queued for viral analysis",
                json.dumps(params),
            ),
        )
        await db.commit()

    spawn_job(process_clipper_job(job_id, params, user["id"]))

    return ClipperProcessResponse(
        job_id=job_id,
        project_id=project_id,
        status="queued",
        message="AI Clipper analysis queued. Detecting viral moments and preparing vertical shorts.",
    )
