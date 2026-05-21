"""test_forks.py — WD-19 acceptance tests for /drops/{id}/fork + /forks."""

from __future__ import annotations

from typing import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from windy_registry.database import Base, get_session
from windy_registry.middleware.auth import AuthUser, get_current_user
from windy_registry.models import Drop, DropVersion
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
def client(db_session: AsyncSession) -> TestClient:
    from windy_registry.main import create_app
    app = create_app()

    async def override_session():
        yield db_session

    async def override_user() -> AuthUser:
        return AuthUser(
            subject="user-1", issuer=None, tier="human",
            passport=None, integrity_band=None, clearance_level=None, raw_claims={},
        )

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = override_user
    return TestClient(app)


async def _seed(session: AsyncSession, drop_id: str, drop_type: str = "skill") -> None:
    session.add(Drop(id=drop_id, type=drop_type, current_version="1.0.0"))
    session.add(DropVersion(
        drop_id=drop_id, version="1.0.0",
        manifest={"schema": "windy.drop.v1", "id": drop_id, "name": "x", "type": drop_type,
                  "version": "1.0.0", "author": [{"name": "Test"}], "license": "MIT"},
        bundle_url="https://drops/x.zip", bundle_sha256="a" * 64,
    ))
    await session.flush()


@pytest.mark.asyncio
async def test_fork_registers_lineage(db_session: AsyncSession, client: TestClient) -> None:
    await _seed(db_session, "source")
    r = client.post("/api/v1/drops/source/fork", json={"new_id": "my-fork"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["source_drop_id"] == "source"
    assert body["fork_drop_id"] == "my-fork"
    assert body["is_published"] is False


@pytest.mark.asyncio
async def test_fork_source_not_found(client: TestClient) -> None:
    r = client.post("/api/v1/drops/ghost/fork", json={"new_id": "x"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_fork_collision_with_existing_drop(db_session: AsyncSession, client: TestClient) -> None:
    await _seed(db_session, "source")
    await _seed(db_session, "existing")
    r = client.post("/api/v1/drops/source/fork", json={"new_id": "existing"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "new_id_collision"


@pytest.mark.asyncio
async def test_fork_lineage_already_registered(db_session: AsyncSession, client: TestClient) -> None:
    await _seed(db_session, "source")
    client.post("/api/v1/drops/source/fork", json={"new_id": "my-fork"})
    r = client.post("/api/v1/drops/source/fork", json={"new_id": "my-fork"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "lineage_already_registered"


@pytest.mark.asyncio
async def test_fork_source_withdrawn(db_session: AsyncSession, client: TestClient) -> None:
    from datetime import UTC, datetime
    await _seed(db_session, "source")
    src = await db_session.get(Drop, "source")
    src.withdrawn_at = datetime.now(UTC)
    await db_session.flush()
    r = client.post("/api/v1/drops/source/fork", json={"new_id": "fork"})
    assert r.status_code == 410


@pytest.mark.asyncio
async def test_list_forks_returns_descendants(db_session: AsyncSession, client: TestClient) -> None:
    await _seed(db_session, "source")
    client.post("/api/v1/drops/source/fork", json={"new_id": "fork-1"})
    client.post("/api/v1/drops/source/fork", json={"new_id": "fork-2"})
    r = client.get("/api/v1/drops/source/forks")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert {i["fork_drop_id"] for i in body["items"]} == {"fork-1", "fork-2"}


@pytest.mark.asyncio
async def test_list_forks_404_on_missing_source(client: TestClient) -> None:
    r = client.get("/api/v1/drops/no-such/forks")
    assert r.status_code == 404
