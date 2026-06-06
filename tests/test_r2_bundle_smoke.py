"""test_r2_bundle_smoke.py — WD-13/WD-18 R2 + bundle integration smoke test.

Two layers, both exercising the *publish -> bundle pointer -> download* path:

1. Deterministic (always runs, no creds): publish a drop through the real
   app/router/DB stack, then fetch the drop detail and assert the stored
   bundle pointer (bundle_url + bundle_sha256) round-trips byte-for-byte and
   that the URL is rooted at the configured R2 public domain — i.e. an
   installer following the pointer would hit the right object. This is the
   self-contained smoke test that keeps CI green.

2. Live R2 round-trip (skipped unless R2 creds are present): actually PUT
   bundle bytes to the configured R2 bucket and GET them back via boto3,
   verifying the SHA-256 survives the upload/download. Gated behind the
   `r2` marker + a skipif on the credential env vars so CI without live R2
   stays green. Run explicitly with R2_* exported, e.g.
       R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... \
           pytest -m r2 tests/test_r2_bundle_smoke.py

Mirrors the in-memory-SQLite + dependency-override fixtures from
tests/test_publish.py so it imports/collects cleanly in the registry-only
environment.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from windy_registry.config import get_settings
from windy_registry.database import Base, get_session
from windy_registry.middleware.auth import AuthUser, get_current_user

# Import for the SQLite pgvector swap (mirrors test_publish.py).
from windy_registry.models.drop import Drop as DropModel  # noqa: F401  side-effect


def _swap_postgres_specific_cols_for_sqlite() -> None:
    """SQLite has no pgvector; the column is nullable + unused here.

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
def app(db_session: AsyncSession) -> FastAPI:
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


# --- bundle fixture: deterministic bytes + their SHA-256 -------------------

BUNDLE_BYTES = b"windy-drops smoke bundle v1\n"
BUNDLE_SHA256 = hashlib.sha256(BUNDLE_BYTES).hexdigest()
DROP_ID = "smoke-bundle-drop"
VERSION = "1.0.0"


def _bundle_key() -> str:
    return f"{DROP_ID}/{VERSION}/{DROP_ID}-{VERSION}.zip"


def _bundle_url() -> str:
    settings = get_settings()
    return f"https://{settings.r2_public_domain}/{_bundle_key()}"


def _manifest() -> dict:
    return {
        "schema": "windy.drop.v1",
        "id": DROP_ID,
        "name": "Smoke Bundle Drop",
        "type": "skill",
        "version": VERSION,
        "author": [{"name": "Smoke", "passport": "ET26-TEST-0001", "type": "human"}],
        "license": "MIT",
    }


# --- Layer 1: deterministic publish -> pointer -> retrieve ------------------


def test_publish_bundle_pointer_roundtrips_through_detail(client: TestClient) -> None:
    """Publish a drop, then follow the bundle pointer exposed on the detail
    endpoint and assert it round-trips exactly. An installer GETting the drop
    detail must receive the same R2 URL + SHA that publish recorded."""
    pub = client.post(
        "/api/v1/drops",
        json={
            "manifest": _manifest(),
            "bundle_url": _bundle_url(),
            "bundle_sha256": BUNDLE_SHA256,
        },
    )
    assert pub.status_code == 201, pub.text
    published = pub.json()
    assert published["bundle_url"] == _bundle_url()
    assert published["bundle_sha256"] == BUNDLE_SHA256

    # Now the "download pointer" path: fetch the drop detail (what the SDK /
    # installer reads) and confirm the bundle pointer survived persistence.
    detail = client.get(f"/api/v1/drops/{DROP_ID}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["bundle_url"] == _bundle_url()
    assert body["bundle_sha256"] == BUNDLE_SHA256
    # The pointer must be rooted at the configured R2 public domain so the
    # installer dereferences it against the right bucket-backed host.
    assert body["bundle_url"].startswith(f"https://{get_settings().r2_public_domain}/")


def test_r2_config_advertises_bundle_host(client: TestClient) -> None:
    """The public /.well-known/r2-config the SDK reads must point at the same
    public domain the published bundle URLs are rooted at."""
    r = client.get("/.well-known/r2-config")
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["public_domain"] == get_settings().r2_public_domain
    assert cfg["bucket"]


# --- Layer 2: live R2 round-trip (skip-if-creds-absent) ---------------------

_R2_CREDS_PRESENT = all(
    os.environ.get(k)
    for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
)


@pytest.mark.r2
@pytest.mark.skipif(
    not _R2_CREDS_PRESENT,
    reason="live R2 creds absent (set R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY)",
)
def test_live_r2_bundle_upload_download_roundtrip() -> None:
    """PUT the bundle bytes to R2 and GET them back, asserting the SHA-256
    survives. Only runs when live R2 creds are exported."""
    boto3 = pytest.importorskip("boto3")
    settings = get_settings()
    endpoint = f"https://{settings.r2_account_id}.r2.cloudflarestorage.com"
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    key = f"_smoke/{_bundle_key()}"
    s3.put_object(Bucket=settings.r2_bucket, Key=key, Body=BUNDLE_BYTES)
    try:
        got = s3.get_object(Bucket=settings.r2_bucket, Key=key)["Body"].read()
        assert hashlib.sha256(got).hexdigest() == BUNDLE_SHA256
        assert got == BUNDLE_BYTES
    finally:
        s3.delete_object(Bucket=settings.r2_bucket, Key=key)
