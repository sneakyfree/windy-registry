"""test_round1_gaps.py — verify F10, F11, F14, F17, F18, G9, G13, G19 closures."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from windy_registry.database import Base, get_session
from windy_registry.middleware.auth import AuthUser, get_current_user, get_current_user_optional
from windy_registry.models import Author, Drop, DropVersion, Follow, UserLibrary
from windy_registry.models.drop import Drop as DropModel
from windy_registry.services.handle import derive_handle_candidates, ensure_unique_handle
from windy_registry.services.i18n import parse_accept_language, resolve_i18n


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
    async def ouo() -> AuthUser | None:
        return await ou()
    app.dependency_overrides[get_session] = os_
    app.dependency_overrides[get_current_user] = ou
    app.dependency_overrides[get_current_user_optional] = ouo
    return app


async def _seed(session: AsyncSession, drop_id: str, **kwargs) -> None:
    drop_type = kwargs.pop("drop_type", "skill")
    manifest = {
        "schema": "windy.drop.v1", "id": drop_id, "name": kwargs.pop("name", "x"),
        "type": drop_type, "version": "1.0.0",
        "author": kwargs.pop("author", [{"name": "T"}]),
        "license": "MIT",
    }
    manifest.update(kwargs.pop("extras", {}))
    session.add(Drop(id=drop_id, type=drop_type, current_version="1.0.0",
                      forked_from=kwargs.pop("forked_from", None)))
    session.add(DropVersion(
        drop_id=drop_id, version="1.0.0", manifest=manifest,
        bundle_url=f"https://drops/{drop_id}.zip", bundle_sha256="a" * 64,
        signer_passport=kwargs.pop("signer_passport", None),
    ))
    await session.flush()


# ---- F10: /oembed ----

@pytest.mark.asyncio
async def test_oembed_returns_rich_json(db_session: AsyncSession) -> None:
    await _seed(db_session, "ode-drop")
    client = TestClient(_app(db_session))
    r = client.get("/api/v1/drops/ode-drop/oembed")
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "rich"
    assert body["provider_name"] == "Windy Drops"
    assert "iframe" in body["html"]
    assert body["width"] == 600


@pytest.mark.asyncio
async def test_oembed_xml_format_returns_501(db_session: AsyncSession) -> None:
    await _seed(db_session, "ode-drop")
    client = TestClient(_app(db_session))
    r = client.get("/api/v1/drops/ode-drop/oembed?format=xml")
    assert r.status_code == 501


# ---- F11: /og ----

@pytest.mark.asyncio
async def test_og_metadata_returns_canonical_shape(db_session: AsyncSession) -> None:
    await _seed(db_session, "og-drop")
    client = TestClient(_app(db_session))
    r = client.get("/api/v1/drops/og-drop/og")
    assert r.status_code == 200
    body = r.json()
    for key in ("title", "description", "image_url", "canonical_url",
                "embed_iframe_url", "type", "site_name"):
        assert key in body
    assert body["canonical_url"] == "https://windydrops.com/d/og-drop"
    assert body["image_url"].endswith("/preview.png")


# ---- F13: deterministic handle derivation ----

def test_handle_from_callsign() -> None:
    assert derive_handle_candidates({"callsign": "Echo", "name": "Kit OC5"})[0] == "echo"


def test_handle_from_passport_fallback() -> None:
    cands = derive_handle_candidates({"passport": "ET26-OCKM-Y005", "name": "Kit OC5"})
    # No callsign → uses passport-derived "u-ockmy005" first.
    assert cands[0] == "u-ockmy005"


def test_handle_from_name_when_no_callsign_no_passport() -> None:
    cands = derive_handle_candidates({"name": "Grant Whitmer"})
    assert "grant-whitmer" in cands


@pytest.mark.asyncio
async def test_ensure_unique_handle_suffixes_on_collision(db_session: AsyncSession) -> None:
    db_session.add(Author(id=uuid4(), handle="ada", display_name="Ada A",
                            passport="ET26-AAAA-AAAA"))
    await db_session.flush()
    # Same passport → returns the existing handle (idempotent).
    h1 = await ensure_unique_handle(db_session, "ada", passport="ET26-AAAA-AAAA")
    assert h1 == "ada"
    # Different passport → suffix-disambiguated.
    h2 = await ensure_unique_handle(db_session, "ada", passport="ET26-BBBB-BBBB")
    assert h2 == "ada-2"


# ---- F14: Accept-Language i18n ----

def test_parse_accept_language_with_q_values() -> None:
    out = parse_accept_language("en-US,en;q=0.9,ko;q=0.6,*;q=0.1")
    assert out[0] == "en-us"
    assert "ko" in out


def test_resolve_i18n_prefix_match() -> None:
    obj = {"default": "en", "en": "Hello", "ko": "안녕"}
    assert resolve_i18n(obj, "en-US") == "Hello"  # prefix match


def test_resolve_i18n_falls_back_to_default() -> None:
    obj = {"default": "en", "en": "Hello", "ko": "안녕"}
    assert resolve_i18n(obj, "fr-FR,fr;q=0.9") == "Hello"  # falls back to default


def test_resolve_i18n_passes_strings_through() -> None:
    assert resolve_i18n("plain string", "en") == "plain string"
    assert resolve_i18n(None, "en") is None


@pytest.mark.asyncio
async def test_browse_resolves_i18n_per_request_header(db_session: AsyncSession) -> None:
    await _seed(db_session, "i18n-drop", extras={"name": {"default": "en", "en": "Hi", "ko": "안녕"}})
    client = TestClient(_app(db_session))
    r = client.get("/api/v1/drops", headers={"Accept-Language": "ko"})
    body = r.json()
    item = next(i for i in body["items"] if i["id"] == "i18n-drop")
    assert item["name"] == "안녕"


# ---- F17: depends_on composition resolver ----

@pytest.mark.asyncio
async def test_install_recursively_installs_dependencies(db_session: AsyncSession) -> None:
    await _seed(db_session, "child")
    await _seed(db_session, "parent",
                extras={"depends_on": [{"id": "child", "type": "skill"}]})
    client = TestClient(_app(db_session))
    r = client.post("/api/v1/me/library/install", json={"drop_id": "parent"})
    assert r.status_code == 201
    listing = client.get("/api/v1/me/library").json()
    ids = {i["drop_id"] for i in listing["items"]}
    assert {"parent", "child"} == ids


@pytest.mark.asyncio
async def test_install_skips_missing_dependencies_silently(db_session: AsyncSession) -> None:
    await _seed(db_session, "lonely",
                extras={"depends_on": [{"id": "no-such", "type": "skill"}]})
    client = TestClient(_app(db_session))
    r = client.post("/api/v1/me/library/install", json={"drop_id": "lonely"})
    assert r.status_code == 201
    assert client.get("/api/v1/me/library").json()["total"] == 1


# ---- F18: follower-aware trending ----

@pytest.mark.asyncio
async def test_trending_boosts_drops_from_followed_authors(db_session: AsyncSession) -> None:
    # Two drops, same install count. One has a signer the user follows.
    await _seed(db_session, "popular-unrelated")
    await _seed(db_session, "popular-followed", signer_passport="ET26-FOLLOW-0001")
    # Author row for the followed passport.
    db_session.add(Author(id=uuid4(), handle="followed-author", display_name="F",
                            passport="ET26-FOLLOW-0001"))
    # Hash 'u-1' subject the same way the route does it.
    import hashlib
    from uuid import UUID
    uid = UUID(bytes=hashlib.sha256(b"u-1").digest()[:16])
    db_session.add(Follow(follower_user_id=uid, followed_handle="followed-author"))
    # Tie-breaking install counts to make ordering depend on the follower boost.
    db_session.add(UserLibrary(user_id=uuid4(), drop_id="popular-unrelated", version="1.0.0"))
    db_session.add(UserLibrary(user_id=uuid4(), drop_id="popular-followed", version="1.0.0"))
    await db_session.flush()

    client = TestClient(_app(db_session))
    r = client.get("/api/v1/drops/trending")
    ids = [i["id"] for i in r.json()["items"]]
    assert ids[0] == "popular-followed", f"expected followed-author drop first, got {ids}"


# ---- G13: health/full real probes ----

def test_health_endpoint_still_ok() -> None:
    from windy_registry.main import create_app
    from windy_registry.routes.health import reset_probe_cache_for_tests
    reset_probe_cache_for_tests()
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_health_full_handles_unconfigured_db(db_session: AsyncSession) -> None:
    """When DB isn't wired, /health/full reports 'unconfigured' for DB, not a 500."""
    from windy_registry.main import create_app
    from windy_registry.routes.health import reset_probe_cache_for_tests
    reset_probe_cache_for_tests()
    client = TestClient(create_app())
    r = client.get("/health/full")
    assert r.status_code == 200
    body = r.json()
    assert body["database"] == "unconfigured"
    assert "r2_bucket" in body
    assert "jwks" in body


# ---- G19: JWKS refresh on kid miss ----

@pytest.mark.asyncio
async def test_jwks_refresh_on_kid_miss(monkeypatch) -> None:
    """If a token's kid isn't in cached JWKS, refresh once + retry.

    We only assert _fetch_jwks is called twice when the kid never matches —
    the actual key construction is exercised by the real test_auth.py suite.
    """
    from windy_registry.middleware import auth as auth_module
    from windy_registry.middleware.auth import _try_verify

    auth_module.reset_jwks_cache_for_tests()

    call_count = {"n": 0}
    async def fake_fetch(url):
        call_count["n"] += 1
        return {"keys": []}  # kid never matches → forces refresh
    monkeypatch.setattr(auth_module, "_fetch_jwks", fake_fetch)

    from jose import jwt
    token = jwt.encode({"sub": "x"}, "secret", algorithm="HS256", headers={"kid": "new-kid"})
    result = await _try_verify(token, "https://example.com/jwks", ["RS256"])
    assert result is None, "expected None when kid never matches even after refresh"
    assert call_count["n"] == 2, "expected JWKS to be refetched after kid miss"
