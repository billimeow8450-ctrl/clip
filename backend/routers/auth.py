from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import re
import secrets
import uuid
from typing import Dict, Any
from urllib.parse import urlencode
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field

from ..database import get_db, adapt_timestamp, _parse_db_timestamp
from ..auth import (
    SECRET_KEY,
    get_password_hash,
    verify_password,
    create_access_token,
    get_current_user,
    is_admin_user,
    revoke_jti,
    reset_token_fingerprint,
    DUMMY_PASSWORD_HASH,
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
    is_admin: bool = False


class AuthResponse(BaseModel):
    token: str
    user: UserResponse


class GoogleCodeRequest(BaseModel):
    code: str = Field(min_length=32, max_length=255)


class ProvidersResponse(BaseModel):
    google: bool


def _google_config() -> tuple[str, str, str]:
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
    if not client_id or not client_secret or not redirect_uri:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured yet. Please use email sign-in.",
        )
    return client_id, client_secret, redirect_uri


def _sign_google_state(timestamp: int, nonce: str) -> str:
    payload = f"{timestamp}.{nonce}"
    signature = hmac.new(SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _validate_google_state(state_value: str) -> bool:
    parts = state_value.split(".")
    if len(parts) != 3 or not parts[0].isdigit():
        return False
    timestamp, nonce, signature = parts
    if len(nonce) < 16 or datetime.now(timezone.utc).timestamp() - int(timestamp) > 600:
        return False
    expected = _sign_google_state(int(timestamp), nonce)
    return hmac.compare_digest(expected, state_value)


def _oauth_code_hash(code: str) -> str:
    return hmac.new(SECRET_KEY.encode("utf-8"), code.encode("utf-8"), hashlib.sha256).hexdigest()


async def _unique_oauth_username(db, email: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9_.-]", "", email.split("@", 1)[0])[:24] or "googleuser"
    base = base if len(base) >= 3 else f"user{base}"
    for attempt in range(100):
        candidate = base if attempt == 0 else f"{base[:26]}{attempt:02d}"
        cursor = await db.execute("SELECT 1 FROM users WHERE username = ?", (candidate.lower(),))
        if not await cursor.fetchone():
            return candidate
    return f"google_{secrets.token_hex(8)}"


def _auth_response(user: Dict[str, Any]) -> AuthResponse:
    token = create_access_token({"sub": user["id"]}, token_version=int(user.get("token_version") or 0))
    return AuthResponse(
        token=token,
        user=UserResponse(
            id=user["id"], email=user["email"], username=user["username"],
            tier=user["tier"], is_admin=is_admin_user(user),
        ),
    )


def _check_password_strength(password: str) -> None:
    """Minimal complexity policy to complement the length rule from Pydantic."""
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one letter and one number.",
        )
    if len(password.encode("utf-8")) > 72:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at most 72 bytes.")


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
            (req.email.lower(), username, hashed, "free"),
        )
        await db.commit()
        user_id = cursor.lastrowid

    token = create_access_token({"sub": user_id})
    return AuthResponse(
        token=token,
        user=UserResponse(id=user_id, email=req.email.lower(), username=username, tier="free", is_admin=is_admin_user({"email": req.email})),
    )


@router.get("/providers", response_model=ProvidersResponse)
async def providers() -> ProvidersResponse:
    """Expose only provider availability, never OAuth client credentials."""
    return ProvidersResponse(
        google=bool(
            os.getenv("GOOGLE_CLIENT_ID", "").strip()
            and os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
            and os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
        )
    )


@router.get("/google/start")
async def google_start() -> RedirectResponse:
    client_id, _, redirect_uri = _google_config()
    state_value = _sign_google_state(int(datetime.now(timezone.utc).timestamp()), secrets.token_urlsafe(24))
    params = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state_value,
        "prompt": "select_account",
    })
    response = RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}", status_code=302)
    response.set_cookie(
        "clip_google_oauth_state", state_value, max_age=600, httponly=True,
        secure=True, samesite="lax", path="/api/auth/google",
    )
    return response


