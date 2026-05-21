"""webhooks.py — POST /api/v1/webhooks/subscribe + DELETE.

The "secret" the subscriber supplies at subscribe time is stored verbatim in
secret_hash (which doubles as the HMAC key for future deliveries — see
webhook_dispatcher.py). Subscribers HMAC-verify incoming deliveries with the
same secret string they provided.

For v1 we trade a little secret-storage hygiene for delivery-time
verification simplicity. v1.1 can move to a separate sealed table + KMS
envelope if needed.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..middleware.auth import AuthUser, get_current_user
from ..models import WebhookSubscription
from ..schemas.webhooks import (
    ALLOWED_EVENT_TYPES,
    SubscribeRequest,
    SubscriptionList,
    SubscriptionRow,
)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


@router.post(
    "/subscribe",
    response_model=SubscriptionRow,
    status_code=status.HTTP_201_CREATED,
)
async def subscribe(
    body: SubscribeRequest,
    user: AuthUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionRow:
    """Create a subscription for one or more event types.

    Unknown event_types are accepted (forward-compat per Invariant 12 of the
    DNA strand plan); they simply won't match any current emitter.
    """
    sub = WebhookSubscription(
        callback_url=str(body.callback_url),
        event_types=body.event_types,
        secret_hash=body.secret,  # stored as HMAC key; see module docstring
    )
    session.add(sub)
    await session.flush()
    return SubscriptionRow.model_validate(sub, from_attributes=True)


@router.delete("/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(
    subscription_id: UUID,
    user: AuthUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    sub = await session.get(WebhookSubscription, subscription_id)
    if sub is None:
        raise HTTPException(status_code=404, detail={"error": "subscription_not_found"})
    await session.delete(sub)
    await session.flush()


@router.get("", response_model=SubscriptionList)
async def list_subscriptions(
    user: AuthUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionList:
    rows = (await session.execute(select(WebhookSubscription))).scalars().all()
    return SubscriptionList(
        items=[SubscriptionRow.model_validate(r, from_attributes=True) for r in rows],
        total=len(rows),
    )


@router.get("/event-types")
def supported_event_types() -> dict[str, list[str]]:
    """Public — what event_types can be subscribed to."""
    return {"event_types": ALLOWED_EVENT_TYPES}
