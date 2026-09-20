from __future__ import annotations

import socket
import ipaddress
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

    # Allow local uploaded files and internal paths
    if clean_url.startswith("/api/files/") or clean_url.startswith("up_"):
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to resolve host '{hostname}'. Please provide a reachable URL.",
        )

    return clean_url
