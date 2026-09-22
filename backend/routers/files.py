from __future__ import annotations

import os
import re
import uuid
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Query, Request, status
from fastapi.responses import RedirectResponse, Response, StreamingResponse

from ..database import get_db
from ..auth import get_current_user, get_optional_user
from ..utils.rate_limit import check_rate_limit, get_client_ip
from ..utils.security import sign_file_url, verify_file_signature
from ..utils.storage import create_download_url, delete_file as delete_stored_file, upload_file as upload_stored_file, using_object_storage

router = APIRouter(prefix="/api", tags=["Files"])
logger = logging.getLogger("clip_studio.files")

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "storage" / "uploads"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "storage" / "outputs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE_BYTES = int(os.getenv("MAX_FILE_SIZE_MB", "100")) * 1024 * 1024
MAX_TOTAL_PER_USER_BYTES = int(os.getenv("MAX_TOTAL_STORAGE_PER_USER_MB", "2048")) * 1024 * 1024
MAX_FILES_PER_USER = int(os.getenv("MAX_FILES_PER_USER", "50"))
CHUNK_SIZE = 1024 * 1024  # 1 MB chunk
SAFE_FILENAME_REGEX = re.compile(r"^[a-zA-Z0-9_.-]+$")
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".mp3", ".wav", ".m4a"}


def _magic_ok(head: bytes, ext: str) -> bool:
    """Verify the first bytes look like the declared media type (finding C5).

    Extension checks alone are trivially spoofable; this is a lightweight
    sanity check, not full format validation.
    """
    if ext in (".mp4", ".mov", ".m4a"):
        return b"ftyp" in head[:16]
    if ext in (".mkv", ".webm"):
        return head.startswith(b"\x1aE\xdf\xa3")  # EBML header
    if ext == ".avi":
        return head.startswith(b"RIFF") and b"AVI " in head[:16]
    if ext == ".mp3":
        return head.startswith((b"ID3", b"\xff\xfb", b"\xff\xf3"))
    if ext == ".wav":
        return head.startswith(b"RIFF") and b"WAVE" in head[:16]
    return False


async def _user_storage_usage(user_id: int) -> tuple[int, int]:
    """Returns (total_bytes, file_count) for the user's uploads."""
    async with await get_db() as db:
        cursor = await db.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) AS total, COUNT(*) AS cnt FROM files WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        return int(row["total"]), int(row["cnt"])


async def _ownership_ok(filename: str, user_id: Optional[int]) -> bool:
    if user_id is None:
        return False
    async with await get_db() as db:
        cursor = await db.execute(
            "SELECT 1 FROM files WHERE id = ? AND user_id = ? LIMIT 1",
            (filename, user_id),
        )
        return await cursor.fetchone() is not None


