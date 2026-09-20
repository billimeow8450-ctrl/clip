from __future__ import annotations

import io
import pytest
from httpx import AsyncClient
import backend.routers.files as files_module


@pytest.mark.asyncio
async def test_upload_requires_auth(client: AsyncClient):
    """Anonymous file upload must be rejected with 401."""
    files = {"file": ("video.mp4", b"dummy mp4 content", "video/mp4")}
    response = await client.post("/api/upload", files=files)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_upload_authenticated_success(client: AsyncClient, auth_user, tmp_path, monkeypatch):
    """Authenticated upload creates file in storage and records DB entry."""
    monkeypatch.setattr(files_module, "UPLOAD_DIR", tmp_path / "uploads")
    (tmp_path / "uploads").mkdir(parents=True, exist_ok=True)

    file_content = b"\x00\x00\x00\x18ftypmp42" + b"A" * 1024
    files = {"file": ("my_podcast.mp4", io.BytesIO(file_content), "video/mp4")}

    response = await client.post("/api/upload", files=files, headers=auth_user["headers"])
    assert response.status_code == 200
    data = response.json()
    assert "file_id" in data
    assert data["original_name"] == "my_podcast.mp4"
    assert data["size_bytes"] == len(file_content)


@pytest.mark.asyncio
async def test_upload_unsupported_format_rejected(client: AsyncClient, auth_user):
    """Dangerous or unsupported extensions (.exe, .sh, .py) must be rejected with 400."""
    files = {"file": ("malicious.exe", io.BytesIO(b"executable"), "application/octet-stream")}
    response = await client.post("/api/upload", files=files, headers=auth_user["headers"])
    assert response.status_code == 400
    assert "unsupported format" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upload_size_limit_enforcement(client: AsyncClient, auth_user, tmp_path, monkeypatch):
    """Uploading beyond the size limit must immediately abort with 413 and delete partial file."""
    monkeypatch.setattr(files_module, "UPLOAD_DIR", tmp_path / "uploads")
    (tmp_path / "uploads").mkdir(parents=True, exist_ok=True)
    # Set artificial small limit for test
    monkeypatch.setattr(files_module, "MAX_FILE_SIZE_BYTES", 500)
    monkeypatch.setattr(files_module, "CHUNK_SIZE", 256)

    large_content = b"X" * 1000  # Exceeds 500 bytes
    files = {"file": ("oversized.mp4", io.BytesIO(large_content), "video/mp4")}

    response = await client.post("/api/upload", files=files, headers=auth_user["headers"])
    assert response.status_code == 413
    assert "file too large" in response.json()["detail"].lower()

    # Ensure no partial file remained on disk
    upload_files = list((tmp_path / "uploads").glob("*"))
    assert len(upload_files) == 0


@pytest.mark.asyncio
async def test_path_traversal_download_blocked(client: AsyncClient):
    """Attempting path traversal via ../ must be rejected."""
    # Direct dot-dot traversal
    res1 = await client.get("/api/files/../../etc/passwd")
    assert res1.status_code in (400, 404)

    # Encoded dot-dot traversal
    res2 = await client.get("/api/files/..%2f..%2f.env")
    assert res2.status_code in (400, 404)

    # Invalid characters
    res3 = await client.get("/api/files/file;rm -rf")
    assert res3.status_code in (400, 404)
