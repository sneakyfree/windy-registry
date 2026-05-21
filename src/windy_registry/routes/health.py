"""health.py — liveness / readiness endpoints.

`/health`        — fast, no dependencies. Must always answer.
`/health/full`   — readiness: probes DB + R2 + Eternitas JWKS reachability.

Strand: WD-12 + G13 (real probes).
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..database import get_session

router = APIRouter(tags=["health"])


# G13: cache probe results for 30s so /health/full isn't a thundering herd.
_probe_cache: dict[str, tuple[float, Any]] = {}
_PROBE_TTL = 30.0


async def _cached(key: str, fn) -> Any:
    entry = _probe_cache.get(key)
    if entry and entry[0] > time.monotonic():
        return entry[1]
    value = await fn()
    _probe_cache[key] = (time.monotonic() + _PROBE_TTL, value)
    return value


def reset_probe_cache_for_tests() -> None:
    _probe_cache.clear()


@router.get("/health")
def health() -> dict[str, str]:
    """Process is up. No DB or external dependency."""
    return {"status": "ok"}


async def _probe_db(session: AsyncSession) -> str:
    try:
        await session.execute(text("SELECT 1"))
        return "ok"
    except Exception as e:
        return f"error: {type(e).__name__}"


async def _probe_url(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
            r = await client.head(url, follow_redirects=True)
            # Many JWKS endpoints don't accept HEAD; fall back to GET.
            if r.status_code == 405:
                r = await client.get(url)
        return "ok" if 200 <= r.status_code < 400 else f"http {r.status_code}"
    except httpx.HTTPError as e:
        return f"error: {type(e).__name__}"


async def _try_get_session():
    """Lazy session — if DB isn't configured we report 'unconfigured' rather
    than 500 the health probe."""
    try:
        async for s in get_session():
            yield s
    except RuntimeError:
        yield None


@router.get("/health/full")
async def health_full(
    settings: Settings = Depends(get_settings),
    session: AsyncSession | None = Depends(_try_get_session),
) -> dict[str, Any]:
    """Readiness — actually probes Postgres + R2 public domain + both JWKS URLs.
    Results cached for 30s. Endpoint never 500s — every probe maps to a string."""
    if session is None:
        db_status = "unconfigured"
    else:
        db_status = await _cached("db", lambda: _probe_db(session))
    r2_status = await _cached(
        "r2",
        lambda: _probe_url(f"https://{settings.r2_public_domain}/"),
    )
    pro_status = await _cached("pro_jwks", lambda: _probe_url(settings.pro_jwks_url))
    et_status = await _cached("et_jwks", lambda: _probe_url(settings.eternitas_jwks_url))

    overall = "ok"
    if any(("error" in s) for s in (db_status, pro_status, et_status)):
        overall = "degraded"

    return {
        "status": overall,
        "database": db_status,
        "r2_bucket": r2_status,
        "jwks": {
            "pro": pro_status,
            "eternitas": et_status,
        },
    }
