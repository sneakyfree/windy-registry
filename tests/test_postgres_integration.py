"""test_postgres_integration.py — the real-database suite.

Gated on TEST_DATABASE_URL (postgresql+asyncpg://…). CI provides a pgvector
service container; locally:

    docker run --rm -d -p 55432:5432 -e POSTGRES_PASSWORD=test \
        pgvector/pgvector:pg16
    TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@127.0.0.1:55432/postgres \
        pytest tests/test_postgres_integration.py -v

Exists because three prod-only bugs shipped while the SQLite suite was green:
  - forks.fork_drop_id FK (SQLite does not enforce FKs)          → PR #17
  - webhook dispatch `jsonb LIKE varchar` (no operator on PG)    → PR #16
  - real-EPT claim shape (unit tokens had a fabricated claim)    → PR #15
Everything here runs the actual app against genuine Postgres, with alembic
migrations as the schema source (not create_all) so migration drift fails too.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from windy_registry.database import get_session
from windy_registry.middleware.auth import AuthUser, get_current_user

PG_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not PG_URL,
        reason="TEST_DATABASE_URL not set — Postgres integration suite skipped",
    ),
]

PASSPORT = "ET26-TEST-0001"

TABLES = (
    "webhook_deliveries",
    "webhook_subscriptions",
    "ratings",
    "user_library",
    "forks",
    "refunds",
    "purchases",
    "tips",
    "follows",
    "authors",
    "drop_versions",
    "drops",
)


def _run_migrations() -> None:
    """Apply alembic migrations against the test database (sync URL)."""
    from alembic.config import Config

    from alembic import command

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", PG_URL)
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def migrated_schema() -> None:
    _run_migrations()


@pytest.fixture
def clean_tables() -> None:
    """Truncate between tests (own short-lived loop; NullPool keeps it clean)."""

    async def _truncate() -> None:
        engine = create_async_engine(PG_URL, poolclass=NullPool)
        async with engine.begin() as conn:
            existing = {
                r[0]
                for r in await conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                )
            }
            victims = [t for t in TABLES if t in existing]
            if victims:
                await conn.execute(
                    text(f"TRUNCATE {', '.join(victims)} RESTART IDENTITY CASCADE")
                )
        await engine.dispose()

    asyncio.run(_truncate())


@pytest.fixture
def app(clean_tables: None) -> FastAPI:
    from windy_registry.main import create_app

    app = create_app()

    # asyncpg binds connections to the creating event loop, so the engine must
    # be built INSIDE the app's loop (TestClient portal), not pytest-asyncio's.
    # NullPool → no connection outlives its request, nothing crosses loops.
    engine = create_async_engine(PG_URL, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with maker() as session:
            yield session
            await session.commit()

    async def override_user() -> AuthUser:
        return AuthUser(
            subject=PASSPORT,
            issuer="eternitas.ai",
            tier="agent",
            passport=PASSPORT,
            integrity_band="fair",
            clearance_level="verified",
            raw_claims={"sub": PASSPORT},
        )

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = override_user
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def manifest(drop_id: str = "pg-test") -> dict:
    return {
        "schema": "windy.drop.v1",
        "id": drop_id,
        "name": "PG Integration Drop",
        "type": "skill",
        "version": "1.0.0",
        "author": [{"name": "PG Test", "passport": PASSPORT, "type": "human"}],
        "license": "MIT",
    }


def publish(client: TestClient, drop_id: str = "pg-test") -> None:
    r = client.post(
        "/api/v1/drops",
        json={
            "manifest": manifest(drop_id),
            "bundle_url": f"https://drops.windydrops.com/{drop_id}/1.0.0/{drop_id}-1.0.0.zip",
            "bundle_sha256": "a" * 64,
        },
    )
    assert r.status_code == 201, f"publish on Postgres: {r.status_code} {r.text}"


def test_migrations_apply_cleanly(client: TestClient) -> None:
    # migrated_schema already ran; a live query proves the schema is usable.
    r = client.get("/api/v1/drops")
    assert r.status_code == 200, r.text


def test_publish_with_webhook_subscriber_dispatches_on_pg(client: TestClient) -> None:
    """Regression for PR #16: jsonb LIKE 500'd every event-firing write."""
    r = client.post(
        "/api/v1/webhooks/subscribe",
        json={
            "callback_url": "https://example.com/pg-hook",
            "event_types": ["drop.published"],
            "secret": "pg-integration-secret",
        },
    )
    assert r.status_code in (200, 201), r.text

    publish(client)  # 500'd here pre-#16

    r = client.get("/api/v1/webhooks")
    assert r.status_code == 200
    assert r.json()["total"] == 1


def test_fork_before_publish_on_pg(client: TestClient) -> None:
    """Regression for PR #17: FK on fork_drop_id 500'd every fork."""
    publish(client, "pg-fork-src")
    r = client.post("/api/v1/drops/pg-fork-src/fork", json={"new_id": "pg-fork-new"})
    assert r.status_code == 201, f"fork on Postgres: {r.status_code} {r.text}"
    r = client.get("/api/v1/drops/pg-fork-src/forks")
    assert r.json()["total"] == 1


def test_full_write_path_on_pg(client: TestClient) -> None:
    """install → rate → uninstall → withdraw, all event-firing, all on PG."""
    publish(client, "pg-writes")

    r = client.post(
        "/api/v1/me/library/install",
        json={"drop_id": "pg-writes", "version": "1.0.0"},
    )
    assert r.status_code == 201, r.text

    r = client.post(
        "/api/v1/drops/pg-writes/rating",
        json={"stars": 4, "review": "pg integration"},
    )
    assert r.status_code == 201, r.text

    r = client.post("/api/v1/me/library/uninstall", json={"drop_id": "pg-writes"})
    assert r.status_code in (200, 204), r.text

    r = client.delete("/api/v1/drops/pg-writes")
    assert r.status_code in (200, 204), r.text

    r = client.get("/api/v1/drops")
    assert "pg-writes" not in [i["id"] for i in r.json()["items"]]
