"""rate_limit.py — G11. In-memory token-bucket rate limit.

Per ADR-053 §"The Registry": settings.rate_limit_unauthenticated /
rate_limit_user. v1 implementation is in-process; v1.1 should move to
Redis to survive multi-worker setups.

The /version, /health, /webhooks/stripe (Stripe needs to retry without
us throttling), and /.well-known/* endpoints are exempt — per MF1 they
must always answer.

Tests + dev environments are exempt via ENVIRONMENT=development.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

EXEMPT_PATHS = {"/version", "/health", "/health/full", "/api/v1/webhooks/stripe"}
_EXEMPT_PREFIXES = ("/.well-known/", "/docs", "/openapi.json")


def _parse_rate(spec: str) -> tuple[int, int]:
    """Parse '100/minute' -> (100, 60)."""
    m = re.match(r"^(\d+)/(\w+)$", spec.strip())
    if not m:
        return (100, 60)
    n = int(m.group(1))
    unit = m.group(2).lower()
    window = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}.get(unit, 60)
    return (n, window)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory sliding window: key by (auth_subject | client_ip)."""

    def __init__(self, app, *, unauth_rate: str, auth_rate: str, enabled: bool = True) -> None:
        super().__init__(app)
        self.enabled = enabled
        self.unauth_n, self.unauth_window = _parse_rate(unauth_rate)
        self.auth_n, self.auth_window = _parse_rate(auth_rate)
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def _key(self, request: Request) -> tuple[str, int, int]:
        auth = request.headers.get("authorization")
        if auth and auth.lower().startswith("bearer "):
            # Don't decode; just hash the token for the key.
            return (f"auth:{hash(auth)}", self.auth_n, self.auth_window)
        ip = request.client.host if request.client else "unknown"
        return (f"ip:{ip}", self.unauth_n, self.unauth_window)

    def _allow(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        now = time.monotonic()
        bucket = self._buckets[key]
        cutoff = now - window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return (False, int(bucket[0] + window - now))
        bucket.append(now)
        return (True, 0)

    async def dispatch(self, request: Request, call_next):
        if not self.enabled:
            return await call_next(request)
        path = request.url.path
        if path in EXEMPT_PATHS or any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)

        key, limit, window = self._key(request)
        ok, retry_after = self._allow(key, limit, window)
        if not ok:
            return Response(
                content='{"error":"rate_limit_exceeded"}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)
