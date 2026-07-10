"""Passport revocation enforcement — CRL cache (finding A4).

Ported from windy-search #57. The EPT is a long-lived (365-day) bearer
token verified fully offline; its `rev` claim is baked at mint time, so a
revocation issued AFTER the token was minted is invisible to signature
verification (this is A4 — registry's `_resolve_user` decoded EPTs on
signature+expiry alone). This closes the hole with a TTL-cached copy of
the eternitas CRL:

  - eternitas publishes every revoked passport at `/.well-known/eternitas-crl`
    (shape `{"revoked": [{"passport": ...}]}`). Cached for `ttl_seconds`
    (default 30s) and consulted on every authenticated request, so a revoked
    passport is rejected within one TTL window at worst.

Registry has no inbound eternitas firehose consumer, so (unlike search/mind)
the CRL poll is the only signal — the `blacklist()` webhook path is retained
for parity and future use.

Failure semantics (ADR-026 §4 — graceful gates):
  - CRL reachable → refresh, decide.
  - CRL unreachable + cache younger than `max_stale_seconds` → serve stale
    (no new revocation can originate while eternitas is down).
  - CRL unreachable beyond `max_stale_seconds` (or never fetched) → fail
    CLOSED when `fail_closed` (production): 503 on gated routes.

Wired as a module singleton, initialised in create_app() only outside dev so
the test suite makes no network calls (the `rev`-claim + issuer checks in
middleware/auth.py always run regardless).
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)


class RevocationCache:
    """In-process revocation state: TTL-cached CRL + webhook blacklist."""

    def __init__(
        self,
        crl_url: str,
        ttl_seconds: int = 30,
        max_stale_seconds: int = 300,
        fail_closed: bool = True,
        suspended_ttl_seconds: int = 3600,
        http_timeout_seconds: float = 5.0,
    ) -> None:
        self.crl_url = crl_url
        self.ttl = ttl_seconds
        self.max_stale = max_stale_seconds
        self.fail_closed = fail_closed
        self.suspended_ttl = suspended_ttl_seconds
        self.http_timeout = http_timeout_seconds
        self._revoked: frozenset[str] = frozenset()
        self._fetched_at: float = 0.0  # 0.0 = never fetched successfully
        self._webhook_revoked: set[str] = set()
        self._webhook_suspended: dict[str, float] = {}  # passport → blacklisted_at
        self._lock = asyncio.Lock()

    def blacklist(self, passport: str, *, suspended: bool = False) -> None:
        if suspended:
            self._webhook_suspended[passport] = time.time()
        else:
            self._webhook_revoked.add(passport)

    async def _fetch(self) -> frozenset[str]:
        async with httpx.AsyncClient(timeout=self.http_timeout) as client:
            resp = await client.get(self.crl_url)
            resp.raise_for_status()
            body = resp.json()
        return frozenset(
            entry["passport"]
            for entry in body.get("revoked", [])
            if isinstance(entry, dict) and entry.get("passport")
        )

    async def _refresh_if_stale(self) -> None:
        now = time.time()
        if self._fetched_at and (now - self._fetched_at) < self.ttl:
            return
        async with self._lock:
            now = time.time()
            if self._fetched_at and (now - self._fetched_at) < self.ttl:
                return
            try:
                self._revoked = await self._fetch()
                self._fetched_at = time.time()
                return
            except Exception as e:
                age = (now - self._fetched_at) if self._fetched_at else None
                if age is not None and age < self.max_stale:
                    logger.warning(
                        "CRL refresh failed (%s); serving %.0fs-stale CRL "
                        "(max_stale=%ds)", e, age, self.max_stale,
                    )
                    return
                if not self.fail_closed:
                    logger.error(
                        "CRL unreachable past max_stale (%s) — failing OPEN "
                        "(non-production posture)", e,
                    )
                    return
                logger.error(
                    "CRL unreachable past max_stale (%s) — failing CLOSED", e,
                )
                raise HTTPException(
                    status_code=503,
                    detail="Revocation status unavailable — retry shortly",
                )

    async def check(self, passport: str) -> None:
        """Raise HTTPException(401) if the passport is revoked/suspended,
        HTTPException(503) if revocation state is unavailable fail-closed."""
        if passport in self._webhook_revoked:
            raise HTTPException(status_code=401, detail="EPT revoked")
        suspended_at = self._webhook_suspended.get(passport)
        if suspended_at is not None:
            if (time.time() - suspended_at) < self.suspended_ttl:
                raise HTTPException(status_code=401, detail="EPT suspended")
            del self._webhook_suspended[passport]
        await self._refresh_if_stale()
        if passport in self._revoked:
            raise HTTPException(status_code=401, detail="EPT revoked")


_cache: RevocationCache | None = None


def init_revocation_cache(
    crl_url: str,
    *,
    ttl_seconds: int = 30,
    max_stale_seconds: int = 300,
    fail_closed: bool = True,
) -> RevocationCache:
    global _cache
    _cache = RevocationCache(
        crl_url,
        ttl_seconds=ttl_seconds,
        max_stale_seconds=max_stale_seconds,
        fail_closed=fail_closed,
    )
    return _cache


def get_revocation_cache() -> RevocationCache | None:
    """The installed cache, or None when revocation isn't wired (dev/tests) —
    callers treat None as 'skip the CRL gate'."""
    return _cache
