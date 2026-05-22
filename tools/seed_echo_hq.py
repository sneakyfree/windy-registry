"""Seed Echo HQ — the first official control-panel-template drop — into a fresh registry.

Idempotent: re-running on an already-seeded registry is a no-op.

Usage (inside the api container):
    docker exec windy-registry-api python /app/tools/seed_echo_hq.py

The bundle is expected to live at https://drops.windydrops.com/windy-echo-hq/0.1.0/.
Upload it first per docs/runbooks/seed-echo-hq.md (R2 cli command captured there).

This script bootstraps the registry's catalog for the first deploy; once the
SDK + author flow is the canonical path for getting drops into the registry,
this script stays for greenfield environments + sandbox resets.
"""

import asyncio

from sqlalchemy import select

from windy_registry.database import get_session_factory
from windy_registry.models import Drop, DropVersion

MANIFEST = {
    "schema": "windy.drop.v1",
    "id": "windy-echo-hq",
    "name": "Echo HQ",
    "subtitle": "Cyberpunk-vitals dashboard for the box you're sitting at",
    "type": "control-panel-template",
    "version": "0.1.0",
    "author": [{"name": "Kit Army Windstorm"}],
    "license": "MIT",
    "consumes": ["windy.vitals.v1", "windy.fleet.v1"],
    "surfaces": ["windy-control-panel"],
    "entry": "render.js",
    "tags": ["vitals", "cyberpunk", "fleet", "official"],
    "preview": "preview.png",
    "control_panel": {"refresh_interval_ms": 1000, "supports_remote_fleet": True},
}
BUNDLE_URL = "https://drops.windydrops.com/windy-echo-hq/0.1.0/windy-echo-hq-0.1.0.zip"
# sha256 of the bundle uploaded 2026-05-22 — see docs/runbooks/seed-echo-hq.md
# for how to regenerate when the canonical source updates.
SHA256 = "36f9a76e53b29b5b0b1e658c56a69b921c1605ff7b43db22498162e7cc6e885a"


async def main() -> None:
    async with get_session_factory()() as session:
        existing = (
            await session.execute(select(Drop).where(Drop.id == "windy-echo-hq"))
        ).scalar_one_or_none()
        if existing:
            print("windy-echo-hq already exists — no-op")
            return
        session.add(Drop(id="windy-echo-hq", type="control-panel-template", current_version="0.1.0"))
        session.add(
            DropVersion(
                drop_id="windy-echo-hq",
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
        print(f"seeded windy-echo-hq 0.1.0 → {BUNDLE_URL}")


if __name__ == "__main__":
    asyncio.run(main())
