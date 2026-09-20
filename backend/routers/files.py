from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Dict, Any
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, status
from fastapi.responses import FileResponse

from ..database import get_db
from ..auth import get_current_user

router = APIRouter(prefix="/api", tags=["Files"])

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "storage" / "uploads"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "storage" / "outputs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB limit
CHUNK_SIZE = 1024 * 1024  # 1 MB chunk
SAFE_FILENAME_REGEX = re.compile(r"^[a-zA-Z0-9_.-]+$")
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".mp3", ".wav", ".m4a"}


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> dict:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No file uploaded")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported format: {ext}. Allowed formats: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    file_id = f"up_{uuid.uuid4().hex[:12]}{ext}"
    dest_path = (UPLOAD_DIR / file_id).resolve()

    total_bytes = 0
    try:
        with dest_path.open("wb") as buffer:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_FILE_SIZE_BYTES:
                    # Exceeded maximum allowed size: abort & clean up
                    buffer.close()
                    dest_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail="File too large. Maximum allowed size is 500MB.",
                    )
                buffer.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(exc)}")

    # Store file ownership in database
    async with await get_db() as db:
        await db.execute(
            """
            INSERT INTO files (id, user_id, original_name, file_path, size_bytes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (file_id, current_user["id"], file.filename, str(dest_path), total_bytes),
        )
        await db.commit()

    return {
        "file_id": file_id,
        "original_name": file.filename,
        "url": f"/api/files/{file_id}",
        "size_bytes": total_bytes,
        "size_mb": round(total_bytes / (1024 * 1024), 2),
    }


@router.get("/files/{filename}")
async def get_file(filename: str):
    # Sanitize filename and prevent directory traversal
    clean_name = os.path.basename(filename)
    if not clean_name or not SAFE_FILENAME_REGEX.match(clean_name) or ".." in filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename")

    resolved_output_dir = OUTPUT_DIR.resolve()
    resolved_upload_dir = UPLOAD_DIR.resolve()

    # Check outputs first
    output_path = (OUTPUT_DIR / clean_name).resolve()
    try:
        if output_path.is_relative_to(resolved_output_dir) and output_path.is_file():
            return FileResponse(output_path, media_type="video/mp4", filename=clean_name)
    except (ValueError, RuntimeError):
        pass

    # Check uploads
    upload_path = (UPLOAD_DIR / clean_name).resolve()
    try:
        if upload_path.is_relative_to(resolved_upload_dir) and upload_path.is_file():
            return FileResponse(upload_path, filename=clean_name)
    except (ValueError, RuntimeError):
        pass

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

