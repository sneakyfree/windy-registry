"""test_browse.py — WD-16 acceptance tests for browse + trending + detail + r2-config."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from windy_registry.database import Base, get_session
from windy_registry.middleware.auth import AuthUser, get_current_user
from windy_registry.models import Drop, DropVersion, Fork, UserLibrary
from windy_registry.models.drop import Drop as DropModel


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
    app = create_app()

    async def override_session():
        yield db_session

    async def override_user() -> AuthUser:
        return AuthUser(
            subject="user-1",
            issuer="https://account.windyword.ai",
            tier="human",
            passport=None,
            integrity_band=None,
            clearance_level=None,
            raw_claims={},
        )

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = override_user
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


async def _seed(session: AsyncSession, drop_id: str, drop_type: str = "skill", **manifest_extra) -> None:
    manifest = {
        "schema": "windy.drop.v1",
        "id": drop_id,
        "name": manifest_extra.pop("name", f"Drop {drop_id}"),
        "type": drop_type,
        "version": "1.0.0",
        "author": [{"name": "Test"}],
        "license": "MIT",
        "tags": manifest_extra.pop("tags", []),
        **manifest_extra,
    }
    session.add(Drop(id=drop_id, type=drop_type, current_version="1.0.0"))
    session.add(DropVersion(
        drop_id=drop_id, version="1.0.0", manifest=manifest,
        bundle_url=f"https://drops/{drop_id}.zip", bundle_sha256="a" * 64,
    ))
    await session.flush()


@pytest.mark.asyncio
async def test_browse_lists_published_drops(db_session: AsyncSession, client: TestClient) -> None:
    await _seed(db_session, "alpha")
    await _seed(db_session, "beta", drop_type="control-panel-template")
    r = client.get("/api/v1/drops")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert {i["id"] for i in body["items"]} == {"alpha", "beta"}


@pytest.mark.asyncio
async def test_browse_filter_by_type(db_session: AsyncSession, client: TestClient) -> None:
    await _seed(db_session, "sk", drop_type="skill")
    await _seed(db_session, "cp", drop_type="control-panel-template")
    r = client.get("/api/v1/drops?type=skill")
    assert r.status_code == 200
    body = r.json()
    assert {i["id"] for i in body["items"]} == {"sk"}


@pytest.mark.asyncio
async def test_browse_excludes_withdrawn(db_session: AsyncSession, client: TestClient) -> None:
    from datetime import UTC, datetime
    await _seed(db_session, "alive")
    await _seed(db_session, "dead")
    drop = await db_session.get(Drop, "dead")
    drop.withdrawn_at = datetime.now(UTC)
    await db_session.flush()
    r = client.get("/api/v1/drops")
    assert {i["id"] for i in r.json()["items"]} == {"alive"}


@pytest.mark.asyncio
async def test_drop_detail_returns_manifest_and_aggregates(
    db_session: AsyncSession, client: TestClient
) -> None:
    await _seed(db_session, "detail-drop")
    r = client.get("/api/v1/drops/detail-drop")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "detail-drop"
    assert body["manifest"]["id"] == "detail-drop"
    assert body["bundle_sha256"] == "a" * 64
    assert body["install_count"] == 0


@pytest.mark.asyncio
async def test_drop_detail_404(client: TestClient) -> None:
    r = client.get("/api/v1/drops/no-such")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_trending_orders_by_install_count(
    db_session: AsyncSession, client: TestClient
) -> None:
    from uuid import uuid4
    await _seed(db_session, "popular")
    await _seed(db_session, "unpopular")
    # Seed 3 installs on "popular".
    for _ in range(3):
        db_session.add(UserLibrary(user_id=uuid4(), drop_id="popular", version="1.0.0"))
    await db_session.flush()
    r = client.get("/api/v1/drops/trending")
    assert r.status_code == 200, r.text
    ids = [i["id"] for i in r.json()["items"]]
    assert ids[0] == "popular"
    # install_count populated
    assert r.json()["items"][0]["install_count"] == 3


@pytest.mark.asyncio
async def test_trending_fork_count_contributes(
    db_session: AsyncSession, client: TestClient
) -> None:
    await _seed(db_session, "original")
    await _seed(db_session, "no-forks")
    # Seed a fork — and add the fork's own Drop row so the FK is satisfied.
    await _seed(db_session, "fork-1")
    db_session.add(Fork(source_drop_id="original", fork_drop_id="fork-1", is_published=True))
    await db_session.flush()
    r = client.get("/api/v1/drops/trending")
    ids = [i["id"] for i in r.json()["items"]]
    # "original" has 1 fork; "no-forks" has 0. "original" ranks higher than
    # "no-forks". "fork-1" also exists but has 0 installs + 0 forks of its own.
    assert ids.index("original") < ids.index("no-forks")


@pytest.mark.asyncio
async def test_well_known_r2_config(client: TestClient) -> None:
    r = client.get("/.well-known/r2-config")
    assert r.status_code == 200
    body = r.json()
    assert body["bucket"] == "windydrops-bundles"
    assert body["public_domain"] == "drops.windydrops.com"
    assert "account_id" in body
