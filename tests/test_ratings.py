"""test_ratings.py — WD-20 acceptance tests for rating endpoints."""

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
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sm() as session:
        yield session
    await engine.dispose()


def _app(db_session: AsyncSession, sub: str = "user-1") -> FastAPI:
    from windy_registry.main import create_app
    app = create_app()

    async def os_(): yield db_session
    async def ou() -> AuthUser:
        return AuthUser(subject=sub, issuer=None, tier="human",
                        passport=None, integrity_band=None, clearance_level=None, raw_claims={})

    app.dependency_overrides[get_session] = os_
    app.dependency_overrides[get_current_user] = ou
    return app


async def _seed(session: AsyncSession, drop_id: str) -> None:
    session.add(Drop(id=drop_id, type="skill", current_version="1.0.0"))
    session.add(DropVersion(
        drop_id=drop_id, version="1.0.0",
        manifest={"id": drop_id, "name": "x", "type": "skill", "version": "1.0.0",
                  "author": [{"name": "A"}], "license": "MIT", "schema": "windy.drop.v1"},
        bundle_url=f"https://drops/{drop_id}.zip", bundle_sha256="a" * 64,
    ))
    await session.flush()


@pytest.mark.asyncio
async def test_rate_drop_creates_row(db_session: AsyncSession) -> None:
    await _seed(db_session, "rated")
    client = TestClient(_app(db_session))
    r = client.post("/api/v1/drops/rated/rating", json={"stars": 5, "review": "Lovely"})
    assert r.status_code == 201, r.text
    assert r.json()["stars"] == 5


@pytest.mark.asyncio
async def test_rate_is_upsert_idempotent_per_user(db_session: AsyncSession) -> None:
    await _seed(db_session, "rated")
    client = TestClient(_app(db_session))
    assert client.post("/api/v1/drops/rated/rating", json={"stars": 5}).status_code == 201
    r = client.post("/api/v1/drops/rated/rating", json={"stars": 2, "review": "Changed my mind"})
    assert r.status_code == 201
    listing = client.get("/api/v1/drops/rated/ratings").json()
    assert listing["aggregate"]["rating_count"] == 1
    assert listing["aggregate"]["stars_avg_raw"] == 2.0


@pytest.mark.asyncio
async def test_rate_out_of_range_rejected(db_session: AsyncSession) -> None:
    await _seed(db_session, "rated")
    client = TestClient(_app(db_session))
    assert client.post("/api/v1/drops/rated/rating", json={"stars": 0}).status_code == 422
    assert client.post("/api/v1/drops/rated/rating", json={"stars": 6}).status_code == 422


@pytest.mark.asyncio
async def test_rate_missing_drop_404(db_session: AsyncSession) -> None:
    client = TestClient(_app(db_session))
    r = client.post("/api/v1/drops/ghost/rating", json={"stars": 5})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_aggregate_histogram_and_bayesian(db_session: AsyncSession) -> None:
    await _seed(db_session, "popular")
    # Seed 3 ratings from 3 different users (subject is hashed → distinct user_ids).
    for sub, stars in [("u1", 5), ("u2", 5), ("u3", 4)]:
        app = _app(db_session, sub=sub)
        TestClient(app).post("/api/v1/drops/popular/rating", json={"stars": stars})

    client = TestClient(_app(db_session))
    body = client.get("/api/v1/drops/popular/ratings").json()
    agg = body["aggregate"]
    assert agg["rating_count"] == 3
    # raw avg = (5+5+4)/3 ≈ 4.667
    assert abs(agg["stars_avg_raw"] - 4.667) < 0.01
    # bayesian = (3*4.667 + 5*3.5) / (3+5) = (14+17.5)/8 = 3.9375
    assert abs(agg["bayesian_score"] - 3.9375) < 0.001
    assert agg["histogram"]["5"] == 2
    assert agg["histogram"]["4"] == 1
    assert agg["histogram"]["1"] == 0


@pytest.mark.asyncio
async def test_recent_reviews_only_includes_reviewed_ratings(db_session: AsyncSession) -> None:
    await _seed(db_session, "drop-x")
    # Star-only rating (no review).
    app1 = _app(db_session, sub="silent")
    TestClient(app1).post("/api/v1/drops/drop-x/rating", json={"stars": 5})
    # Star + review.
    app2 = _app(db_session, sub="verbose")
    TestClient(app2).post(
        "/api/v1/drops/drop-x/rating",
        json={"stars": 3, "review": "Mid-tier"},
    )

    body = TestClient(_app(db_session)).get("/api/v1/drops/drop-x/ratings").json()
    assert body["aggregate"]["rating_count"] == 2
    assert body["aggregate"]["review_count"] == 1
    assert len(body["recent"]) == 1
    assert body["recent"][0]["review"] == "Mid-tier"
