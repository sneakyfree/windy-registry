"""version.py — /version endpoint per MF1 contract.

Reference impl: eternitas/src/eternitas/routes/version.py (PR #74).
Contract (every Windy service returns this shape):

    {
      "service": "windy-registry",
      "version": "0.1.0",
      "commit_sha": "abc1234567890abcdef1234567890abcdef12345" | null,
      "commit_sha_short": "abc1234" | null,
      "build_timestamp": "2026-05-21T16:00:00Z" | null,
      "started_at": "2026-05-21T17:00:00Z",
      "environment": "production"
    }

Invariants:
  - No auth required.
  - No DB or Redis dependency — must answer during incidents.
  - Skipped from rate limiting.
  - Unset env vars surface as null (NOT empty string, NOT zero).
  - `started_at` captured at module import (per-process, fixed).
  - `build_timestamp` set by build pipeline (per-image, fixed).

Strand: WD-12 (per AUDIT_2026-05-21.md Gap #2 + MF1).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from .. import __version__
from ..config import get_settings

router = APIRouter(tags=["health"])

# Captured at module import — per-process, fixed.
_STARTED_AT = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize(value: str | None) -> str | None:
    """Treat empty strings the same as unset envs — MF1 invariant."""
    if value is None or value == "":
        return None
    return value


@router.get("/version")
def version_endpoint() -> dict[str, str | None]:
    s = get_settings()
    commit = _normalize(s.commit_sha)
    return {
        "service": s.service,
        "version": __version__,
        "commit_sha": commit,
        "commit_sha_short": commit[:7] if commit else None,
        "build_timestamp": _normalize(s.build_timestamp),
        "started_at": _STARTED_AT,
        "environment": s.environment,
    }
