from datetime import datetime, timedelta, timezone
import os
import re
import secrets
import uuid
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field

from ..database import get_db
from ..auth import (
    get_password_hash,
    verify_password,
    create_access_token,
    get_current_user,
    revoke_jti,
)
from ..utils.emailer import send_reset_email
from ..utils.rate_limit import check_rate_limit, get_client_ip

router = APIRouter(prefix="/api/auth", tags=["Auth"])

GENERIC_RESET_MESSAGE = (
    "If this email is registered, password reset instructions have been sent. "
    "Please check your inbox."
)

USERNAME_REGEX = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)

    @staticmethod
    def validate_username(value: str) -> str:
        if not USERNAME_REGEX.match(value):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username must be 3-32 characters using letters, numbers, dot, dash or underscore.",
            )
        return value


class LoginRequest(BaseModel):
    email_or_username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    message: str


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=16, max_length=255)
    new_password: str = Field(min_length=8, max_length=128)


class ResetPasswordResponse(BaseModel):
    message: str


class LogoutResponse(BaseModel):
    message: str


class UserResponse(BaseModel):
    id: int
    email: str
    username: str
    tier: str


class AuthResponse(BaseModel):
    token: str
    user: UserResponse


def _check_password_strength(password: str) -> None:
    """Minimal complexity policy to complement the length rule from Pydantic."""
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one letter and one number.",
        )


@router.post("/register", response_model=AuthResponse)
async def register(req: RegisterRequest, request: Request) -> AuthResponse:
    # Rate limit registration attempts by IP
    client_ip = get_client_ip(request)
    max_reg = int(os.getenv("MAX_REGISTRATIONS_PER_10MIN", "3"))
    await check_rate_limit(
        key=f"register:{client_ip}",
        max_requests=max_reg,
        window_seconds=600,
        error_message="Too many registration attempts. Please try again in 10 minutes.",
    )

    username = RegisterRequest.validate_username(req.username.strip())
    _check_password_strength(req.password)

    async with await get_db() as db:
        # Check if email or username already exists
        cursor = await db.execute(
            "SELECT id FROM users WHERE email = ? OR username = ?",
            (req.email.lower(), username.lower()),
        )
        existing = await cursor.fetchone()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email or username already registered",
            )

        hashed = get_password_hash(req.password)
        cursor = await db.execute(
            "INSERT INTO users (email, username, hashed_password, tier, token_version) VALUES (?, ?, ?, ?, 0)",
            (req.email.lower(), username, hashed, "pro"),
        )
        await db.commit()
        user_id = cursor.lastrowid

    token = create_access_token({"sub": user_id})
    return AuthResponse(
        token=token,
        user=UserResponse(id=user_id, email=req.email.lower(), username=username, tier="pro"),
    )


