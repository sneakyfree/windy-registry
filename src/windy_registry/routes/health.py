"""health.py — liveness / readiness endpoints.

`/health`        — fast, no dependencies. Must always answer.
`/health/full`   — readiness: probes DB + R2 + Eternitas JWKS reachability.

Strand: WD-12. Extended in WD-13 (R2 probe), WD-14 (DB probe), WD-15 (JWKS).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Process is up. No DB or external dependency."""
    return {"status": "ok"}


@router.get("/health/full")
async def health_full() -> dict[str, Any]:
    """Readiness probe — covers DB, R2, JWKS reachability once those land.

    For now returns a structured stub so consumers can wire dashboards
    against the eventual shape without churn.
    """
    return {
        "status": "ok",
        "database": "unconfigured",   # WD-14 will probe Postgres
        "r2_bucket": "unconfigured",  # WD-13 will probe R2
        "jwks": {
            "pro": "unconfigured",        # WD-15 will probe
            "eternitas": "unconfigured",  # WD-15 will probe
        },
    }
