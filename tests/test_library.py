"""test_library.py — WD-17 acceptance tests for /me/library."""

from __future__ import annotations

from typing import Any, AsyncGenerator

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


def _swap_pgvector_for_sqlite() -> None:
    tbl = DropModel.__table__
    if "embedding" in tbl.c and not isinstance(tbl.c.embedding.type, JSON):
        tbl.c.embedding.type = JSON()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    _swap_pgvector_for_sqlite()
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

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
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


async def _seed_drop(
    session: AsyncSession,
    drop_id: str,
    drop_type: str = "skill",
    version: str = "1.0.0",
    pricing_type: str = "free",
) -> None:
    session.add(Drop(id=drop_id, type=drop_type, current_version=version))
    session.add(DropVersion(
        drop_id=drop_id,
        version=version,
        manifest={
            "schema": "windy.drop.v1",
            "id": drop_id,
            "name": "Test",
            "type": drop_type,
            "version": version,
            "author": [{"name": "Test"}],
            "license": "MIT",
            "pricing": {"type": pricing_type},
        },
        bundle_url="https://drops/x.zip",
        bundle_sha256="a" * 64,
    ))
    await session.flush()


@pytest.mark.asyncio
async def test_install_free_drop_succeeds(db_session: AsyncSession, client: TestClient) -> None:
    await _seed_drop(db_session, "test-drop")
    r = client.post("/api/v1/me/library/install", json={"drop_id": "test-drop"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["drop_id"] == "test-drop"
    assert body["version"] == "1.0.0"
    assert body["auto_update"] is True


@pytest.mark.asyncio
async def test_install_unknown_drop_returns_404(client: TestClient) -> None:
    r = client.post("/api/v1/me/library/install", json={"drop_id": "no-such-drop"})
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "drop_not_found"


@pytest.mark.asyncio
async def test_install_paid_drop_returns_402(db_session: AsyncSession, client: TestClient) -> None:
    await _seed_drop(db_session, "paid-drop", pricing_type="paid")
    r = client.post("/api/v1/me/library/install", json={"drop_id": "paid-drop"})
    assert r.status_code == 402
    assert r.json()["detail"]["error"] == "paid_drops_v1_1"


@pytest.mark.asyncio
async def test_install_twice_returns_409(db_session: AsyncSession, client: TestClient) -> None:
    await _seed_drop(db_session, "twice-drop")
    assert client.post("/api/v1/me/library/install", json={"drop_id": "twice-drop"}).status_code == 201
    r = client.post("/api/v1/me/library/install", json={"drop_id": "twice-drop"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "already_installed"


@pytest.mark.asyncio
async def test_install_withdrawn_drop_returns_410(db_session: AsyncSession, client: TestClient) -> None:
    from datetime import UTC, datetime
    await _seed_drop(db_session, "withdrawn-drop")
    drop = await db_session.get(Drop, "withdrawn-drop")
    drop.withdrawn_at = datetime.now(UTC)
    await db_session.flush()
    r = client.post("/api/v1/me/library/install", json={"drop_id": "withdrawn-drop"})
    assert r.status_code == 410


@pytest.mark.asyncio
async def test_list_returns_installed_drops(db_session: AsyncSession, client: TestClient) -> None:
    await _seed_drop(db_session, "first", drop_type="control-panel-template")
    await _seed_drop(db_session, "second", drop_type="skill")
    client.post("/api/v1/me/library/install", json={"drop_id": "first"})
    client.post("/api/v1/me/library/install", json={"drop_id": "second"})
    r = client.get("/api/v1/me/library")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert {item["drop_id"] for item in body["items"]} == {"first", "second"}


@pytest.mark.asyncio
async def test_list_filter_by_type(db_session: AsyncSession, client: TestClient) -> None:
    await _seed_drop(db_session, "cp", drop_type="control-panel-template")
    await _seed_drop(db_session, "sk", drop_type="skill")
    client.post("/api/v1/me/library/install", json={"drop_id": "cp"})
    client.post("/api/v1/me/library/install", json={"drop_id": "sk"})
    r = client.get("/api/v1/me/library?type=control-panel-template")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["drop_id"] == "cp"


@pytest.mark.asyncio
async def test_uninstall_removes_row(db_session: AsyncSession, client: TestClient) -> None:
    await _seed_drop(db_session, "removable")
    client.post("/api/v1/me/library/install", json={"drop_id": "removable"})
    r = client.post("/api/v1/me/library/uninstall", json={"drop_id": "removable"})
    assert r.status_code == 204
    listing = client.get("/api/v1/me/library").json()
    assert listing["total"] == 0


@pytest.mark.asyncio
async def test_uninstall_nonexistent_returns_404(client: TestClient) -> None:
    r = client.post("/api/v1/me/library/uninstall", json={"drop_id": "ghost"})
    assert r.status_code == 404
