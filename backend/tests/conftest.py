from __future__ import annotations

import os
import sys
from pathlib import Path
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# Ensure backend root is on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Force testing environment before importing backend modules
os.environ["ENVIRONMENT"] = "development"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-clip-studio-testing-32-bytes"
os.environ["ALLOWED_ORIGINS"] = "http://localhost:5173,http://127.0.0.1:5173"
os.environ["MAX_LOGIN_ATTEMPTS_PER_MIN"] = "5"
os.environ["MAX_REGISTRATIONS_PER_10MIN"] = "3"

import backend.database as db_module
from backend.main import app
from backend.database import init_db, get_db
from backend.auth import create_access_token, get_password_hash
from backend.utils.rate_limit import clear_rate_limits


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db(tmp_path, monkeypatch):
    """Isolates each test in a dedicated SQLite database and resets rate limits."""
    test_db = tmp_path / "test_clip_studio.db"
    monkeypatch.setattr(db_module, "DB_PATH", test_db)
    monkeypatch.setattr(db_module, "DB_DIR", tmp_path)
    clear_rate_limits()

    await init_db()
    yield
    clear_rate_limits()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def auth_user():
    """Creates a pre-seeded test user and returns user info + JWT token."""
    email = "creator@example.com"
    username = "clipcreator"
    password = "SuperSecurePassword2026!"
    hashed = get_password_hash(password)

    async with await get_db() as db:
        cursor = await db.execute(
            "INSERT INTO users (email, username, hashed_password, tier) VALUES (?, ?, ?, ?)",
            (email, username, hashed, "pro"),
        )
        await db.commit()
        user_id = cursor.lastrowid

    token = create_access_token({"sub": user_id})
    return {
        "id": user_id,
        "email": email,
        "username": username,
        "password": password,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }
