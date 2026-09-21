from __future__ import annotations

import os
import time
import asyncio
from collections import defaultdict, deque
from typing import Dict, Deque, Optional
from fastapi import Request, HTTPException, status

_WINDOW_STORAGE: Dict[str, Deque[float]] = defaultdict(deque)
_LOCK = asyncio.Lock()


def get_client_ip(request: Request) -> str:
    """Resolve the client IP for rate-limiting.

    Finds the right-most X-Forwarded-For entry NOT in the trusted proxy
    list (CODE_REVIEW.md finding M3): left-most entries are client-controlled
    and trivially spoofable. When TRUSTED_PROXY_COUNT is unset, only a
    direct connection's remote address is trusted.
    """
    remote = request.client.host if request.client else "unknown"

    trusted_proxy_count = os.getenv("TRUSTED_PROXY_COUNT", "").strip()
    if not trusted_proxy_count:
        # Conservative default: ignore XFF entirely unless explicitly configured.
        return remote

    try:
        n = int(trusted_proxy_count)
    except ValueError:
        return remote
    if n <= 0:
        return remote

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return remote

    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    if not hops:
        return remote

    # Walk from the right-most hop leftwards past trusted proxies.
    idx = len(hops) - n
    if idx < 0:
        return hops[0]
    return hops[idx]


async def check_rate_limit(
    key: str,
    max_requests: int,
    window_seconds: int,
    error_message: str = "Too many requests. Please try again later.",
) -> None:
    now = time.time()
    cutoff = now - window_seconds

    async with _LOCK:
        timestamps = _WINDOW_STORAGE[key]
        # Prune expired timestamps
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

        if len(timestamps) >= max_requests:
            retry_after = int(window_seconds - (now - timestamps[0])) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=error_message,
                headers={"Retry-After": str(max(1, retry_after))},
            )

        timestamps.append(now)


def clear_rate_limits() -> None:
    """Helper for unit tests to reset in-memory limits."""
    _WINDOW_STORAGE.clear()
