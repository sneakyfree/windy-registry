"""test_health_r2_probe.py — the R2 health probe checks a REAL object.

2026-07-13 production finding: /health/full reported `r2_bucket: http 404`
while bundles downloaded fine (sha256-verified). Root cause: the probe
HEAD'd the public-domain root, which an R2 public bucket 404s BY DESIGN.
These tests pin the fixed semantics:

  - a published bundle exists  → probe THAT url; 404 there = real degradation
  - nothing published          → root 404 = `ok (empty)` (domain is wired)
  - r2 participates in the overall ok/degraded verdict
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from windy_registry.database import Base
from windy_registry.models import Drop, DropVersion
from windy_registry.models.drop import Drop as DropModel
from windy_registry.routes import health as health_module
from windy_registry.routes.health import _try_get_session, reset_probe_cache_for_tests

BUNDLE_URL = "https://drops.windydrops.com/probe-drop/1.0.0/probe-drop-1.0.0.zip"


def _swap_pgvector() -> None:
    tbl = DropModel.__table__
    if "embedding" in tbl.c and not isinstance(tbl.c.embedding.type, JSON):
        tbl.c.embedding.type = JSON()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    _swap_pgvector()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def app(db_session: AsyncSession) -> FastAPI:
    from windy_registry.main import create_app

    application = create_app()

    async def override_session():
        yield db_session

    application.dependency_overrides[_try_get_session] = override_session
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def fake_probe(monkeypatch):
    """Replace _probe_url with a recorder returning per-URL canned statuses."""
    calls: list[str] = []
    responses: dict[str, str] = {}

    async def _fake(url: str) -> str:
        calls.append(url)
        for prefix, status in responses.items():
            if url.startswith(prefix):
                return status
        return "ok"

    monkeypatch.setattr(health_module, "_probe_url", _fake)
    reset_probe_cache_for_tests()
    return {"calls": calls, "responses": responses}


async def _seed_version(session: AsyncSession) -> None:
    session.add(Drop(id="probe-drop", type="skill", current_version="1.0.0"))
    session.add(
        DropVersion(
            drop_id="probe-drop",
            version="1.0.0",
            manifest={"schema": "windy.drop.v1", "id": "probe-drop"},
            bundle_url=BUNDLE_URL,
            bundle_sha256="0" * 64,
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_probe_uses_real_published_object(client, db_session, fake_probe) -> None:
    await _seed_version(db_session)
    r = client.get("/health/full")
    assert r.status_code == 200
    body = r.json()
    assert body["r2_bucket"] == "ok"
    assert BUNDLE_URL in fake_probe["calls"], "probed the real bundle URL"
    assert not any(u.rstrip("/").endswith("windydrops.com") for u in fake_probe["calls"] if u != BUNDLE_URL)


@pytest.mark.asyncio
async def test_known_object_404_is_real_degradation(client, db_session, fake_probe) -> None:
    await _seed_version(db_session)
    fake_probe["responses"][BUNDLE_URL] = "http 404"
    r = client.get("/health/full")
    body = r.json()
    assert body["r2_bucket"] == "http 404"
    assert body["status"] == "degraded", "r2 now participates in the verdict"


@pytest.mark.asyncio
async def test_empty_registry_root_404_means_ok_empty(client, fake_probe) -> None:
    """No published bundles: the root probe's 404 is R2's no-such-object
    answer from a wired bucket — the 2026-07-13 false alarm, now labeled."""
    fake_probe["responses"]["https://drops.windydrops.com/"] = "http 404"
    r = client.get("/health/full")
    body = r.json()
    assert body["r2_bucket"] == "ok (empty)"
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_empty_registry_root_error_still_reported(client, fake_probe) -> None:
    fake_probe["responses"]["https://drops.windydrops.com/"] = "error: ConnectError"
    r = client.get("/health/full")
    body = r.json()
    assert body["r2_bucket"] == "error: ConnectError"
    assert body["status"] == "degraded"
