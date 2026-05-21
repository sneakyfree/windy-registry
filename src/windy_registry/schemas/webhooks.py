"""webhooks.py — schemas for webhook subscriptions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

ALLOWED_EVENT_TYPES = [
    "drop.published",
    "drop.installed",
    "drop.uninstalled",
    "drop.forked",
    "drop.rated",
    "drop.tipped",
    "drop.withdrawn",
]


class SubscribeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    callback_url: HttpUrl
    event_types: list[str] = Field(..., min_length=1)
    secret: str = Field(..., min_length=16, max_length=256)


class SubscriptionRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    callback_url: str
    event_types: list[str]
    created_at: datetime
    last_delivery_at: datetime | None
    consecutive_failures: int


class SubscriptionList(BaseModel):
    items: list[SubscriptionRow]
    total: int