@router.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> dict:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No file uploaded")

    client_ip = get_client_ip(request)
    await check_rate_limit(
        key=f"upload:{client_ip}",
        max_requests=20,
        window_seconds=3600,
        error_message="Too many uploads. Please try again later.",
    )

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported format: {ext}. Allowed formats: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # Per-user storage quota (CODE_REVIEW.md finding C5)
    used_bytes, file_count = await _user_storage_usage(current_user["id"])
    if used_bytes >= MAX_TOTAL_PER_USER_BYTES or file_count >= MAX_FILES_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                "Storage quota reached. Delete some files or upgrade your plan. "
                f"Limits: {MAX_TOTAL_PER_USER_BYTES // (1024 * 1024)}MB / {MAX_FILES_PER_USER} files."
            ),
        )

    file_id = f"up_{uuid.uuid4().hex[:12]}{ext}"
    # The local path is only a short-lived staging file. Production persists it
    # to object storage before creating the database record.
    dest_path = (UPLOAD_DIR / f".{file_id}.uploading").resolve()
    storage_key = f"uploads/{file_id}"

    total_bytes = 0
    head = b""
    try:
        with dest_path.open("wb") as buffer:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                if total_bytes == 0:
                    head = chunk[:64]
                total_bytes += len(chunk)
                if total_bytes > MAX_FILE_SIZE_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=f"File too large. Maximum allowed size is {MAX_FILE_SIZE_BYTES // (1024 * 1024)}MB.",
                    )
                buffer.write(chunk)
    except HTTPException:
        dest_path.unlink(missing_ok=True)
        raise
    except Exception:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Upload failed. Please try again.")

    # Content must match the declared media format
    if not _magic_ok(head, ext):
        dest_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File content does not match the declared media format.",
        )

    try:
        if using_object_storage():
            await upload_stored_file(storage_key, dest_path, file.content_type or "application/octet-stream")
        else:
            final_path = (UPLOAD_DIR / file_id).resolve()
            dest_path.replace(final_path)
            dest_path = final_path
    except Exception:
        logger.exception("Durable upload failed for %s", file_id)
        dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail="Media storage is temporarily unavailable. Please try again.")

    # Store file ownership only after durable storage succeeds.
    try:
        async with await get_db() as db:
            await db.execute(
                """
                INSERT INTO files (id, user_id, original_name, file_path, size_bytes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (file_id, current_user["id"], file.filename, storage_key if using_object_storage() else str(dest_path), total_bytes),
            )
            await db.commit()
    except Exception:
        if using_object_storage():
            await delete_stored_file(storage_key)
        dest_path.unlink(missing_ok=True)
        raise
    finally:
        # Never retain production uploads on the web-service filesystem.
        if using_object_storage():
            dest_path.unlink(missing_ok=True)

    return {
        "file_id": file_id,
        "original_name": file.filename,
        "url": sign_file_url(file_id, current_user["id"]),
        "url_base": f"/api/files/{file_id}",
        "size_bytes": total_bytes,
        "size_mb": round(total_bytes / (1024 * 1024), 2),
    }


def _resolve_media_path(clean_name: str) -> Optional[Path]:
    """Locate the file under OUTPUT_DIR or UPLOAD_DIR with traversal protection."""
    resolved_output_dir = OUTPUT_DIR.resolve()
    resolved_upload_dir = UPLOAD_DIR.resolve()

    output_path = (OUTPUT_DIR / clean_name).resolve()
    try:
        if output_path.is_relative_to(resolved_output_dir) and output_path.is_file():
            return output_path
    except (ValueError, RuntimeError):
        pass

    upload_path = (UPLOAD_DIR / clean_name).resolve()
    try:
        if upload_path.is_relative_to(resolved_upload_dir) and upload_path.is_file():
            return upload_path
    except (ValueError, RuntimeError):
        pass

    return None


@router.get("/files/{filename}")
async def get_file(
    filename: str,
    exp: Optional[str] = Query(None),
    uid: Optional[str] = Query(None),
    sig: Optional[str] = Query(None),
    current_user: Optional[Dict[str, Any]] = Depends(get_optional_user),
):
    """Serve a file only to authorized callers (CODE_REVIEW.md finding C3).

    Access requires one of:
    - a valid signed URL (expiring, bound to the owner's user id), or
    - a valid Bearer token plus DB ownership of the upload, or
    - ALLOW_PUBLIC_FILE_URLS=1 (local development only).
    """
    clean_name = os.path.basename(filename)
    if not clean_name or not SAFE_FILENAME_REGEX.match(clean_name) or ".." in filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename")

    public_mode = os.getenv("ALLOW_PUBLIC_FILE_URLS", "0").strip() == "1"
    authorized = False

    if public_mode:
        authorized = True
    elif verify_file_signature(clean_name, uid, exp, sig):
        authorized = True
    elif current_user is not None:
        authorized = await _ownership_ok(clean_name, current_user["id"])

    if not authorized:
        # 404 rather than 403 to avoid confirming a file exists for the caller.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    if using_object_storage():
        # The storage key is not user-controlled: file ownership/signature was
        # verified above and upload objects use this fixed key convention.
        try:
            namespace = "uploads" if clean_name.startswith("up_") else "outputs"
            return RedirectResponse(await create_download_url(f"{namespace}/{clean_name}"), status_code=307)
        except Exception:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    media_path = _resolve_media_path(clean_name)
    if media_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    media_type = "video/mp4" if media_path.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm", ".avi") else "application/octet-stream"

    # Small artifacts are returned directly. This makes tiny transcript/test
    # downloads fast and avoids a Starlette streaming deadlock in Python 3.14's
    # in-process ASGI transport. Large media remains constant-memory streamed.
    headers = {"Content-Disposition": f'attachment; filename="{clean_name}"'}
    if media_path.stat().st_size <= 8 * 1024 * 1024:
        return Response(content=media_path.read_bytes(), media_type=media_type, headers=headers)

    # StreamingResponse avoids Starlette's sendfile extension, which hangs
    # under the in-process ASGI transport used by our test environment while
    # retaining constant-memory downloads in production.
    def stream_file():
        with media_path.open("rb") as handle:
            while chunk := handle.read(CHUNK_SIZE):
                yield chunk

    return StreamingResponse(
        stream_file(),
        media_type=media_type,
        headers=headers,
    )


@router.delete("/files/{filename}")
async def delete_file(
    filename: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> dict:
    """Delete an owned upload (and its DB row) so users can manage their quota."""
    clean_name = os.path.basename(filename)
    if not clean_name or not SAFE_FILENAME_REGEX.match(clean_name):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename")

    if not await _ownership_ok(clean_name, current_user["id"]):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    async with await get_db() as db:
        cursor = await db.execute("SELECT file_path FROM files WHERE id = ? AND user_id = ?", (clean_name, current_user["id"]))
        row = await cursor.fetchone()
        await db.execute("DELETE FROM files WHERE id = ? AND user_id = ?", (clean_name, current_user["id"]))
        await db.commit()

    if using_object_storage():
        await delete_stored_file(str(row["file_path"]))
        return {"deleted": clean_name}

    upload_path = (UPLOAD_DIR / clean_name).resolve()
    if upload_path.is_relative_to(UPLOAD_DIR.resolve()):
        upload_path.unlink(missing_ok=True)

    return {"deleted": clean_name}
