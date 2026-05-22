"""tips.py — POST /api/v1/drops/{id}/tip + POST /webhooks/stripe.

0% platform cut. Stripe Checkout Session with destination_charge to the
creator's connected account. Webhook records the tip + bumps the
author's lifetime_tips_cents.
"""

from __future__ import annotations

import hashlib
import os
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..middleware.auth import AuthUser, get_current_user
from ..models import Author, Drop, DropVersion, Tip
from ..schemas.stripe_ import TipRequest, TipResponse
from ..services import stripe_client

router = APIRouter(tags=["tips"])


def _user_uuid(user: AuthUser) -> UUID:
    digest = hashlib.sha256(user.subject.encode("utf-8")).digest()[:16]
    return UUID(bytes=digest)


@router.post(
    "/api/v1/drops/{drop_id}/tip",
    response_model=TipResponse,
    status_code=status.HTTP_201_CREATED,
)
async def tip(
    drop_id: str,
    body: TipRequest,
    user: AuthUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TipResponse:
    """Create a Stripe Checkout Session to tip the drop's author."""
    drop = await session.get(Drop, drop_id)
    if drop is None:
        raise HTTPException(status_code=404, detail={"error": "drop_not_found"})

    # Find the current version's manifest to learn tip-enabled state.
    version_row = (await session.execute(
        select(DropVersion).where(
            DropVersion.drop_id == drop_id,
            DropVersion.version == drop.current_version,
        )
    )).scalar_one_or_none()
    if version_row is None:
        raise HTTPException(status_code=500, detail={"error": "missing_current_version"})

    manifest = version_row.manifest or {}
    monetization = manifest.get("monetization") or {}
    if not monetization.get("tips_enabled"):
        raise HTTPException(
            status_code=400,
            detail={"error": "tips_not_enabled", "message": "author has not enabled tip jar on this drop"},
        )

    # Find the author by signer_passport → Author.passport.
    if not version_row.signer_passport:
        raise HTTPException(
            status_code=400,
            detail={"error": "no_passport", "message": "drop is unsigned; tip recipient cannot be determined"},
        )
    author = (await session.execute(
        select(Author).where(Author.passport == version_row.signer_passport)
    )).scalar_one_or_none()
    if author is None or not author.stripe_account_id:
        raise HTTPException(
            status_code=400,
            detail={"error": "author_not_connected", "message": "author has not connected Stripe"},
        )

    success_url = os.environ.get(
        "TIP_SUCCESS_URL", f"https://windydrops.com/d/{drop_id}?tipped=true"
    )
    cancel_url = os.environ.get(
        "TIP_CANCEL_URL", f"https://windydrops.com/d/{drop_id}"
    )

    try:
        sess = stripe_client.create_tip_checkout(
            account_id=author.stripe_account_id,
            amount_cents=body.amount_cents,
            currency=body.currency,
            drop_id=drop_id,
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail={"error": "stripe_failure", "reason": str(e)}) from e

    # Record a pending tip row; webhook flips it to succeeded.
    session.add(Tip(
        id=uuid4(),
        drop_id=drop_id,
        user_id=_user_uuid(user),
        author_handle=author.handle,
        amount_cents=body.amount_cents,
        currency=body.currency,
        stripe_session_id=sess["id"],
        status="pending",
    ))
    await session.flush()

    return TipResponse(checkout_url=sess["url"], session_id=sess["id"])


@router.post("/api/v1/webhooks/stripe", status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(..., alias="stripe-signature"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Verify the Stripe-Signature header + handle checkout.session.completed."""
    payload = await request.body()
    try:
        event = stripe_client.verify_webhook(payload=payload, sig_header=stripe_signature)
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error": "invalid_signature", "reason": str(e)}) from e

    event_type = event["type"] if isinstance(event, dict) else event.type
    if event_type == "checkout.session.completed":
        data = (event.get("data") if isinstance(event, dict) else event.data).get("object", {})
        session_id = data.get("id")
        if session_id:
            tip = (await session.execute(
                select(Tip).where(Tip.stripe_session_id == session_id)
            )).scalar_one_or_none()
            if tip is not None:
                tip.status = "succeeded"
                # Bump author's lifetime tips counter.
                author = (await session.execute(
                    select(Author).where(Author.handle == tip.author_handle)
                )).scalar_one_or_none()
                if author is not None:
                    author.lifetime_tips_cents = (author.lifetime_tips_cents or 0) + tip.amount_cents
                await session.flush()

                # Fan out drop.tipped webhook.
                from ..services.webhook_dispatcher import dispatch_event
                await dispatch_event(
                    session, "drop.tipped",
                    {"drop_id": tip.drop_id, "amount_cents": tip.amount_cents,
                     "currency": tip.currency, "author_handle": tip.author_handle},
                    skip_async=True,
                )

    return {"received": True}
