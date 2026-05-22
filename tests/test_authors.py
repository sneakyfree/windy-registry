"""test_authors.py — WD-25 acceptance tests for /authors + /me/follows."""

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
from windy_registry.models import Author, Drop, DropVersion
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


def _app(db_session: AsyncSession, sub: str = "u-1") -> FastAPI:
    from windy_registry.main import create_app
    app = create_app()

    async def os_(): yield db_session
    async def ou() -> AuthUser:
        return AuthUser(subject=sub, issuer=None, tier="human",
                        passport=None, integrity_band=None, clearance_level=None, raw_claims={})

    app.dependency_overrides[get_session] = os_
    app.dependency_overrides[get_current_user] = ou
    return app


async def _seed_author(session: AsyncSession, handle: str, passport: str = "ET26-TEST-0001") -> Author:
    from uuid import uuid4
    a = Author(id=uuid4(), handle=handle, display_name=handle.title(),
               passport=passport, integrity_band="fair", clearance_level="verified")
    session.add(a)
    await session.flush()
    return a


async def _seed_drop(session: AsyncSession, drop_id: str, callsign: str | None = None,
                     passport: str | None = None) -> None:
    session.add(Drop(id=drop_id, type="skill", current_version="1.0.0"))
    author_entry: dict = {"name": callsign or "Test"}
    if callsign:
        author_entry["callsign"] = callsign
    if passport:
        author_entry["passport"] = passport
    session.add(DropVersion(
        drop_id=drop_id, version="1.0.0",
        manifest={"schema": "windy.drop.v1", "id": drop_id, "name": "x",
                  "type": "skill", "version": "1.0.0", "author": [author_entry],
                  "license": "MIT"},
        bundle_url=f"https://drops/{drop_id}.zip", bundle_sha256="a" * 64,
        signer_passport=passport,
    ))
    await session.flush()


@pytest.mark.asyncio
async def test_author_profile_returns_404_for_unknown(db_session: AsyncSession) -> None:
    client = TestClient(_app(db_session))
    r = client.get("/api/v1/authors/no-such")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_author_profile_returns_explicit_row(db_session: AsyncSession) -> None:
    await _seed_author(db_session, "kit-oc5")
    client = TestClient(_app(db_session))
    r = client.get("/api/v1/authors/kit-oc5")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["handle"] == "kit-oc5"
    assert body["integrity_band"] == "fair"
    assert body["follower_count"] == 0


@pytest.mark.asyncio
async def test_author_profile_synthesizes_from_drop_callsign(db_session: AsyncSession) -> None:
    """If no Author row exists but a drop's manifest has callsign==handle, synthesize."""
    await _seed_drop(db_session, "drop-1", callsign="ada")
    client = TestClient(_app(db_session))
    r = client.get("/api/v1/authors/ada")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["handle"] == "ada"


@pytest.mark.asyncio
async def test_author_drops_lists_their_drops(db_session: AsyncSession) -> None:
    await _seed_drop(db_session, "alpha", callsign="ada")
    await _seed_drop(db_session, "beta", callsign="ada")
    await _seed_drop(db_session, "other", callsign="zoe")
    client = TestClient(_app(db_session))
    r = client.get("/api/v1/authors/ada/drops")
    assert r.status_code == 200
    ids = {i["id"] for i in r.json()["items"]}
    assert ids == {"alpha", "beta"}


@pytest.mark.asyncio
async def test_follow_unfollow_round_trip(db_session: AsyncSession) -> None:
    client = TestClient(_app(db_session))
    r = client.post("/api/v1/me/follows", json={"author_handle": "ada"})
    assert r.status_code == 201, r.text
    # Idempotent — second POST returns the existing row.
    r2 = client.post("/api/v1/me/follows", json={"author_handle": "ada"})
    assert r2.status_code == 201
    list_resp = client.get("/api/v1/me/follows").json()
    assert list_resp["total"] == 1
    assert client.delete("/api/v1/me/follows/ada").status_code == 204
    assert client.delete("/api/v1/me/follows/ada").status_code == 404
    assert client.get("/api/v1/me/follows").json()["total"] == 0


@pytest.mark.asyncio
async def test_follow_count_increments_on_profile(db_session: AsyncSession) -> None:
    await _seed_author(db_session, "ada")
    # User A follows.
    client_a = TestClient(_app(db_session, sub="user-a"))
    client_a.post("/api/v1/me/follows", json={"author_handle": "ada"})
    # User B follows.
    client_b = TestClient(_app(db_session, sub="user-b"))
    client_b.post("/api/v1/me/follows", json={"author_handle": "ada"})

    profile = TestClient(_app(db_session)).get("/api/v1/authors/ada").json()
    assert profile["follower_count"] == 2
