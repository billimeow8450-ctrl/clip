"""Transactional email delivery for password resets.

Transports, in priority order:
1. RESEND_API_KEY  — Resend HTTP API (https://resend.com). Recommended: no SMTP
   ports to open, works out of the box on Render. Sender defaults to
   ``onboarding@resend.dev`` (Resend sandbox: delivers ONLY to your own Resend
   account's email address) until you verify a domain and set EMAIL_FROM.
2. SMTP_HOST/SMTP_USER/SMTP_PASSWORD — any classic SMTP provider
   (SendGrid, Postmark, SES, Mailgun...). Use port 587 + STARTTLS.
3. RESET_TOKEN_DEBUG_ECHO=1 — dev only: the token is printed to server logs
   instead of being emailed. NEVER enable in production.

If no transport is configured the caller falls back to a generic
"check your inbox" response (no token ever leaks through the API).
"""
from __future__ import annotations

import asyncio
import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Optional

logger = logging.getLogger("clip_studio.email")

RESEND_API_URL = "https://api.resend.com/emails"


def _app_base_url() -> str:
    return os.getenv("APP_BASE_URL", "http://localhost:5173").rstrip("/")


def _from_address() -> str:
    explicit = (os.getenv("EMAIL_FROM") or os.getenv("SMTP_FROM") or "").strip()
    if explicit:
        return explicit
    # Resend sandbox sender — works without domain verification but only
    # delivers to the address that owns the Resend account.
    if os.getenv("RESEND_API_KEY", "").strip():
        return "onboarding@resend.dev"
    return "no-reply@localhost"


def _reset_link(reset_token: str) -> str:
    # Query string must come BEFORE the hash fragment: the SPA uses hash
    # routing and AuthPage reads ?reset_token= to prefill the reset form.
    return f"{_app_base_url()}/?reset_token={reset_token}#/"


def _build_text_body(to_email: str, reset_token: str) -> str:
    return (
        "We received a request to reset your password.\n\n"
        f"Reset link (valid for 30 minutes):\n{_reset_link(reset_token)}\n\n"
        f"Or paste this one-time token into the reset form:\n{reset_token}\n\n"
        "If you didn't request this, you can safely ignore this email — "
        "your password will not change."
    )


async def _send_via_resend(to_email: str, reset_token: str) -> bool:
    import httpx

    api_key = os.getenv("RESEND_API_KEY", "").strip()
    payload = {
        "from": _from_address(),
        "to": [to_email],
        "subject": "Reset your Clip Studio password",
        "text": _build_text_body(to_email, reset_token),
    }

    def _post() -> httpx.Response:
        with httpx.Client(timeout=15) as client:
            return client.post(
                RESEND_API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )

    try:
        response = await asyncio.to_thread(_post)
        if response.status_code >= 400:
            logger.error(
                "Resend API error for %s: HTTP %s %s",
                to_email,
                response.status_code,
                response.text[:500],
            )
            return False
        return True
    except Exception:
        logger.exception("Failed to send reset email via Resend to %s", to_email)
        return False


async def _send_via_smtp(to_email: str, reset_token: str) -> bool:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()

    msg = EmailMessage()
    msg["Subject"] = "Reset your Clip Studio password"
    msg["From"] = _from_address()
    msg["To"] = to_email
    msg.set_content(_build_text_body(to_email, reset_token))

    def _send() -> None:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.ehlo()
            try:
                server.starttls()
                server.ehlo()
            except smtplib.SMTPNotSupportedError:
                pass  # e.g. local relay without STARTTLS
            if user and password:
                server.login(user, password)
            server.send_message(msg)

    try:
        await asyncio.to_thread(_send)
        return True
    except Exception:
        logger.exception("Failed to send reset email via SMTP to %s", to_email)
        return False


def _debug_echo(to_email: str, reset_token: str) -> bool:
    logger.warning(
        "RESET_TOKEN_DEBUG_ECHO=1 — reset token for %s: %s "
        "(dev only; never enable in production)",
        to_email,
        reset_token,
    )
    return True


async def send_reset_email(to_email: str, reset_token: str) -> bool:
    """Send the password-reset email. Returns True when delivery was attempted
    successfully, False when no transport is configured or delivery failed."""
    resend_key = os.getenv("RESEND_API_KEY", "").strip()
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    debug = os.getenv("RESET_TOKEN_DEBUG_ECHO", "").strip() == "1"

    if not resend_key and not smtp_host:
        if debug:
            return _debug_echo(to_email, reset_token)
        logger.warning(
            "No email transport configured (set RESEND_API_KEY or SMTP_HOST); "
            "reset email for %s was not sent.",
            to_email,
        )
        return False

    if resend_key:
        return await _send_via_resend(to_email, reset_token)
    return await _send_via_smtp(to_email, reset_token)


def email_transport_name() -> Optional[str]:
    """Which transport would be used right now ('resend', 'smtp', 'debug', None)."""
    if os.getenv("RESEND_API_KEY", "").strip():
        return "resend"
    if os.getenv("SMTP_HOST", "").strip():
        return "smtp"
    if os.getenv("RESET_TOKEN_DEBUG_ECHO", "").strip() == "1":
        return "debug"
    return None
