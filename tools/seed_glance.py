"""Seed Glance — the second official drop — into a fresh registry.

Companion to seed_echo_hq.py. Idempotent. Bootstraps the catalog with
the second drop so multi-drop browsing works on cold deploys + sandbox
resets, before any SDK-published drops land.

Usage (inside the api container):
    docker exec windy-registry-api python /app/tools/seed_glance.py

The bundle is expected to live at https://drops.windydrops.com/windy-glance/0.1.0/.
Upload it first per docs/runbooks/seed-glance.md.

Canonical source: sneakyfree/windy-control-panel:packages/official-drops/glance/.
"""

import asyncio

from sqlalchemy import select

from windy_registry.database import get_session_factory
from windy_registry.models import Drop, DropVersion

MANIFEST = {
    "schema": "windy.drop.v1",
    "id": "windy-glance",
    "name": "Glance",
    "subtitle": "Quiet vitals dashboard for the rest of us",
    "type": "control-panel-template",
    "version": "0.1.0",
    "author": [{"name": "Kit Army Windstorm"}],
    "license": "MIT",
    "consumes": ["windy.vitals.v1", "windy.fleet.v1"],
    "surfaces": ["windy-control-panel"],
    "entry": "render.js",
    "tags": ["vitals", "minimal", "calm", "official"],
    "preview": "preview.png",
    "control_panel": {"refresh_interval_ms": 1000, "supports_remote_fleet": True},
}
BUNDLE_URL = "https://drops.windydrops.com/windy-glance/0.1.0/windy-glance-0.1.0.zip"
# sha256 of the bundle uploaded 2026-05-22 — see docs/runbooks/seed-glance.md
# for how to regenerate when the canonical source updates.
SHA256 = "26023178c5f7b9d00c8fac5a4701c1d497c4b1a0d630eac73ea72ae643636b1f"


async def main() -> None:
    async with get_session_factory()() as session:
        existing = (
            await session.execute(select(Drop).where(Drop.id == "windy-glance"))
        ).scalar_one_or_none()
        if existing:
            print("windy-glance already exists — no-op")
            return
        session.add(Drop(id="windy-glance", type="control-panel-template", current_version="0.1.0"))
        session.add(
            DropVersion(
                drop_id="windy-glance",
                version="0.1.0",
                manifest=MANIFEST,
                bundle_url=BUNDLE_URL,
                bundle_sha256=SHA256,
                signature_verified=False,
                signer_passport=None,
                signer_integrity_band=None,
                signer_clearance_level=None,
            )
        )
        await session.commit()
        print(f"seeded windy-glance 0.1.0 → {BUNDLE_URL}")


if __name__ == "__main__":
    asyncio.run(main())
