from __future__ import annotations

import os
import secrets
import uuid
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from dotenv import load_dotenv
from .database import get_db

# Load environment variables from .env
load_dotenv()


def resolve_jwt_secret() -> str:
    raw_secret = os.getenv("JWT_SECRET_KEY")
    environment = os.getenv("ENVIRONMENT", "development").lower().strip()
    if not raw_secret or raw_secret == "super-secret-clip-studio-key-change-in-production-2026":
        if environment == "production":
            raise RuntimeError("CRITICAL SECURITY ERROR: JWT_SECRET_KEY environment variable is required in production mode!")
        return secrets.token_hex(32)
    return raw_secret


SECRET_KEY = resolve_jwt_secret()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7

import bcrypt

security = HTTPBearer()


def reset_token_fingerprint(token: str) -> str:
    """Return a keyed, non-reversible database representation of a reset token.

    Reset tokens are bearer credentials. Storing the raw token meant anyone
    with database read access could take over an account during its validity
    window. The HMAC is deterministic for lookup but cannot be used as the
    token itself.
    """
    return hmac.new(
        SECRET_KEY.encode("utf-8"), token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def is_admin_user(user: Dict[str, Any]) -> bool:
    """Use a server-side bootstrap allowlist; never trust a client role claim."""
    admin_emails = {
        email.strip().lower()
        for email in os.getenv("ADMIN_EMAILS", "").split(",")
        if email.strip()
    }
    return bool(admin_emails and str(user.get("email", "")).lower() in admin_emails)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not plain_password or not hashed_password:
        return False
    try:
        pwd_bytes = plain_password.encode("utf-8")[:72]
        hashed_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(pwd_bytes, hashed_bytes)
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    if not password:
        raise ValueError("Password cannot be empty")
    pwd_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
    token_version: int = 0,
) -> str:
    to_encode = data.copy()
    if "sub" in to_encode and to_encode["sub"] is not None:
        to_encode["sub"] = str(to_encode["sub"])
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({
        "exp": expire,
        # jti enables per-token revocation (logout); tv enables global
        # invalidation of a user's sessions (password reset).
        "jti": uuid.uuid4().hex,
        "tv": int(token_version or 0),
    })
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def revoke_jti(jti: str, user_id: Any, expires_at: datetime) -> None:
    """Revoke a single JWT by id. Expired revocation rows are pruned opportunistically."""
    from .database import adapt_timestamp

    now = datetime.now(timezone.utc)
    async with await get_db() as db:
        await db.execute("DELETE FROM revoked_tokens WHERE expires_at < ?", (adapt_timestamp(now),))
        await db.execute("DELETE FROM revoked_tokens WHERE jti = ?", (jti,))
        await db.execute(
            "INSERT INTO revoked_tokens (id, jti, user_id, expires_at) VALUES (?, ?, ?, ?)",
            (f"rvk_{uuid.uuid4().hex[:16]}", jti, user_id, adapt_timestamp(expires_at)),
        )
        await db.commit()


async def is_jti_revoked(jti: str) -> bool:
    async with await get_db() as db:
        cursor = await db.execute("SELECT 1 FROM revoked_tokens WHERE jti = ?", (jti,))
        return await cursor.fetchone() is not None


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if sub is None:
            raise credentials_exception
        user_id = int(sub) if str(sub).isdigit() else sub
        jti = payload.get("jti")
        token_version = int(payload.get("tv", 0) or 0)
    except jwt.PyJWTError:
        raise credentials_exception

    if jti and await is_jti_revoked(str(jti)):
        raise credentials_exception

    async with await get_db() as db:
        cursor = await db.execute(
            "SELECT id, email, username, tier, created_at, token_version FROM users WHERE id = ?",
            (user_id,),
        )
        user = await cursor.fetchone()
        if user is None:
            raise credentials_exception
        user = dict(user)
        if int(user.get("token_version") or 0) != token_version:
            # Password was reset after this token was issued.
            raise credentials_exception
        return user


async def get_optional_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False))) -> Optional[Dict[str, Any]]:
    """Resolve the user if a valid token is presented; None for anonymous callers.

    Only authentication failures are swallowed — a database outage must still
    surface as a 500 rather than silently looking like "not logged in".
    """
    if not credentials:
        return None
    try:
        return await get_current_user(credentials)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            return None
        raise