@router.get("/google/callback")
async def google_callback(code: str, state: str, request: Request) -> RedirectResponse:
    client_id, client_secret, redirect_uri = _google_config()
    cookie_state = request.cookies.get("clip_google_oauth_state", "")
    if not cookie_state or not hmac.compare_digest(cookie_state, state) or not _validate_google_state(state):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google sign-in session expired. Please try again.")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token_response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            token_response.raise_for_status()
            id_token_value = token_response.json().get("id_token")
        if not id_token_value:
            raise ValueError("Google did not return an identity token")
        from google.oauth2 import id_token
        from google.auth.transport import requests as google_requests
        claims = id_token.verify_oauth2_token(id_token_value, google_requests.Request(), client_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Google sign-in could not be verified. Please try again.") from exc

    email = str(claims.get("email", "")).lower().strip()
    subject = str(claims.get("sub", "")).strip()
    if not email or not subject or claims.get("email_verified") is not True:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Google account must have a verified email address.")

    async with await get_db() as db:
        cursor = await db.execute(
            "SELECT id, email, username, tier, token_version, oauth_provider, oauth_subject FROM users WHERE oauth_provider = ? AND oauth_subject = ?",
            ("google", subject),
        )
        user = await cursor.fetchone()
        if not user:
            cursor = await db.execute(
                "SELECT id, email, username, tier, token_version, oauth_provider, oauth_subject FROM users WHERE email = ?",
                (email,),
            )
            user = await cursor.fetchone()
            if user:
                # A verified Google email may safely link to its matching local account.
                await db.execute(
                    "UPDATE users SET oauth_provider = ?, oauth_subject = ? WHERE id = ?",
                    ("google", subject, user["id"]),
                )
            else:
                username = await _unique_oauth_username(db, email)
                cursor = await db.execute(
                    "INSERT INTO users (email, username, hashed_password, oauth_provider, oauth_subject, tier, token_version) VALUES (?, ?, ?, ?, ?, ?, 0)",
                    (email, username, get_password_hash(secrets.token_urlsafe(48)), "google", subject, "free"),
                )
                user = {"id": cursor.lastrowid, "email": email, "username": username, "tier": "free", "token_version": 0}
            await db.commit()
        user = dict(user)

        one_time_code = secrets.token_urlsafe(32)
        await db.execute("DELETE FROM oauth_login_codes WHERE expires_at < ? OR used = 1", (adapt_timestamp(datetime.now(timezone.utc)),))
        await db.execute(
            "INSERT INTO oauth_login_codes (id, user_id, code_hash, expires_at, used) VALUES (?, ?, ?, ?, 0)",
            (f"oauth_{uuid.uuid4().hex[:18]}", user["id"], _oauth_code_hash(one_time_code), adapt_timestamp(datetime.now(timezone.utc) + timedelta(minutes=2))),
        )
        await db.commit()

    app_base = os.getenv("APP_BASE_URL", "").rstrip("/")
    if not app_base.startswith("https://"):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Google sign-in is not configured for a secure application URL.")
    response = RedirectResponse(f"{app_base}/?oauth_code={one_time_code}#/", status_code=302)
    response.delete_cookie("clip_google_oauth_state", path="/api/auth/google")
    return response


@router.post("/google/exchange", response_model=AuthResponse)
async def exchange_google_code(req: GoogleCodeRequest) -> AuthResponse:
    code_hash = _oauth_code_hash(req.code)
    async with await get_db() as db:
        cursor = await db.execute(
            "SELECT id, user_id, expires_at, used FROM oauth_login_codes WHERE code_hash = ?",
            (code_hash,),
        )
        login_code = await cursor.fetchone()
        if not login_code or login_code["used"]:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google sign-in link is invalid or has already been used.")
        expires_at = _parse_db_timestamp(login_code["expires_at"])
        if not expires_at or expires_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google sign-in link has expired. Please try again.")
        await db.execute("UPDATE oauth_login_codes SET used = 1 WHERE id = ?", (login_code["id"],))
        cursor = await db.execute(
            "SELECT id, email, username, tier, token_version FROM users WHERE id = ?",
            (login_code["user_id"],),
        )
        user = await cursor.fetchone()
        await db.commit()
    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google account is no longer available.")
    return _auth_response(dict(user))


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

    # Perform the same bcrypt work for an unknown account to avoid making
    # account existence measurable from login response time.
    password_hash = user["hashed_password"] if user else DUMMY_PASSWORD_HASH
    if not user or not verify_password(req.password, password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username/email or password",
        )

    user = dict(user)  # sqlite3.Row does not support .get()
    token = create_access_token({"sub": user["id"]}, token_version=int(user.get("token_version") or 0))
    return AuthResponse(
        token=token,
        user=UserResponse(id=user["id"], email=user["email"], username=user["username"], tier=user["tier"], is_admin=is_admin_user(user)),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(user: Dict[str, Any] = Depends(get_current_user)) -> UserResponse:
    return UserResponse(
        id=user["id"],
        email=user["email"],
        username=user["username"],
        tier=user["tier"],
        is_admin=is_admin_user(user),
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
        expires_at = adapt_timestamp(datetime.now(timezone.utc) + timedelta(minutes=30))

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
            (reset_id, user_id, reset_token_fingerprint(reset_token), expires_at),
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
            (reset_token_fingerprint(clean_token),),
        )
        reset_entry = await cursor.fetchone()

        if not reset_entry:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token. Please request a new one.",
            )

        # Verify expiration (Postgres returns datetime, SQLite returns ISO str)
        try:
            exp_dt = _parse_db_timestamp(reset_entry["expires_at"])
            if exp_dt is None:
                raise ValueError("unparseable expires_at")
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
