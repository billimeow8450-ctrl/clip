from __future__ import annotations

import os
import time
import asyncio
from collections import defaultdict, deque
from typing import Dict, Deque
from fastapi import Request, HTTPException, status

_WINDOW_STORAGE: Dict[str, Deque[float]] = defaultdict(deque)
_LOCK = asyncio.Lock()


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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
