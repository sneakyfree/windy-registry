"""stripe_connect.py — Stripe Connect Express OAuth init / callback / status.

POST /api/v1/me/stripe/connect   — returns the OAuth init URL
GET  /api/v1/me/stripe/callback  — handles Stripe's redirect; stores account_id
GET  /api/v1/me/stripe/status    — connected state for the current user
"""

from __future__ import annotations

# G14: HMAC-signed stateless state token. No in-memory dict (would lose
# pending callbacks on restart + couldn't be shared across multi-worker
# setups). Format: base64url(payload).base64url(hmac_sha256(payload)).
# 10-min TTL. STRIPE_STATE_SECRET env var pins the key across workers.
import base64 as _b64
import hashlib
import hashlib as _hashlib_mod
import hmac as _hmac_mod
import json as _json_mod
import os
import time as _time_mod
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..middleware.auth import AuthUser, get_current_user
from ..models import Author
from ..schemas.stripe_ import StripeConnectInitResponse, StripeStatusResponse
from ..services import stripe_client

router = APIRouter(prefix="/api/v1/me/stripe", tags=["stripe"])


def _state_secret() -> bytes:
    return (os.environ.get("STRIPE_STATE_SECRET")
            or "dev-state-secret-not-for-prod").encode("utf-8")


def _sign_state(user_uuid: str) -> str:
    payload = _json_mod.dumps(
        {"u": user_uuid, "iat": int(_time_mod.time())},
        separators=(",", ":"),
    ).encode("utf-8")
    enc = _b64.urlsafe_b64encode(payload).rstrip(b"=")
    sig = _hmac_mod.new(_state_secret(), enc, _hashlib_mod.sha256).digest()
    sig_enc = _b64.urlsafe_b64encode(sig).rstrip(b"=")
    return (enc + b"." + sig_enc).decode("ascii")


def _verify_state(state: str, max_age: int = 600) -> str | None:
    try:
        enc, sig_enc = state.split(".", 1)
        expected = _hmac_mod.new(_state_secret(), enc.encode(), _hashlib_mod.sha256).digest()
        actual = _b64.urlsafe_b64decode(sig_enc + "==")
        if not _hmac_mod.compare_digest(expected, actual):
            return None
        payload = _json_mod.loads(_b64.urlsafe_b64decode(enc + "==").decode("utf-8"))
        if _time_mod.time() - payload["iat"] > max_age:
            return None
        return payload["u"]
    except Exception:
        return None


def _user_uuid(user: AuthUser) -> UUID:
    digest = hashlib.sha256(user.subject.encode("utf-8")).digest()[:16]
    return UUID(bytes=digest)


@router.post("/connect", response_model=StripeConnectInitResponse)
async def init_connect(
    user: AuthUser = Depends(get_current_user),
) -> StripeConnectInitResponse:
    """Generate the Stripe Connect Express OAuth URL."""
    state = _sign_state(str(_user_uuid(user)))
    redirect_uri = os.environ.get(
        "STRIPE_CONNECT_REDIRECT_URI",
        "https://api.windydrops.com/api/v1/me/stripe/callback",
    )
    url = stripe_client.build_connect_oauth_url(state=state, redirect_uri=redirect_uri)
    return StripeConnectInitResponse(oauth_url=url)


@router.get("/callback")
async def callback(
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """Exchange the OAuth code for the connected account id + persist on Author row."""
    user_uuid_str = _verify_state(state)
    if user_uuid_str is None:
        raise HTTPException(status_code=400, detail={"error": "invalid_state"})

    try:
        token = stripe_client.exchange_oauth_code(code)
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error": "oauth_exchange_failed", "reason": str(e)}) from e

    account_id = token.get("stripe_user_id")
    if not account_id:
        raise HTTPException(status_code=400, detail={"error": "no_account_id_in_response"})

    # Persist on a synthetic Author row keyed by the user. The follow flow uses
    # author.handle as the FK; for Stripe linkage we look up by passport when
    # available and fall back to the synthetic UUID.
    # (For brevity v1 just stores account_id in a dict — real implementation
    # writes to the Author row matching the caller's identity. The Author
    # column already exists; the lookup-by-user wiring is left as a fast-follow.)
    from datetime import UTC, datetime
    # Use the user_uuid string as a synthetic handle so multiple users can connect.
    handle = f"u-{user_uuid_str[:8]}"
    author = (await session.execute(
        select(Author).where(Author.handle == handle)
    )).scalar_one_or_none()
    if author is None:
        from uuid import uuid4
        author = Author(id=uuid4(), handle=handle, display_name=handle)
        session.add(author)
    author.stripe_account_id = account_id
    author.stripe_charges_enabled = True
    author.stripe_payouts_enabled = True
    author.stripe_connected_at = datetime.now(UTC)
    await session.flush()

    # Redirect to the marketplace payout page (or return JSON in CLI flows).
    redirect_to = os.environ.get(
        "STRIPE_CONNECT_SUCCESS_URL",
        "https://windydrops.com/@me/payouts",
    )
    from fastapi.responses import RedirectResponse
    return RedirectResponse(redirect_to, status_code=303)


@router.get("/status", response_model=StripeStatusResponse)
async def status_endpoint(
    user: AuthUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StripeStatusResponse:
    """Return the caller's Stripe connection status."""
    uid = _user_uuid(user)
    handle = f"u-{str(uid)[:8]}"
    author = (await session.execute(
        select(Author).where(Author.handle == handle)
    )).scalar_one_or_none()
    if author is None or not author.stripe_account_id:
        return StripeStatusResponse(connected=False)
    return StripeStatusResponse(
        connected=True,
        account_id=author.stripe_account_id,
        charges_enabled=author.stripe_charges_enabled,
        payouts_enabled=author.stripe_payouts_enabled,
    )
