"""Simple per-IP rate limiting (SPMS-SR-006).

Login, registration and payment endpoints use `Depends(rate_limit("login", 10))`:
the 11th request from the same IP within 60 seconds gets HTTP 429.
Counters live in memory, so each server instance limits on its own (fine for one instance; behind a load
balancer use a shared store such as Redis - the API itself stays stateless, see NF-005).
"""
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

_hits: dict[tuple[str, str], deque] = defaultdict(deque)
_lock = threading.Lock()


def reset() -> None:
    with _lock:
        _hits.clear()


def rate_limit(name: str, limit: int = 10, per_seconds: int = 60):
    """FastAPI dependency factory: allow `limit` requests per `per_seconds` per client IP."""

    def check(request: Request) -> None:
        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with _lock:
            window = _hits[(name, ip)]
            while window and now - window[0] >= per_seconds:
                window.popleft()
            if len(window) >= limit:
                retry = int(per_seconds - (now - window[0])) + 1
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later",
                                    headers={"Retry-After": str(retry)})
            window.append(now)

    return check
