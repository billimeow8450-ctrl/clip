from datetime import datetime, timedelta, timezone
import os
import secrets
import uuid
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr

from ..database import get_db
from ..auth import get_password_hash, verify_password, create_access_token, get_current_user
from ..utils.rate_limit import check_rate_limit, get_client_ip

router = APIRouter(prefix="/api/auth", tags=["Auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str
    password: str


class LoginRequest(BaseModel):
    email_or_username: str
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    message: str
    reset_token: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class ResetPasswordResponse(BaseModel):
    message: str


class UserResponse(BaseModel):
    id: int
    email: str
    username: str
    tier: str


class AuthResponse(BaseModel):
    token: str
    user: UserResponse


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

    async with await get_db() as db:
        # Check if email or username already exists
        cursor = await db.execute(
            "SELECT id FROM users WHERE email = ? OR username = ?",
            (req.email.lower(), req.username.lower()),
        )
        existing = await cursor.fetchone()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email or username already registered",
            )

        hashed = get_password_hash(req.password)
        cursor = await db.execute(
            "INSERT INTO users (email, username, hashed_password, tier) VALUES (?, ?, ?, ?)",
            (req.email.lower(), req.username, hashed, "pro"),
        )
        await db.commit()
        user_id = cursor.lastrowid

    token = create_access_token({"sub": user_id})
    return AuthResponse(
        token=token,
        user=UserResponse(id=user_id, email=req.email.lower(), username=req.username, tier="pro"),
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
            "SELECT id, email, username, hashed_password, tier FROM users WHERE email = ? OR username = ?",
            (query, query),
        )
        user = await cursor.fetchone()

    if not user or not verify_password(req.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username/email or password",
        )

    token = create_access_token({"sub": user["id"]})
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
            # Generic response to prevent user enumeration
            return ForgotPasswordResponse(
                message="If this email is registered, password reset instructions have been generated.",
                reset_token=None,
            )

        user_id = user["id"]
        # Generate 32-byte secure urlsafe token
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

    return ForgotPasswordResponse(
        message="Password reset instructions generated successfully. Token is valid for 30 minutes.",
        reset_token=reset_token,
    )


@router.post("/reset-password", response_model=ResetPasswordResponse)
async def reset_password(req: ResetPasswordRequest, request: Request) -> ResetPasswordResponse:
    client_ip = get_client_ip(request)
    await check_rate_limit(
        key=f"reset_pass:{client_ip}",
        max_requests=5,
        window_seconds=300,
        error_message="Too many password reset attempts. Please try again later.",
    )

    if not req.new_password or len(req.new_password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be at least 6 characters long.",
        )

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

        # Update user's password
        await db.execute(
            "UPDATE users SET hashed_password = ? WHERE id = ?",
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
