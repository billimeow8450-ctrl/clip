"""Source authorization shared by video-processing endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from ..database import get_db
from .security import get_local_upload_id, validate_source_url


async def validate_owned_source(source: str, user: dict[str, Any]) -> str:
    """Validate a source and ensure a local upload belongs to the current user.

    External sources are constrained by ``validate_source_url``.  A local upload
    is an object capability only for downloads; processing it must additionally
    check database ownership to prevent one account from rendering another
    account's upload by guessing its id.
    """
    clean_source = validate_source_url(source)
    upload_id = get_local_upload_id(clean_source)
    if not upload_id:
        return clean_source

    async with await get_db() as db:
        cursor = await db.execute(
            "SELECT 1 FROM files WHERE id = ? AND user_id = ? LIMIT 1",
            (upload_id, user["id"]),
        )
        if not await cursor.fetchone():
            # Match download semantics: do not disclose whether another user's
            # upload id exists.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Source file was not found.",
            )
    return clean_source
