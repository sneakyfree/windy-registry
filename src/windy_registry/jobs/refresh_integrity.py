"""refresh_integrity.py — G5. Nightly job that refreshes Eternitas
integrity_band + clearance_level for every author with a passport.

Per ADR-053 §"Author profiles & social graph": "Eternitas integrity_band
is refreshed nightly via GET https://api.eternitas.ai/api/v1/passports/<passport>
(NOT per-request — too expensive)".

Run via systemd timer (deploy/integrity-refresh.timer) at 04:06 UTC daily
or via `python -m windy_registry.jobs.refresh_integrity`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from ..config import get_settings
from ..database import get_session_factory
from ..models import Author

logger = logging.getLogger(__name__)


async def refresh_one(client: httpx.AsyncClient, passport: str) -> dict | None:
    """Fetch Eternitas profile status for a single passport."""
    settings = get_settings()
    # Eternitas exposes the per-passport status at /api/v1/passports/<id>/status
    # (per windy-connect/docs/bundle-spec-v1.md §"eternitas block").
    base = settings.eternitas_jwks_url.rsplit("/.well-known/", 1)[0]
    url = f"{base}/api/v1/passports/{passport}/status"
    try:
        r = await client.get(url, timeout=httpx.Timeout(10.0))
        if r.status_code == 200:
            return r.json()
    except httpx.HTTPError as e:
        logger.warning("integrity refresh failed for %s: %s", passport, e)
    return None


async def refresh_all() -> dict:
    """Sweep all Author rows that have a passport, update their bands.
    Returns counters."""
    factory = get_session_factory()
    refreshed = 0
    failed = 0
    async with factory() as session:
        rows = (await session.execute(
            select(Author).where(Author.passport.is_not(None))
        )).scalars().all()
        async with httpx.AsyncClient() as client:
            for a in rows:
                status = await refresh_one(client, a.passport)
                if status is None:
                    failed += 1
                    continue
                a.integrity_band = status.get("integrity_band")
                a.clearance_level = status.get("clearance_level")
                a.integrity_refreshed_at = datetime.now(UTC)
                refreshed += 1
            await session.commit()
    return {"refreshed": refreshed, "failed": failed, "total": refreshed + failed}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = asyncio.run(refresh_all())
    logger.info("integrity refresh complete: %s", result)
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
