"""test_publish.py — WD-18 acceptance tests for POST /api/v1/drops.

Uses in-memory SQLite for the DB session (pgvector + JSONB unused on the
publish path) and overrides the auth + signature_verify dependencies for
the various scenarios.

The full Postgres integration test will live in a separate suite gated on
a CI Postgres service container.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from windy_registry.database import Base, get_session
from windy_registry.middleware.auth import AuthUser, get_current_user
from windy_registry.routes import drops as drops_route
from windy_registry.services import signature_verify
from windy_registry.services.signature_verify import VerifyResult

# Disable the pgvector column on SQLite (it's nullable and unused for publish).
from windy_registry.models.drop import Drop as DropModel  # noqa: F401  side-effect
from sqlalchemy import JSON, Column


def _swap_postgres_specific_cols_for_sqlite() -> None:
    """SQLite doesn't have pgvector; the column is nullable + unused on publish.
    Replace its type with a no-op JSON column so create_all() works.
    """
    tbl = DropModel.__table__
    if "embedding" in tbl.c and not isinstance(tbl.c.embedding.type, JSON):
        tbl.c.embedding.type = JSON()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    _swap_postgres_specific_cols_for_sqlite()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def app(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    from windy_registry.main import create_app
    app = create_app()

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def override_user() -> AuthUser:
        return AuthUser(
            subject="agent-1",
            issuer="https://api.eternitas.ai",
            tier="agent",
            passport="ET26-TEST-0001",
            integrity_band="fair",
            clearance_level="verified",
            raw_claims={"passport": "ET26-TEST-0001"},
        )

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = override_user
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


VALID_BUNDLE_SHA = "a" * 64
VALID_BUNDLE_URL = "https://drops.windydrops.com/test-drop/1.0.0/test-drop-1.0.0.zip"


def minimal_manifest(drop_id: str = "test-drop") -> dict[str, Any]:
    return {
        "schema": "windy.drop.v1",
        "id": drop_id,
        "name": "Test Drop",
        "type": "skill",
        "version": "1.0.0",
        "author": [{"name": "Test", "passport": "ET26-TEST-0001", "type": "human"}],
        "license": "MIT",
    }


def test_publish_valid_unsigned_free_drop_succeeds(client: TestClient) -> None:
    r = client.post(
        "/api/v1/drops",
        json={
            "manifest": minimal_manifest(),
            "bundle_url": VALID_BUNDLE_URL,
            "bundle_sha256": VALID_BUNDLE_SHA,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["drop_id"] == "test-drop"
    assert body["version"] == "1.0.0"
    assert body["signature_verified"] is False


def test_publish_with_invalid_manifest_returns_400(client: TestClient) -> None:
    m = minimal_manifest()
    del m["author"]
    r = client.post(
        "/api/v1/drops",
        json={"manifest": m, "bundle_url": VALID_BUNDLE_URL, "bundle_sha256": VALID_BUNDLE_SHA},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "schema_invalid"


def test_duplicate_version_returns_409(client: TestClient) -> None:
    payload = {
        "manifest": minimal_manifest(),
        "bundle_url": VALID_BUNDLE_URL,
        "bundle_sha256": VALID_BUNDLE_SHA,
    }
    assert client.post("/api/v1/drops", json=payload).status_code == 201
    r = client.post("/api/v1/drops", json=payload)
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "version_already_published"


def test_new_version_of_existing_drop_succeeds(client: TestClient) -> None:
    m = minimal_manifest()
    assert client.post(
        "/api/v1/drops",
        json={"manifest": m, "bundle_url": VALID_BUNDLE_URL, "bundle_sha256": VALID_BUNDLE_SHA},
    ).status_code == 201
    m2 = minimal_manifest()
    m2["version"] = "1.0.1"
    r = client.post(
        "/api/v1/drops",
        json={
            "manifest": m2,
            "bundle_url": VALID_BUNDLE_URL.replace("1.0.0", "1.0.1"),
            "bundle_sha256": "b" * 64,
        },
    )
    assert r.status_code == 201


def test_caller_passport_mismatch_returns_403(app: FastAPI, client: TestClient) -> None:
    async def override_other_user() -> AuthUser:
        return AuthUser(
            subject="agent-2",
            issuer="https://api.eternitas.ai",
            tier="agent",
            passport="ET26-OTHER-0002",
            integrity_band=None,
            clearance_level=None,
            raw_claims={},
        )

    app.dependency_overrides[get_current_user] = override_other_user
    r = client.post(
        "/api/v1/drops",
        json={
            "manifest": minimal_manifest(),
            "bundle_url": VALID_BUNDLE_URL,
            "bundle_sha256": VALID_BUNDLE_SHA,
        },
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "caller_passport_not_in_authors"


def test_paid_pricing_without_signature_returns_422(client: TestClient) -> None:
    m = minimal_manifest()
    m["pricing"] = {"type": "paid", "amount_cents": 500, "currency": "USD"}
    r = client.post(
        "/api/v1/drops",
        json={"manifest": m, "bundle_url": VALID_BUNDLE_URL, "bundle_sha256": VALID_BUNDLE_SHA},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "paid_requires_signature"


def test_invalid_signature_returns_422(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    async def fake_verify(*args: Any, **kwargs: Any) -> VerifyResult:
        return VerifyResult(valid=False, error="bad signature")

    monkeypatch.setattr(drops_route, "verify_signature", fake_verify)
    m = minimal_manifest()
    m["signature"] = {
        "algorithm": "ES256",
        "signer": {"passport": "ET26-TEST-0001"},
        "signed_at": "2026-05-21T00:00:00Z",
        "signed_digest": "sha256:" + "f" * 64,
        "signature": "X" * 88,
    }
    r = client.post(
        "/api/v1/drops",
        json={"manifest": m, "bundle_url": VALID_BUNDLE_URL, "bundle_sha256": VALID_BUNDLE_SHA},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "signature_invalid"


def test_valid_signature_records_signer_attributes(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    async def fake_verify(*args: Any, **kwargs: Any) -> VerifyResult:
        return VerifyResult(
            valid=True,
            signer_passport="ET26-TEST-0001",
            signer_integrity_band="fair",
            signer_clearance_level="verified",
        )

    monkeypatch.setattr(drops_route, "verify_signature", fake_verify)
    m = minimal_manifest()
    m["signature"] = {
        "algorithm": "ES256",
        "signer": {"passport": "ET26-TEST-0001", "integrity_band": "fair", "clearance_level": "verified"},
        "signed_at": "2026-05-21T00:00:00Z",
        "signed_digest": "sha256:" + "f" * 64,
        "signature": "X" * 88,
    }
    r = client.post(
        "/api/v1/drops",
        json={"manifest": m, "bundle_url": VALID_BUNDLE_URL, "bundle_sha256": VALID_BUNDLE_SHA},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["signature_verified"] is True
    assert body["signer_passport"] == "ET26-TEST-0001"
    assert body["signer_integrity_band"] == "fair"
    assert body["signer_clearance_level"] == "verified"
