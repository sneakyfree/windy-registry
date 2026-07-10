"""[A4] RevocationCache unit tests — registry mirrors windy-search #57.

Uses asyncio.run wrappers so the tests don't depend on the repo's async-test
configuration (the rest of the auth suite is sync TestClient).
"""
import asyncio

import pytest
from fastapi import HTTPException

from windy_registry.middleware.revocation import RevocationCache


def test_crl_revoked_passport_rejected_401():
    async def _run():
        cache = RevocationCache(crl_url="http://unused", ttl_seconds=999, fail_closed=False)

        async def _fetch():
            return frozenset({"ET26-REVK-0001"})

        cache._fetch = _fetch  # type: ignore[method-assign]
        await cache.check("ET26-LIVE-0001")  # clear → no raise
        with pytest.raises(HTTPException) as ei:
            await cache.check("ET26-REVK-0001")
        assert ei.value.status_code == 401

    asyncio.run(_run())


def test_webhook_blacklist_rejected_401():
    async def _run():
        cache = RevocationCache(crl_url="http://unused", ttl_seconds=999, fail_closed=False)
        cache.blacklist("ET26-HOOK-0001")
        with pytest.raises(HTTPException) as ei:
            await cache.check("ET26-HOOK-0001")
        assert ei.value.status_code == 401

    asyncio.run(_run())


def test_crl_unreachable_fails_closed_503():
    async def _run():
        cache = RevocationCache(
            crl_url="http://127.0.0.1:1/nope",
            ttl_seconds=0,
            max_stale_seconds=0,
            fail_closed=True,
            http_timeout_seconds=0.2,
        )
        with pytest.raises(HTTPException) as ei:
            await cache.check("ET26-ANY-0001")
        assert ei.value.status_code == 503

    asyncio.run(_run())
