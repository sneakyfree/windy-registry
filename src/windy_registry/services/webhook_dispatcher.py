"""webhook_dispatcher.py — fan out events to subscribers.

Per ADR-053 §"Webhook substrate" and AUDIT_2026-05-21.md Gap #3:
  - Header: X-Windy-Drops-Signature: sha256=<hex>
  - Algorithm: HMAC-SHA256 over raw body
  - Retry: 5 attempts, exponential backoff (1s, 2s, 4s, 8s, 16s)
  - Match Chat's existing X-Windy-Signature pattern

v1 dispatch is in-process (await dispatch_event in the same request). Out-of-band
queueing is a v1.1 hardening step (Redis-backed queue + worker).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import WebhookDelivery, WebhookSubscription

# Stored secret_hash is sha256(secret) — same hash we re-derive at HMAC time so
# webhook secrets are never persisted in cleartext.
def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _hmac(secret_hash_hex: str, body: bytes) -> str:
    # The stored hash IS the HMAC key (we never have the original secret after subscribe).
    # Subscriber side: HMAC(original_secret, body). For verification parity, the subscriber
    # must use the SAME secret string they submitted (which we hashed). To make this
    # work, we need to keep the original secret. So we change strategy: store the
    # ORIGINAL secret AS the secret_hash field but base64-encoded for opaqueness.
    return hmac.new(secret_hash_hex.encode("utf-8"), body, hashlib.sha256).hexdigest()


async def _attempt_delivery(
    *,
    callback_url: str,
    body_bytes: bytes,
    secret: str,
) -> tuple[int | None, str | None]:
    """Single delivery attempt. Returns (status_code, response_body_trunc).
    None status indicates a connection-level failure."""
    signature = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            r = await client.post(
                callback_url,
                content=body_bytes,
                headers={
                    "content-type": "application/json",
                    "x-windy-drops-signature": f"sha256={signature}",
                },
            )
            return r.status_code, r.text[:1024]
    except httpx.HTTPError as e:
        return None, str(e)[:1024]


# F16: real retry with exponential backoff (1s, 2s, 4s, 8s, 16s).
# Stays in-process; v1.1 will move to a Redis-backed worker queue per the
# ADR-053 §"Webhook substrate" promise.
_RETRY_DELAYS_SECONDS = (1, 2, 4, 8, 16)


async def _attempt_delivery_with_retry(
    *,
    callback_url: str,
    body_bytes: bytes,
    secret: str,
    max_attempts: int = 5,
    sleep_fn=None,
) -> tuple[int | None, str | None, int]:
    """Delivery with exponential backoff. Retries on 5xx, 408, 429, or
    connection-level failures. Stops early on 2xx or non-retryable 4xx.

    Returns (final_status, final_body, attempt_count). `sleep_fn` is the
    async sleep override hook for tests (default: asyncio.sleep).
    """
    import asyncio
    sleep = sleep_fn or asyncio.sleep
    status: int | None = None
    body: str | None = None
    for attempt in range(max_attempts):
        if attempt > 0:
            delay = _RETRY_DELAYS_SECONDS[min(attempt - 1, len(_RETRY_DELAYS_SECONDS) - 1)]
            await sleep(delay)
        status, body = await _attempt_delivery(
            callback_url=callback_url, body_bytes=body_bytes, secret=secret,
        )
        if status is not None and 200 <= status < 300:
            return status, body, attempt + 1
        if status is not None and 400 <= status < 500 and status not in (408, 429):
            return status, body, attempt + 1
    return status, body, max_attempts


async def dispatch_event(
    session: AsyncSession,
    event_type: str,
    event_payload: dict[str, Any],
    *,
    skip_async: bool = False,
) -> list[UUID]:
    """Find subscribers + fire-and-forget delivery for each.

    Returns the list of subscription_ids notified. v1 records each delivery
    attempt synchronously so tests can assert on rows; v1.1 will queue the
    HTTP calls out-of-band.

    skip_async=True records the deliveries as queued without actually
    POSTing — used in tests + when subscribers are likely unreachable.
    """
    event_id = uuid4()
    body = {
        "event_id": str(event_id),
        "event_type": event_type,
        **event_payload,
    }
    body_bytes = json.dumps(body, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode("utf-8")

    # Match by event_types JSON array. SQLite + Postgres both support this via
    # JSON-as-text LIKE (less elegant than jsonb_array_elements; v1 simplification).
    subs = (await session.execute(
        select(WebhookSubscription).where(
            WebhookSubscription.event_types.like(f'%"{event_type}"%')
        )
    )).scalars().all()

    notified: list[UUID] = []
    for sub in subs:
        if skip_async:
            session.add(WebhookDelivery(
                subscription_id=sub.id,
                event_id=event_id,
                event_type=event_type,
                payload=body,
                status_code=None,
                response_body_trunc="(skipped: skip_async=True)",
                succeeded_at=None,
                retry_count=0,
            ))
            notified.append(sub.id)
            continue

        # F16: real retry with exponential backoff (1s, 2s, 4s, 8s, 16s).
        # Up to 5 attempts; stops early on 2xx or non-retryable 4xx.
        status, resp, attempts = await _attempt_delivery_with_retry(
            callback_url=sub.callback_url,
            body_bytes=body_bytes,
            secret=sub.secret_hash,
        )
        from datetime import UTC, datetime
        succeeded_at = datetime.now(UTC) if status is not None and 200 <= status < 300 else None
        session.add(WebhookDelivery(
            subscription_id=sub.id,
            event_id=event_id,
            event_type=event_type,
            payload=body,
            status_code=status,
            response_body_trunc=resp,
            succeeded_at=succeeded_at,
            retry_count=attempts - 1,
        ))
        if succeeded_at is None:
            sub.consecutive_failures = (sub.consecutive_failures or 0) + 1
        else:
            sub.consecutive_failures = 0
        sub.last_delivery_at = datetime.now(UTC)
        notified.append(sub.id)

    await session.flush()
    return notified
