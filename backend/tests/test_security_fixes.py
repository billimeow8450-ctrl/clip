from __future__ import annotations

import io
import logging
import time

import pytest
from httpx import AsyncClient

from backend.utils.security import sign_file_url, verify_file_signature

MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"A" * 1024


def _reset_token_from_logs(caplog) -> str:
    """Extract the reset token emitted by the dev echo email transport."""
    tokens = [
        rec.getMessage().split(": ", 1)[1].split(" (dev only")[0]
        for rec in caplog.records
        if "reset token for" in rec.getMessage()
    ]
    assert tokens, "expected reset token via email subsystem (RESET_TOKEN_DEBUG_ECHO=1)"
    return tokens[-1]


async def _upload_video(client: AsyncClient, auth_user, tmp_path, monkeypatch) -> dict:
    import backend.routers.files as files_module

    monkeypatch.setattr(files_module, "UPLOAD_DIR", tmp_path / "uploads")
    (tmp_path / "uploads").mkdir(parents=True, exist_ok=True)

    files = {"file": ("video.mp4", io.BytesIO(MP4_BYTES), "video/mp4")}
    res = await client.post("/api/upload", files=files, headers=auth_user["headers"])
    assert res.status_code == 200
    return res.json()


# ---------------------------------------------------------------------------
# C3: file downloads must be ownership-checked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_requires_ownership_or_signature(client, auth_user, tmp_path, monkeypatch):
    data = await _upload_video(client, auth_user, tmp_path, monkeypatch)
    file_id = data["file_id"]

    # Anonymous caller without a signature must not get the file (or learn it exists)
    anon = await client.get(f"/api/files/{file_id}")
    assert anon.status_code == 404

    # Signed URL issued at upload time works without a session
    signed = await client.get(data["url"])
    assert signed.status_code == 200
    assert signed.content.startswith(b"\x00\x00\x00\x18ftyp")

    # A different authenticated user without ownership is rejected
    reg = await client.post(
        "/api/auth/register",
        json={"email": "intruder@example.com", "username": "intruder", "password": "IntruderPass1"},
    )
    assert reg.status_code == 200
    intruder_headers = {"Authorization": f"Bearer {reg.json()['token']}"}
    stolen = await client.get(f"/api/files/{file_id}", headers=intruder_headers)
    assert stolen.status_code == 404

    # Owner with a Bearer token can always fetch their own upload
    owner = await client.get(f"/api/files/{file_id}", headers=auth_user["headers"])
    assert owner.status_code == 200


@pytest.mark.asyncio
async def test_signed_url_cannot_be_reused_for_another_file(client):
    """Unit-level: signatures are bound to filename + uid + expiry."""
    url = sign_file_url("up_abc123.mp4", 7, ttl_seconds=60)
    exp = url.split("exp=")[1].split("&")[0]
    uid = url.split("uid=")[1].split("&")[0]
    sig = url.split("sig=")[1]

    assert verify_file_signature("up_abc123.mp4", uid, exp, sig) is True
    assert verify_file_signature("up_other.mp4", uid, exp, sig) is False  # wrong file
    assert verify_file_signature("up_abc123.mp4", str(int(uid) + 1), exp, sig) is False  # wrong owner
    assert verify_file_signature("up_abc123.mp4", uid, str(int(time.time()) - 10), sig) is False  # expired
    assert verify_file_signature("up_abc123.mp4", uid, exp, "0" * 32) is False  # forged
    assert verify_file_signature("up_abc123.mp4", uid, None, None) is False  # missing params


@pytest.mark.asyncio
async def test_delete_file_owner_only(client, auth_user, tmp_path, monkeypatch):
    data = await _upload_video(client, auth_user, tmp_path, monkeypatch)
    file_id = data["file_id"]

    reg = await client.post(
        "/api/auth/register",
        json={"email": "someone@example.com", "username": "someone", "password": "SomeonesPass1"},
    )
    other_headers = {"Authorization": f"Bearer {reg.json()['token']}"}

    # Non-owner cannot delete
    forbidden = await client.delete(f"/api/files/{file_id}", headers=other_headers)
    assert forbidden.status_code == 404

    # Owner deletes successfully
    deleted = await client.delete(f"/api/files/{file_id}", headers=auth_user["headers"])
    assert deleted.status_code == 200

    # File is gone afterwards
    gone = await client.get(f"/api/files/{file_id}", headers=auth_user["headers"])
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_processing_rejects_another_users_local_upload(client, auth_user, tmp_path, monkeypatch):
    """Local upload ids are not valid cross-account processing capabilities."""
    data = await _upload_video(client, auth_user, tmp_path, monkeypatch)
    reg = await client.post(
        "/api/auth/register",
        json={"email": "processor@example.com", "username": "processor", "password": "ProcessorPass1"},
    )
    assert reg.status_code == 200
    other_headers = {"Authorization": f"Bearer {reg.json()['token']}"}

    responses = [
        await client.post("/api/clipper/process", json={"url": data["url"]}, headers=other_headers),
        await client.post("/api/transcript/process", json={"url_or_file": data["url"]}, headers=other_headers),
        await client.post(
            "/api/editor/process",
            json={
                "source_type": "file",
                "source_url": data["url"],
                "start_seconds": 0,
                "end_seconds": 30,
            },
            headers=other_headers,
        ),
    ]
    assert all(response.status_code == 404 for response in responses)