@router.post("/login", response_model=AuthResponse)
async def login(req: LoginRequest, request: Request) -> AuthResponse:
    # Rate limit login attempts by IP
    client_ip = get_client_ip(request)
    max_login = int(os.getenv("MAX_LOGIN_ATTEMPTS_PER_MIN", "5"))
    await check_rate_limit(
        key=f"login_ip:{client_ip}",
        max_requests=max_login,
        window_seconds=60,
        error_message="Too many login attempts. Please wait a minute before trying again.",
    )

    query = req.email_or_username.lower().strip()
    async with await get_db() as db:
        cursor = await db.execute(
            "SELECT id, email, username, hashed_password, tier, token_version FROM users WHERE email = ? OR username = ?",
            (query, query),
        )
        user = await cursor.fetchone()

    if not user or not verify_password(req.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username/email or password",
        )

    user = dict(user)  # sqlite3.Row does not support .get()
    token = create_access_token({"sub": user["id"]}, token_version=int(user.get("token_version") or 0))
    return AuthResponse(
        token=token,
        user=UserResponse(id=user["id"], email=user["email"], username=user["username"], tier=user["tier"]),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(user: Dict[str, Any] = Depends(get_current_user)) -> UserResponse:
    return UserResponse(
        id=user["id"],
        email=user["email"],
        username=user["username"],
        tier=user["tier"],
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user),
) -> LogoutResponse:
    """Revoke the presented JWT server-side so it can no longer be used."""
    from ..auth import SECRET_KEY, ALGORITHM  # local import avoids circulars in tests

    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    try:
        payload: Dict[str, Any] = {}
        import jwt as pyjwt

        payload = pyjwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        jti = payload.get("jti")
        exp = payload.get("exp")
        expires_at = (
            datetime.fromtimestamp(float(exp), tz=timezone.utc)
            if exp
            else datetime.now(timezone.utc) + timedelta(days=1)
        )
        if jti:
            await revoke_jti(str(jti), user["id"], expires_at)
    except Exception:
        # Token already expired or malformed; nothing to revoke.
        pass

    return LogoutResponse(message="Logged out successfully.")


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(req: ForgotPasswordRequest, request: Request) -> ForgotPasswordResponse:
    client_ip = get_client_ip(request)
    # Rate limit reset requests to 3 per 10 minutes
    await check_rate_limit(
        key=f"forgot_pass:{client_ip}",
        max_requests=3,
        window_seconds=600,
        error_message="Too many password reset requests. Please try again in 10 minutes.",
    )

    clean_email = req.email.lower().strip()
    async with await get_db() as db:
        cursor = await db.execute(
            "SELECT id, email FROM users WHERE email = ?",
            (clean_email,),
        )
        user = await cursor.fetchone()

        if not user:
            # Generic response to prevent user enumeration; no token is returned.
            return ForgotPasswordResponse(message=GENERIC_RESET_MESSAGE)

        user_id = user["id"]
        # Generate 32-byte secure urlsafe token; delivered only by email.
        reset_token = secrets.token_urlsafe(32)
        reset_id = f"rst_{uuid.uuid4().hex[:10]}"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)

        # Invalidate previous unused tokens for this user
        await db.execute(
            "UPDATE password_resets SET used = 1 WHERE user_id = ? AND used = 0",
            (user_id,),
        )

        await db.execute(
            """
            INSERT INTO password_resets (id, user_id, token, expires_at, used)
            VALUES (?, ?, ?, ?, 0)
            """,
            (reset_id, user_id, reset_token, expires_at.isoformat()),
        )
        await db.commit()

    # Deliver out-of-band. The token must never travel back in the HTTP
    # response — see CODE_REVIEW.md finding C1.
    await send_reset_email(clean_email, reset_token)

    return ForgotPasswordResponse(message=GENERIC_RESET_MESSAGE)


@router.post("/reset-password", response_model=ResetPasswordResponse)
async def reset_password(req: ResetPasswordRequest, request: Request) -> ResetPasswordResponse:
    client_ip = get_client_ip(request)
    await check_rate_limit(
        key=f"reset_pass:{client_ip}",
        max_requests=5,
        window_seconds=300,
        error_message="Too many password reset attempts. Please try again later.",
    )

    _check_password_strength(req.new_password)

    clean_token = req.token.strip()

    async with await get_db() as db:
        cursor = await db.execute(
            """
            SELECT id, user_id, expires_at, used
            FROM password_resets
            WHERE token = ? AND used = 0
            """,
            (clean_token,),
        )
        reset_entry = await cursor.fetchone()

        if not reset_entry:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token. Please request a new one.",
            )

        # Verify expiration
        exp_str = reset_entry["expires_at"]
        try:
            if isinstance(exp_str, str):
                exp_dt = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
            else:
                exp_dt = exp_str
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        except Exception:
            exp_dt = datetime.now(timezone.utc)

        if datetime.now(timezone.utc) > exp_dt:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reset token has expired. Please request a new one.",
            )

        user_id = reset_entry["user_id"]
        hashed = get_password_hash(req.new_password)

        # Update user's password and bump token_version so every previously
        # issued JWT for this account is rejected from now on.
        await db.execute(
            "UPDATE users SET hashed_password = ?, token_version = COALESCE(token_version, 0) + 1 WHERE id = ?",
            (hashed, user_id),
        )

        # Mark token as used
        await db.execute(
            "UPDATE password_resets SET used = 1 WHERE id = ?",
            (reset_entry["id"],),
        )
        await db.commit()

    return ResetPasswordResponse(
        message="Your password has been successfully reset! You can now log in with your new password.",
    )
