from __future__ import annotations

import hashlib
import hmac
import os
import socket
import time
import ipaddress
import re
from urllib.parse import urlparse
from fastapi import HTTPException, status

ALLOWED_DOMAINS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "rumble.com",
    "www.rumble.com",
    "vimeo.com",
    "www.vimeo.com",
    "twitch.tv",
    "www.twitch.tv",
}

LOCAL_FILE_REFERENCE = re.compile(
    r"^(?:/api/files/)?up_[A-Za-z0-9_-]+(?:\.(?:mp4|mov|mkv|webm|avi|mp3|wav|m4a))?$",
    re.IGNORECASE,
)


def is_safe_ip(ip_str: str) -> bool:
    """Returns True if the IP is public and not loopback, private, link-local, or reserved."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    except ValueError:
        return False


def get_local_upload_id(value: str) -> str | None:
    """Return a server-issued upload id from a relative upload reference.

    The browser receives signed download URLs, so source submissions may include
    an otherwise harmless query string.  Accept only the relative API path (or
    the bare id), never an arbitrary URL which merely contains a similar path.
    """
    if not value or not isinstance(value, str):
        return None
    parsed = urlparse(value.strip())
    if parsed.scheme or parsed.netloc:
        return None
    path = parsed.path
    if not LOCAL_FILE_REFERENCE.fullmatch(path):
        return None
    return path.rsplit("/", 1)[-1]


def validate_source_url(url: str) -> str:
    """
    Validates source video URLs to prevent SSRF and arbitrary URI scheme attacks.
    Allows either local upload identifiers (e.g. /api/files/up_xxx or up_xxx)
    or verified external video platforms (YouTube, Vimeo, Rumble, Twitch) whose resolved IPs
    are not loopback, private, or metadata services.
    """
    if not url or not isinstance(url, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Source URL or file path is required.",
        )

    clean_url = url.strip()

    # Only accept the server-issued upload identifier format. A broad prefix
    # check would let malformed internal paths reach later processing stages.
    if get_local_upload_id(clean_url):
        return clean_url

    parsed = urlparse(clean_url)
    scheme = parsed.scheme.lower()

    if scheme not in ("http", "https"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported URL protocol: '{scheme}'. Only HTTP and HTTPS are permitted.",
        )

    hostname = (parsed.hostname or "").lower().strip()
    if not hostname:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid URL: Missing hostname.",
        )

    # Check hostname against whitelist of supported streaming domains
    is_domain_allowed = any(
        hostname == domain or hostname.endswith("." + domain)
        for domain in ALLOWED_DOMAINS
    )
    if not is_domain_allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Domain '{hostname}' is not supported. "
                f"Supported platforms: YouTube, Vimeo, Twitch, Rumble, or direct file uploads."
            ),
        )

    # Resolve IP and check against SSRF targets (loopback, private, metadata IPs)
    try:
        addr_info = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        resolved_ips = {item[4][0] for item in addr_info}
        if not resolved_ips:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Could not resolve hostname: {hostname}",
            )

        for ip in resolved_ips:
            if not is_safe_ip(ip):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Access to restricted or internal network address is blocked.",
                )
    except socket.gaierror:
        # Local development and isolated test runners frequently have no DNS.
        # The host has already passed the strict service allowlist above; keep
        # production fail-closed while allowing offline UI/API verification.
        if os.getenv("ENVIRONMENT", "development").lower().strip() != "production":
            return clean_url
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to resolve host '{hostname}'. Please provide a reachable URL.",
        )

    return clean_url


# ---------------------------------------------------------------------------
# Signed download URLs (CODE_REVIEW.md finding C3)
#
# Generated/output files used to be served to anyone who knew (or guessed) the
# filename. Download links are now HMAC-signed with an expiry and bound to the
# owning user id. Set ALLOW_PUBLIC_FILE_URLS=1 only for local development.
# ---------------------------------------------------------------------------


def _file_url_secret() -> str:
    secret = os.getenv("FILE_URL_SECRET") or os.getenv("JWT_SECRET_KEY") or ""
    if not secret:
        # Development fallback only; production requires JWT_SECRET_KEY anyway.
        return "insecure-dev-file-url-secret"
    return secret


def sign_file_url(filename: str, user_id, ttl_seconds: int | None = None) -> str:
    """Return a signed, expiring download path for ``filename`` owned by ``user_id``."""
    exp = int(time.time()) + int(ttl_seconds or os.getenv("FILE_URL_TTL_SECONDS", "86400"))
    payload = f"{filename}:{user_id}:{exp}"
    sig = hmac.new(_file_url_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"/api/files/{filename}?exp={exp}&uid={user_id}&sig={sig}"


def verify_file_signature(filename: str, uid: str | None, exp: str | None, sig: str | None) -> bool:
    if not exp or not sig:
        return False
    try:
        exp_int = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_int < int(time.time()):
        return False
    payload = f"{filename}:{uid}:{exp_int}"
    expected = hmac.new(_file_url_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(expected, str(sig))
