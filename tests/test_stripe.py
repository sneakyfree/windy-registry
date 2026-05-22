"""test_stripe.py — WD-27 + WD-28 acceptance tests.

Stripe SDK calls are monkey-patched at the service layer so the tests
don't hit Stripe. Real wire-up needs $STRIPE_SECRET_KEY +
$STRIPE_CONNECT_CLIENT_ID + $STRIPE_WEBHOOK_SECRET from the lockbox.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from windy_registry.database import Base, get_session
from windy_registry.middleware.auth import AuthUser, get_current_user
from windy_registry.models import Author, Drop, DropVersion, Tip
from windy_registry.models.drop import Drop as DropModel
from windy_registry.services import stripe_client


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
            subject="sub-1", issuer=None, tier="human",
            passport=None, integrity_band=None, clearance_level=None, raw_claims={},
        )

    app.dependency_overrides[get_session] = os_
    app.dependency_overrides[get_current_user] = ou
    return app


async def _seed_tip_enabled_drop(
    session: AsyncSession,
    drop_id: str,
    *,
    signer_passport: str = "ET26-TEST-0001",
    author_stripe_acct: str | None = "acct_test_123",
    tips_enabled: bool = True,
) -> None:
    from uuid import uuid4
    session.add(Drop(id=drop_id, type="skill", current_version="1.0.0"))
    session.add(DropVersion(
        drop_id=drop_id, version="1.0.0",
        manifest={
            "schema": "windy.drop.v1", "id": drop_id, "name": "x", "type": "skill",
            "version": "1.0.0", "author": [{"name": "T", "passport": signer_passport}],
            "license": "MIT", "monetization": {"tips_enabled": tips_enabled},
        },
        bundle_url=f"https://drops/{drop_id}.zip", bundle_sha256="a" * 64,
        signer_passport=signer_passport,
    ))
    # Seed the author row that owns the passport.
    session.add(Author(
        id=uuid4(), handle="testauth", display_name="Test Author",
        passport=signer_passport,
        stripe_account_id=author_stripe_acct,
        stripe_charges_enabled=True, stripe_payouts_enabled=True,
    ))
    await session.flush()


def test_stripe_connect_init_returns_oauth_url(db_session: AsyncSession) -> None:
    client = TestClient(_app(db_session))
    r = client.post("/api/v1/me/stripe/connect")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["oauth_url"].startswith("https://connect.stripe.com/express/oauth/authorize")


def test_stripe_status_unconnected_by_default(db_session: AsyncSession) -> None:
    client = TestClient(_app(db_session))
    r = client.get("/api/v1/me/stripe/status")
    assert r.status_code == 200
    assert r.json() == {"connected": False, "account_id": None,
                         "charges_enabled": False, "payouts_enabled": False}


@pytest.mark.asyncio
async def test_tip_creates_checkout_session_via_stripe(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_tip_enabled_drop(db_session, "tippable")
    fake_session = {"id": "cs_test_abc123", "url": "https://checkout.stripe.com/c/pay/cs_test_abc123"}
    monkeypatch.setattr(stripe_client, "create_tip_checkout", lambda **_: fake_session)

    client = TestClient(_app(db_session))
    r = client.post("/api/v1/drops/tippable/tip", json={"amount_cents": 500, "currency": "usd"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["session_id"] == "cs_test_abc123"

    rows = (await db_session.execute(select(Tip))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "pending"
    assert rows[0].amount_cents == 500


@pytest.mark.asyncio
async def test_tip_when_tips_not_enabled_400(db_session: AsyncSession) -> None:
    await _seed_tip_enabled_drop(db_session, "notip", tips_enabled=False)
    client = TestClient(_app(db_session))
    r = client.post("/api/v1/drops/notip/tip", json={"amount_cents": 500, "currency": "usd"})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "tips_not_enabled"


@pytest.mark.asyncio
async def test_tip_when_author_not_connected_400(db_session: AsyncSession) -> None:
    await _seed_tip_enabled_drop(db_session, "unwired", author_stripe_acct=None)
    client = TestClient(_app(db_session))
    r = client.post("/api/v1/drops/unwired/tip", json={"amount_cents": 500, "currency": "usd"})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "author_not_connected"


@pytest.mark.asyncio
async def test_tip_drop_not_found_404(db_session: AsyncSession) -> None:
    client = TestClient(_app(db_session))
    r = client.post("/api/v1/drops/ghost/tip", json={"amount_cents": 500, "currency": "usd"})
    assert r.status_code == 404


def test_tip_amount_validation(db_session: AsyncSession) -> None:
    client = TestClient(_app(db_session))
    # Below minimum ($1).
    r = client.post("/api/v1/drops/x/tip", json={"amount_cents": 50, "currency": "usd"})
    assert r.status_code == 422
    # Above maximum ($500).
    r = client.post("/api/v1/drops/x/tip", json={"amount_cents": 60_000, "currency": "usd"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_webhook_completes_pending_tip(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_tip_enabled_drop(db_session, "wh-drop")
    fake_session = {"id": "cs_test_xyz", "url": "https://checkout.stripe.com/x"}
    monkeypatch.setattr(stripe_client, "create_tip_checkout", lambda **_: fake_session)

    client = TestClient(_app(db_session))
    r = client.post("/api/v1/drops/wh-drop/tip", json={"amount_cents": 1000, "currency": "usd"})
    assert r.status_code == 201

    # Fake Stripe verifying the webhook signature → returns a synthesized event.
    fake_event = {
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_test_xyz"}},
    }
    monkeypatch.setattr(stripe_client, "verify_webhook", lambda **_: fake_event)

    r = client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"stripe-signature": "fake"},
    )
    assert r.status_code == 200

    tip_row = (await db_session.execute(select(Tip))).scalar_one()
    assert tip_row.status == "succeeded"
    # Author counter bumped.
    author = (await db_session.execute(
        select(Author).where(Author.passport == "ET26-TEST-0001")
    )).scalar_one()
    assert author.lifetime_tips_cents == 1000


def test_webhook_rejects_invalid_signature(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_: Any, **__: Any) -> None:
        raise ValueError("bad sig")
    monkeypatch.setattr(stripe_client, "verify_webhook", boom)
    client = TestClient(_app(db_session))
    r = client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"stripe-signature": "definitely-wrong"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_signature"
