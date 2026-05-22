"""test_withdraw.py — WD-9 acceptance tests for DELETE /api/v1/drops/{id}."""

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
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sm() as session:
        yield session
    await engine.dispose()


def _app(db_session: AsyncSession, passport: str | None = "ET26-TEST-0001") -> FastAPI:
    from windy_registry.main import create_app
    app = create_app()

    async def override_session():
        yield db_session

    async def override_user() -> AuthUser:
        return AuthUser(
            subject="caller", issuer=None, tier="agent" if passport else "human",
            passport=passport, integrity_band=None, clearance_level=None, raw_claims={},
        )

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = override_user
    return app


async def _seed(session: AsyncSession, drop_id: str, author_passport: str | None = "ET26-TEST-0001") -> None:
    author: dict[str, str | None] = {"name": "Test"}
    if author_passport:
        author["passport"] = author_passport
    session.add(Drop(id=drop_id, type="skill", current_version="1.0.0"))
    session.add(DropVersion(
        drop_id=drop_id, version="1.0.0",
        manifest={"schema": "windy.drop.v1", "id": drop_id, "name": "x", "type": "skill",
                  "version": "1.0.0", "author": [author], "license": "MIT"},
        bundle_url=f"https://drops/{drop_id}.zip", bundle_sha256="a" * 64,
    ))
    await session.flush()


@pytest.mark.asyncio
async def test_withdraw_sets_withdrawn_at(db_session: AsyncSession) -> None:
    await _seed(db_session, "kill-me")
    app = _app(db_session)
    client = TestClient(app)
    r = client.delete("/api/v1/drops/kill-me")
    assert r.status_code == 204
    drop = await db_session.get(Drop, "kill-me")
    assert drop.withdrawn_at is not None


@pytest.mark.asyncio
async def test_withdraw_404_when_drop_missing(db_session: AsyncSession) -> None:
    app = _app(db_session)
    client = TestClient(app)
    r = client.delete("/api/v1/drops/ghost")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_withdraw_403_when_caller_not_author(db_session: AsyncSession) -> None:
    await _seed(db_session, "owned", author_passport="ET26-OTHER-0002")
    app = _app(db_session, passport="ET26-TEST-0001")
    client = TestClient(app)
    r = client.delete("/api/v1/drops/owned")
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "not_author"


@pytest.mark.asyncio
async def test_withdrawn_drop_disappears_from_browse(db_session: AsyncSession) -> None:
    await _seed(db_session, "alive")
    await _seed(db_session, "kill-me")
    app = _app(db_session)
    client = TestClient(app)
    assert client.delete("/api/v1/drops/kill-me").status_code == 204
    browse = client.get("/api/v1/drops").json()
    assert {i["id"] for i in browse["items"]} == {"alive"}
