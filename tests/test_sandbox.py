"""test_sandbox.py — WD-23 acceptance tests for the sandboxed preview endpoint."""

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
from windy_registry.services.sandbox_host import (
    DEFAULT_MOCKS,
    build_preview_html,
)


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
        return AuthUser(subject="u", issuer=None, tier="human",
                        passport=None, integrity_band=None, clearance_level=None, raw_claims={})
    app.dependency_overrides[get_session] = os_
    app.dependency_overrides[get_current_user] = ou
    return app


async def _seed(session: AsyncSession, drop_id: str, drop_type: str = "control-panel-template") -> None:
    session.add(Drop(id=drop_id, type=drop_type, current_version="1.0.0"))
    session.add(DropVersion(
        drop_id=drop_id, version="1.0.0",
        manifest={"schema": "windy.drop.v1", "id": drop_id, "name": "x",
                  "type": drop_type, "version": "1.0.0", "author": [{"name": "T"}],
                  "license": "MIT"},
        bundle_url=f"https://drops/{drop_id}.zip", bundle_sha256="a" * 64,
    ))
    await session.flush()


def test_build_preview_html_for_each_drop_type() -> None:
    """Default mocks exist for every v1 reserved type."""
    for drop_type in [
        "control-panel-template", "skill", "tool",
        "theme", "voice-pack", "workflow",
    ]:
        html = build_preview_html(
            drop_id="x", version="1.0.0", drop_type=drop_type,
            public_bundle_domain="drops.windydrops.com",
        )
        assert "<!doctype html>" in html
        assert "sandbox=\"allow-scripts\"" in html
        assert "allow-same-origin" not in html, "must NOT grant same-origin"
        assert "frame-src https://drops.windydrops.com" in html
        assert "Content-Security-Policy" in html


def test_csp_blocks_inline_navigation_and_network() -> None:
    html = build_preview_html(
        drop_id="x", version="1.0.0", drop_type="skill",
        public_bundle_domain="drops.windydrops.com",
    )
    # No frame ancestors clause permits parent embedding via top-frame.
    assert "connect-src 'none'" in html
    assert "default-src 'none'" in html


def test_postmessage_target_origin_locked_to_bundle_domain() -> None:
    html = build_preview_html(
        drop_id="x", version="1.0.0", drop_type="skill",
        public_bundle_domain="drops.windydrops.com",
    )
    # postMessage targets the bundle domain only; parent never speaks to itself.
    assert 'TARGET_ORIGIN = "https://drops.windydrops.com"' in html


@pytest.mark.asyncio
async def test_preview_endpoint_serves_sandbox_html(db_session: AsyncSession) -> None:
    await _seed(db_session, "echo-hq")
    client = TestClient(_app(db_session))
    r = client.get("/api/v1/drops/echo-hq/preview")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "sandbox=\"allow-scripts\"" in r.text
    assert "drops.windydrops.com/echo-hq/1.0.0/render.html" in r.text


@pytest.mark.asyncio
async def test_preview_404_when_drop_missing(db_session: AsyncSession) -> None:
    client = TestClient(_app(db_session))
    r = client.get("/api/v1/drops/no-such/preview")
    assert r.status_code == 404


def test_default_mocks_have_required_schema_fields() -> None:
    """control-panel-template mock has windy.vitals.v1 + windy.fleet.v1 shapes."""
    cp = DEFAULT_MOCKS["control-panel-template"]
    assert cp["vitals"]["schema"] == "windy.vitals.v1"
    assert cp["fleet"]["schema"] == "windy.fleet.v1"
