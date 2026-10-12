from __future__ import annotations

import math
import os
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import Header, HTTPException, Request

from app.security_baseline import is_production_env, parse_cors_allow_origins

PRO_CLIENT_HEADER_NAME = "X-OFX-Pro-Client"
PRO_CLIENT_HEADER_VALUE = "web"


def _allowed_origins() -> set[str]:
    configured = os.getenv("CORS_ALLOW_ORIGINS", "").strip()
    if configured:
        return {origin.rstrip("/") for origin in parse_cors_allow_origins(configured)}
    if is_production_env():
        return set()
    return {"http://localhost:3000", "http://127.0.0.1:3000"}


def require_pro_browser_request(
    request: Request,
    pro_client: str | None = Header(default=None, alias=PRO_CLIENT_HEADER_NAME),
) -> None:
    if (pro_client or "").strip() != PRO_CLIENT_HEADER_VALUE:
        raise HTTPException(status_code=403, detail="Invalid PRO client request.")

    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if not origin or origin not in _allowed_origins():
        raise HTTPException(status_code=403, detail="Origin is not allowed for PRO writes.")


class ProSessionRateLimiter:
    def __init__(
        self,
        *,
        limit: int,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = max(1, int(limit))
        self.window_seconds = max(1, int(window_seconds))
        self._clock = clock
        self._entries: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def enforce(self, key: str) -> None:
        now = self._clock()
        boundary = now - self.window_seconds
        normalized_key = (key or "unknown").strip() or "unknown"
        with self._lock:
            entries = self._entries[normalized_key]
            while entries and entries[0] <= boundary:
                entries.popleft()
            if len(entries) >= self.limit:
                retry_after = max(1, math.ceil(self.window_seconds - (now - entries[0])))
                raise HTTPException(
                    status_code=429,
                    detail="Muitas tentativas de criação de sessão.",
                    headers={"Retry-After": str(retry_after)},
                )
            entries.append(now)


_pro_session_rate_limiter = ProSessionRateLimiter(
    limit=int(os.getenv("PRO_ANONYMOUS_SESSION_RATE_LIMIT", "10")),
    window_seconds=int(os.getenv("PRO_ANONYMOUS_SESSION_RATE_WINDOW_SECONDS", "60")),
)


def get_pro_session_rate_limiter() -> ProSessionRateLimiter:
    return _pro_session_rate_limiter


def pro_session_rate_limit_key(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


__all__ = [
    "PRO_CLIENT_HEADER_NAME",
    "PRO_CLIENT_HEADER_VALUE",
    "ProSessionRateLimiter",
    "get_pro_session_rate_limiter",
    "pro_session_rate_limit_key",
    "require_pro_browser_request",
]
