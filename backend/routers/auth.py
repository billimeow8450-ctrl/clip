from __future__ import annotations

import os
from typing import Dict, Any
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
