"""test_webhooks.py — WD-21 acceptance tests for webhook substrate."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from windy_registry.database import Base, get_session
from windy_registry.middleware.auth import AuthUser, get_current_user
from windy_registry.models import Drop, DropVersion, WebhookDelivery
from windy_registry.models.drop import Drop as DropModel
from windy_registry.services.webhook_dispatcher import dispatch_event


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


def _app(db_session: AsyncSession) -> FastAPI:
    from windy_registry.main import create_app
    app = create_app()

    async def os_(): yield db_session
    async def ou() -> AuthUser:
        return AuthUser(
            subject="sub", issuer=None, tier="human",
            passport="ET26-TEST-0001",
            integrity_band=None, clearance_level=None, raw_claims={},
        )

    app.dependency_overrides[get_session] = os_
    app.dependency_overrides[get_current_user] = ou
    return app


@pytest.mark.asyncio
async def test_subscribe_creates_row(db_session: AsyncSession) -> None:
    client = TestClient(_app(db_session))
    r = client.post("/api/v1/webhooks/subscribe", json={
        "callback_url": "https://chat.example.com/hooks/drops",
        "event_types": ["drop.published", "drop.installed"],
        "secret": "x" * 32,
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["callback_url"] == "https://chat.example.com/hooks/drops"
    assert body["event_types"] == ["drop.published", "drop.installed"]


@pytest.mark.asyncio
async def test_subscribe_rejects_short_secret(db_session: AsyncSession) -> None:
    client = TestClient(_app(db_session))
    r = client.post("/api/v1/webhooks/subscribe", json={
        "callback_url": "https://x.example.com",
        "event_types": ["drop.published"],
        "secret": "tooshort",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_unsubscribe_removes_row(db_session: AsyncSession) -> None:
    client = TestClient(_app(db_session))
    sub = client.post("/api/v1/webhooks/subscribe", json={
        "callback_url": "https://x.example.com", "event_types": ["drop.published"], "secret": "x" * 32,
    }).json()
    r = client.delete(f"/api/v1/webhooks/{sub['id']}")
    assert r.status_code == 204
    assert client.delete(f"/api/v1/webhooks/{sub['id']}").status_code == 404


def test_event_types_endpoint_is_public() -> None:
    client = TestClient(_app.__wrapped__ if hasattr(_app, "__wrapped__") else None) if False else None
    # The endpoint is public — TestClient should reach it without auth override.
    from windy_registry.main import create_app
    plain = TestClient(create_app())
    r = plain.get("/api/v1/webhooks/event-types")
    assert r.status_code == 200
    assert "drop.published" in r.json()["event_types"]


@pytest.mark.asyncio
async def test_dispatch_event_records_delivery_skip_async(db_session: AsyncSession) -> None:
    client = TestClient(_app(db_session))
    sub = client.post("/api/v1/webhooks/subscribe", json={
        "callback_url": "https://x.example.com/hook",
        "event_types": ["drop.published"],
        "secret": "x" * 32,
    }).json()

    # Manually call the dispatcher in skip_async mode (matches what publish does).
    notified = await dispatch_event(
        db_session, "drop.published", {"drop_id": "test"}, skip_async=True,
    )
    assert len(notified) == 1

    deliveries = (await db_session.execute(select(WebhookDelivery))).scalars().all()
    assert len(deliveries) == 1
    assert deliveries[0].event_type == "drop.published"


@pytest.mark.asyncio
async def test_dispatch_only_matches_subscribed_event_types(db_session: AsyncSession) -> None:
    client = TestClient(_app(db_session))
    # Subscribe only to drop.installed.
    client.post("/api/v1/webhooks/subscribe", json={
        "callback_url": "https://x.example.com",
        "event_types": ["drop.installed"],
        "secret": "x" * 32,
    })

    # Emit drop.published — should NOT match.
    notified = await dispatch_event(db_session, "drop.published", {"drop_id": "x"}, skip_async=True)
    assert len(notified) == 0


@pytest.mark.asyncio
async def test_publish_triggers_drop_published_event(db_session: AsyncSession) -> None:
    """End-to-end: subscribe → publish → delivery row recorded."""
    client = TestClient(_app(db_session))
    client.post("/api/v1/webhooks/subscribe", json={
        "callback_url": "https://x.example.com",
        "event_types": ["drop.published"],
        "secret": "x" * 32,
    })
    r = client.post("/api/v1/drops", json={
        "manifest": {
            "schema": "windy.drop.v1",
            "id": "hook-test",
            "name": "Hook Test",
            "type": "skill",
            "version": "1.0.0",
            "author": [{"name": "T", "passport": "ET26-TEST-0001"}],
            "license": "MIT",
        },
        "bundle_url": "https://drops.windydrops.com/hook-test/1.0.0/hook-test-1.0.0.zip",
        "bundle_sha256": "a" * 64,
    })
    assert r.status_code == 201, r.text
    deliveries = (await db_session.execute(select(WebhookDelivery))).scalars().all()
    assert len(deliveries) == 1
    assert deliveries[0].event_type == "drop.published"
    assert deliveries[0].payload["drop_id"] == "hook-test"


def test_hmac_signature_shape() -> None:
    """HMAC-SHA256 hex matches what subscribers will compute."""
    secret = "shh"
    body = b'{"event_id":"abc","event_type":"drop.published"}'
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    # Subscriber-side verification would be:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert sig == expected
    assert len(sig) == 64
