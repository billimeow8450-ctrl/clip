"""Server-side administration endpoints for Manthan Ventures."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..auth import get_current_user, is_admin_user
from ..database import get_db

router = APIRouter(prefix="/api/admin", tags=["Administration"])


async def require_admin(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    if not is_admin_user(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required.")
    return user


@router.get("/overview")
async def overview(_: Dict[str, Any] = Depends(require_admin)) -> Dict[str, int]:
    """Return aggregate operations metrics without exposing user content."""
    async with await get_db() as db:
        values: Dict[str, int] = {}
        for name, query in {
            "users": "SELECT COUNT(*) AS count FROM users",
            "projects": "SELECT COUNT(*) AS count FROM projects",
            "jobs": "SELECT COUNT(*) AS count FROM jobs",
            "active_jobs": "SELECT COUNT(*) AS count FROM jobs WHERE status IN ('queued', 'processing')",
            "files": "SELECT COUNT(*) AS count FROM files",
        }.items():
            row = await (await db.execute(query)).fetchone()
            values[name] = int(row["count"] if row else 0)
    return values


@router.get("/users")
async def list_users(
    _: Dict[str, Any] = Depends(require_admin),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> List[Dict[str, Any]]:
    """List account metadata for support work; password hashes never leave DB."""
    async with await get_db() as db:
        cursor = await db.execute(
            """
            SELECT id, email, username, tier, created_at
            FROM users
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [dict(row) for row in await cursor.fetchall()]
