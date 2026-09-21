from __future__ import annotations

import os
import pytest
from httpx import AsyncClient
from backend.auth import get_password_hash, verify_password, resolve_jwt_secret
from backend.utils.rate_limit import clear_rate_limits


@pytest.mark.asyncio
async def test_register_success(client: AsyncClient):
    response = await client.post(
        "/api/auth/register",
        json={
            "email": "newuser@example.com",
            "username": "newuser",
            "password": "Password123!",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert data["user"]["email"] == "newuser@example.com"
    assert data["user"]["username"] == "newuser"


@pytest.mark.asyncio
async def test_register_duplicate_rejected(client: AsyncClient, auth_user):
    # Try registering with existing user email
    response = await client.post(
        "/api/auth/register",
        json={
            "email": auth_user["email"],
            "username": "unique_name",
            "password": "Password123!",
        },
    )
    assert response.status_code == 400
    assert "already registered" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient, auth_user):
    response = await client.post(
        "/api/auth/login",
        json={
            "email_or_username": auth_user["email"],
            "password": auth_user["password"],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert data["user"]["id"] == auth_user["id"]


@pytest.mark.asyncio
async def test_login_invalid_password(client: AsyncClient, auth_user):
    response = await client.post(
        "/api/auth/login",
        json={
            "email_or_username": auth_user["email"],
            "password": "WrongPassword123",
        },
    )
    assert response.status_code == 401
    assert "incorrect" in response.json()["detail"].lower()


def test_password_hashing_strictly_bcrypt_no_sha256():
    """Verify that bcrypt is enforced and SHA-256 fallback was completely eliminated."""
    password = "TestBcryptPassword2026!"
    hashed = get_password_hash(password)

    # Must start with bcrypt prefix ($2b$ or $2a$)
    assert hashed.startswith("$2b$") or hashed.startswith("$2a$")
    assert verify_password(password, hashed) is True
    assert verify_password("wrong", hashed) is False

    # A raw SHA-256 hash must NOT verify
    import hashlib
    fake_sha256 = hashlib.sha256(password.encode()).hexdigest()
    assert verify_password(password, fake_sha256) is False


def test_jwt_secret_validation_in_production(monkeypatch):
    """Production mode must reject missing or default/insecure secret keys."""
    monkeypatch.setenv("ENVIRONMENT", "production")

    # Missing key in production raises RuntimeError
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="CRITICAL SECURITY ERROR"):
        resolve_jwt_secret()

    # Insecure default key in production raises RuntimeError
    monkeypatch.setenv("JWT_SECRET_KEY", "super-secret-clip-studio-key-change-in-production-2026")
    with pytest.raises(RuntimeError, match="CRITICAL SECURITY ERROR"):
        resolve_jwt_secret()


@pytest.mark.asyncio
async def test_rate_limiting_on_login(client: AsyncClient, auth_user, monkeypatch):
    """Verify rate limiting blocks brute force login attempts beyond limit."""
    clear_rate_limits()
    monkeypatch.setenv("MAX_LOGIN_ATTEMPTS_PER_MIN", "3")

    # Perform 3 attempts (allowed)
    for _ in range(3):
        res = await client.post(
            "/api/auth/login",
            json={"email_or_username": auth_user["email"], "password": "wrong"},
        )
        assert res.status_code == 401

    # 4th attempt must be rate-limited with 429
    res4 = await client.post(
        "/api/auth/login",
        json={"email_or_username": auth_user["email"], "password": "wrong"},
    )
    assert res4.status_code == 429
    assert "too many" in res4.json()["detail"].lower()


@pytest.mark.asyncio
async def test_forgot_and_reset_password_flow(client: AsyncClient, auth_user, caplog):
    """Reset tokens must be delivered by email, never returned in the API response
    (CODE_REVIEW.md finding C1). In tests, RESET_TOKEN_DEBUG_ECHO=1 logs the token
    so the flow can be completed end-to-end without an SMTP server."""
    import logging

    clear_rate_limits()

    # 1. Request reset token
    forgot_res = await client.post(
        "/api/auth/forgot-password",
        json={"email": auth_user["email"]},
    )
    assert forgot_res.status_code == 200
    forgot_data = forgot_res.json()

    # The response must NOT contain the token anymore (security regression guard)
    assert "reset_token" not in forgot_data
    assert forgot_data["message"]  # generic message present

    # The token IS delivered via the email subsystem (echoed to logs in dev mode)
    with caplog.at_level(logging.WARNING, logger="clip_studio.email"):
        pass
    tokens = [
        rec.getMessage().split(": ", 1)[1].split(" (dev only")[0]
        for rec in caplog.records
        if "reset token for" in rec.getMessage()
    ]
    assert tokens, "expected reset token to be emitted via email subsystem"
    token = tokens[-1]
    assert token

    # 2. Reset password with new password
    new_password = "BrandNewSecurePassword2026!"
    reset_res = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "new_password": new_password},
    )
    assert reset_res.status_code == 200
    assert "successfully reset" in reset_res.json()["message"].lower()

    # 3. Old password must now fail
    old_login = await client.post(
        "/api/auth/login",
        json={"email_or_username": auth_user["email"], "password": auth_user["password"]},
    )
    assert old_login.status_code == 401

    # 4. New password must succeed
    new_login = await client.post(
        "/api/auth/login",
        json={"email_or_username": auth_user["email"], "password": new_password},
    )
    assert new_login.status_code == 200
    assert "token" in new_login.json()

    # 5. Reusing the same token must fail with 400
    reuse_res = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "new_password": "AnotherPassword123!"},
    )
    assert reuse_res.status_code == 400
    assert "invalid or expired" in reuse_res.json()["detail"].lower()

