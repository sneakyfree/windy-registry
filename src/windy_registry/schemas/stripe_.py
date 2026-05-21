"""stripe_.py — schemas for Stripe Connect + tip endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class StripeConnectInitResponse(BaseModel):
    oauth_url: HttpUrl


class StripeStatusResponse(BaseModel):
    connected: bool
    account_id: str | None = None
    charges_enabled: bool = False
    payouts_enabled: bool = False


class TipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount_cents: int = Field(..., ge=100, le=50_000)  # $1 .. $500
    currency: str = Field(default="usd", pattern=r"^[a-z]{3}$")


class TipResponse(BaseModel):
    checkout_url: HttpUrl
    session_id: str
