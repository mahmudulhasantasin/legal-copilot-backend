"""
In-memory sliding-window rate limiter (SEC-AUTH-01).

Suitable for the current single-process deployment (Render free tier runs one
uvicorn worker). Limits reset on restart — acceptable for OTP/email abuse
protection, which only needs short windows.

Not a distributed limiter: if the app ever scales to multiple workers,
replace with a shared store (e.g. Redis). Keys are trimmed lazily on access;
long-running instances with many distinct keys keep small per-key deques.
"""

import threading
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import HTTPException

_WINDOWS: dict[str, deque[float]] = defaultdict(deque)
_LOCK = threading.Lock()


def client_ip(request) -> str:
    """Best-effort client IP: honor Render/reverse-proxy X-Forwarded-For first."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(
    key: str,
    max_requests: int,
    window_seconds: float,
    detail: Optional[str] = None,
) -> None:
    """Raise HTTP 429 when `key` exceeds `max_requests` within `window_seconds`."""
    now = time.monotonic()
    with _LOCK:
        dq = _WINDOWS[key]
        cutoff = now - window_seconds
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if len(dq) >= max_requests:
            raise HTTPException(
                429,
                detail or "Too many requests. Please try again in a minute.",
            )
        dq.append(now)
