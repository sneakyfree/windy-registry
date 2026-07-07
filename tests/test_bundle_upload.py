"""test_bundle_upload.py — PUT /api/v1/drops/{id}/versions/{v}/bundle.

Mirrors the in-memory-SQLite + dependency-override fixtures from
tests/test_publish.py. R2 network calls are stubbed by monkeypatching
services.r2_upload.r2_client with a recording fake.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from windy_registry.config import get_settings
from windy_registry.database import Base, get_session
from windy_registry.middleware.auth import AuthUser, get_current_user
from windy_registry.models.drop import Drop as DropModel  # noqa: F401  side-effect
from windy_registry.services import r2_upload
from windy_registry.services.r2_upload import (
    BundleUploadError,
    validate_and_extract,
)

PASSPORT = "ET26-TEST-0001"


def _swap_postgres_specific_cols_for_sqlite() -> None:
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


class FakeR2Client:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:  # noqa: N803
        self.objects[Key] = (Body, ContentType)


@pytest.fixture
def fake_r2(monkeypatch: pytest.MonkeyPatch) -> FakeR2Client:
    fake = FakeR2Client()
    monkeypatch.setattr(r2_upload, "r2_client", lambda settings: fake)
    return fake


@pytest.fixture
def app(db_session: AsyncSession, fake_r2: FakeR2Client) -> FastAPI:
    from windy_registry.main import create_app

    app = create_app()

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def override_user() -> AuthUser:
        return AuthUser(
            subject="agent-1",
            issuer="https://api.eternitas.ai",
            tier="agent",
            passport=PASSPORT,
            integrity_band="fair",
            clearance_level="verified",
            raw_claims={"passport": PASSPORT},
        )

    def override_settings():
        s = get_settings()
        return s.model_copy(
            update={
                "r2_account_id": "test-account",
                "r2_access_key_id": "test-key",
                "r2_secret_access_key": "test-secret",
            }
        )

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_settings] = override_settings
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def manifest(drop_id: str = "up-test", passport: str = PASSPORT) -> dict[str, Any]:
    return {
        "schema": "windy.drop.v1",
        "id": drop_id,
        "name": "Upload Test",
        "type": "skill",
        "version": "1.0.0",
        "author": [{"name": "Test", "passport": passport, "type": "human"}],
        "license": "MIT",
    }


def publish(client: TestClient, zip_bytes: bytes, drop_id: str = "up-test") -> None:
    r = client.post(
        "/api/v1/drops",
        json={
            "manifest": manifest(drop_id),
            "bundle_url": f"https://drops.windydrops.com/{drop_id}/1.0.0/{drop_id}-1.0.0.zip",
            "bundle_sha256": hashlib.sha256(zip_bytes).hexdigest(),
        },
    )
    assert r.status_code == 201, r.text


def test_upload_happy_path_pushes_zip_and_members(client: TestClient, fake_r2: FakeR2Client) -> None:
    zip_bytes = make_zip({"SKILL.md": b"# hi", "render.html": b"<html></html>"})
    publish(client, zip_bytes)
    r = client.put("/api/v1/drops/up-test/versions/1.0.0/bundle", content=zip_bytes)
    assert r.status_code == 200, r.text
    keys = set(r.json()["uploaded"])
    assert keys == {
        "up-test/1.0.0/up-test-1.0.0.zip",
        "up-test/1.0.0/SKILL.md",
        "up-test/1.0.0/render.html",
    }
    body, ctype = fake_r2.objects["up-test/1.0.0/up-test-1.0.0.zip"]
    assert body == zip_bytes and ctype == "application/zip"
    _, html_type = fake_r2.objects["up-test/1.0.0/render.html"]
    assert html_type == "text/html"


def test_upload_sha_mismatch_422(client: TestClient) -> None:
    zip_bytes = make_zip({"SKILL.md": b"# hi"})
    publish(client, zip_bytes)
    other = make_zip({"SKILL.md": b"# tampered"})
    r = client.put("/api/v1/drops/up-test/versions/1.0.0/bundle", content=other)
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "bundle_sha_mismatch"


def test_upload_unknown_version_404(client: TestClient) -> None:
    r = client.put("/api/v1/drops/nope/versions/9.9.9/bundle", content=b"zzz")
    assert r.status_code == 404


def test_upload_non_author_403(client: TestClient, app: FastAPI) -> None:
    zip_bytes = make_zip({"SKILL.md": b"# hi"})
    publish(client, zip_bytes)

    async def stranger() -> AuthUser:
        return AuthUser(
            subject="agent-2",
            issuer="https://api.eternitas.ai",
            tier="agent",
            passport="ET26-TEST-0002",
            integrity_band="fair",
            clearance_level="verified",
            raw_claims={"passport": "ET26-TEST-0002"},
        )

    app.dependency_overrides[get_current_user] = stranger
    r = client.put("/api/v1/drops/up-test/versions/1.0.0/bundle", content=zip_bytes)
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "not_author"


def test_upload_oversize_413(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    zip_bytes = make_zip({"SKILL.md": b"# hi"})
    publish(client, zip_bytes)
    monkeypatch.setattr(r2_upload, "MAX_BUNDLE_BYTES", 4)
    # The route imports the cap at call time; patch the module attr it reads.
    import windy_registry.routes.drops as drops_mod  # noqa: F401

    r = client.put(
        "/api/v1/drops/up-test/versions/1.0.0/bundle",
        content=zip_bytes,
        headers={"content-length": str(len(zip_bytes))},
    )
    assert r.status_code == 413


def test_upload_r2_unconfigured_503(client: TestClient, app: FastAPI) -> None:
    zip_bytes = make_zip({"SKILL.md": b"# hi"})
    publish(client, zip_bytes)

    def no_r2():
        s = get_settings()
        return s.model_copy(
            update={"r2_account_id": None, "r2_access_key_id": None, "r2_secret_access_key": None}
        )

    app.dependency_overrides[get_settings] = no_r2
    r = client.put("/api/v1/drops/up-test/versions/1.0.0/bundle", content=zip_bytes)
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "r2_not_configured"


def test_upload_bad_zip_422(client: TestClient) -> None:
    bad = b"not a zip at all"
    r = client.post(
        "/api/v1/drops",
        json={
            "manifest": manifest("bad-zip"),
            "bundle_url": "https://drops.windydrops.com/bad-zip/1.0.0/bad-zip-1.0.0.zip",
            "bundle_sha256": hashlib.sha256(bad).hexdigest(),
        },
    )
    assert r.status_code == 201
    r = client.put("/api/v1/drops/bad-zip/versions/1.0.0/bundle", content=bad)
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "not_a_zip"


# ---- pure extraction-safety unit tests ----


def test_zip_slip_member_rejected() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.txt", b"pwn")
    with pytest.raises(BundleUploadError) as e:
        validate_and_extract(buf.getvalue())
    assert e.value.error == "unsafe_zip_member"


def test_absolute_member_rejected() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("/etc/passwd", b"pwn")
    with pytest.raises(BundleUploadError) as e:
        validate_and_extract(buf.getvalue())
    assert e.value.error == "unsafe_zip_member"


def test_empty_zip_rejected() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w"):
        pass
    with pytest.raises(BundleUploadError) as e:
        validate_and_extract(buf.getvalue())
    assert e.value.error == "empty_bundle"


def test_nested_paths_allowed() -> None:
    z = make_zip({"assets/img/logo.svg": b"<svg/>", "SKILL.md": b"# x"})
    files = validate_and_extract(z)
    assert set(files) == {"assets/img/logo.svg", "SKILL.md"}
