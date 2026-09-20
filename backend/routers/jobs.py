from __future__ import annotations

import json
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..database import get_db
from ..auth import get_current_user

router = APIRouter(prefix="/api", tags=["Jobs & Projects"])


@router.get("/jobs")
async def list_jobs(user: Dict[str, Any] = Depends(get_current_user)) -> List[Dict[str, Any]]:
    async with await get_db() as db:
        cursor = await db.execute(
            """
            SELECT id, project_id, type, status, progress, stage, result_data, error_message, created_at, updated_at
            FROM jobs
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (user["id"],),
        )
        rows = await cursor.fetchall()
        jobs = []
        for r in rows:
            d = dict(r)
            if d.get("result_data"):
                try:
                    d["result_data"] = json.loads(d["result_data"])
                except Exception:
                    pass
            jobs.append(d)
        return jobs


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    async with await get_db() as db:
        cursor = await db.execute(
            """
            SELECT id, project_id, type, status, progress, stage, input_params, result_data, error_message, created_at, updated_at
            FROM jobs
            WHERE id = ? AND user_id = ?
            """,
            (job_id, user["id"]),
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")

        job = dict(row)
        if job.get("input_params"):
            try:
                job["input_params"] = json.loads(job["input_params"])
            except Exception:
                pass
        if job.get("result_data"):
            try:
                job["result_data"] = json.loads(job["result_data"])
            except Exception:
                pass
        return job


@router.get("/projects")
async def list_projects(user: Dict[str, Any] = Depends(get_current_user)) -> List[Dict[str, Any]]:
    async with await get_db() as db:
        cursor = await db.execute(
            """
            SELECT id, title, source_type, source_url, duration, thumbnail_url, created_at
            FROM projects
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (user["id"],),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


@router.get("/clips")
async def list_clips(user: Dict[str, Any] = Depends(get_current_user)) -> List[Dict[str, Any]]:
    async with await get_db() as db:
        cursor = await db.execute(
            """
            SELECT id, job_id, title, start_time, end_time, duration, viral_score, hook_text, video_url, thumbnail_url, created_at
            FROM clips
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (user["id"],),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