@pytest.mark.asyncio
async def test_clipper_rejects_unsupported_render_options(client, auth_user):
    """The public API must not accept arbitrary resolution or unbounded batches."""
    bad_quality = await client.post(
        "/api/clipper/process",
        json={"url": "https://youtu.be/4Vz6L8B73i4", "output_quality": "2160"},
        headers=auth_user["headers"],
    )
    bad_count = await client.post(
        "/api/clipper/process",
        json={"url": "https://youtu.be/4Vz6L8B73i4", "clip_count": 20},
        headers=auth_user["headers"],
    )
    assert bad_quality.status_code == 422
    assert bad_count.status_code == 422


def test_youtube_proxy_format_matches_bot_runtime_format():
    """Render uses the same host:port:user:password convention as the bot."""
    from backend.worker import _normalize_proxy_url

    normalized = _normalize_proxy_url("proxy.example:443:user-name:pa$$word")
    assert normalized == "http://user-name:pa%24%24word@proxy.example:443"
    assert _normalize_proxy_url("") is None
    with pytest.raises(ValueError):
        _normalize_proxy_url("proxy.example:not-a-port:user:password")


@pytest.mark.asyncio
async def test_editor_rejects_invalid_source_type_and_timestamps(client, auth_user):
    invalid_type = await client.post(
        "/api/editor/process",
        json={"source_type": "remote", "source_url": "https://youtu.be/example", "start_seconds": 0, "end_seconds": 30},
        headers=auth_user["headers"],
    )
    assert invalid_type.status_code == 400

    invalid_time = await client.post(
        "/api/editor/process",
        json={"source_type": "youtube", "source_url": "https://youtu.be/example", "start_seconds": -1, "end_seconds": 30},
        headers=auth_user["headers"],
    )
    assert invalid_time.status_code == 400


@pytest.mark.asyncio
async def test_admin_api_requires_server_side_email_allowlist(client, auth_user, monkeypatch):
    denied = await client.get("/api/admin/overview", headers=auth_user["headers"])
    assert denied.status_code == 403

    monkeypatch.setenv("ADMIN_EMAILS", auth_user["email"])
    allowed = await client.get("/api/admin/overview", headers=auth_user["headers"])
    assert allowed.status_code == 200
    assert set(allowed.json()) == {"users", "projects", "jobs", "active_jobs", "files"}


# ---------------------------------------------------------------------------
# C4/M7: logout revokes the JWT server-side
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_revokes_token(client, auth_user):
    me = await client.get("/api/auth/me", headers=auth_user["headers"])
    assert me.status_code == 200

    out = await client.post("/api/auth/logout", headers=auth_user["headers"])
    assert out.status_code == 200

    after = await client.get("/api/auth/me", headers=auth_user["headers"])
    assert after.status_code == 401


@pytest.mark.asyncio
async def test_password_reset_invalidates_existing_jwt(client, auth_user, caplog):
    caplog.clear()
    forgot = await client.post("/api/auth/forgot-password", json={"email": auth_user["email"]})
    assert forgot.status_code == 200
    reset_token = _reset_token_from_logs(caplog)

    reset = await client.post(
        "/api/auth/reset-password",
        json={"token": reset_token, "new_password": "BrandNewSecurePassword2026!"},
    )
    assert reset.status_code == 200

    # The JWT issued before the reset must now be rejected (token_version bump)
    old_session = await client.get("/api/auth/me", headers=auth_user["headers"])
    assert old_session.status_code == 401

    # And the new password works
    login = await client.post(
        "/api/auth/login",
        json={"email_or_username": auth_user["email"], "password": "BrandNewSecurePassword2026!"},
    )
    assert login.status_code == 200


# ---------------------------------------------------------------------------
# Input validation hardening
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_rejects_weak_passwords(client):
    # No digit
    res = await client.post(
        "/api/auth/register",
        json={"email": "weak@example.com", "username": "weakuser", "password": "lettersonlypass"},
    )
    assert res.status_code == 400
    assert "letter" in res.json()["detail"].lower()

    # Too short (pydantic min_length=8)
    res2 = await client.post(
        "/api/auth/register",
        json={"email": "weak2@example.com", "username": "weakuser2", "password": "short1"},
    )
    assert res2.status_code == 422


@pytest.mark.asyncio
async def test_register_rejects_invalid_usernames(client):
    res = await client.post(
        "/api/auth/register",
        json={"email": "badname@example.com", "username": "bad user!!", "password": "GoodPass123"},
    )
    assert res.status_code == 400
