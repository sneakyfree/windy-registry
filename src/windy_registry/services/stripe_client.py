"""stripe_client.py — thin wrapper over the Stripe SDK so it can be
monkey-patched in tests without touching every call site.

Per ADR-053 §"Monetization v1 (tip jars)":
  - Stripe Connect Express (Stripe handles KYC, tax, bank linkage)
  - 0% platform cut on tips (application_fee_amount=0)
  - Tips ship in v1; paid drops in v1.1

Token lives in $STRIPE_SECRET_KEY (sourced from
~/kit-army-config/ACCESS_LOCKBOX.md §"Stripe Connect").
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import stripe


@dataclass
class StripeOAuthLink:
    url: str
    state: str


def _set_key() -> None:
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if key:
        stripe.api_key = key


def build_connect_oauth_url(state: str, redirect_uri: str) -> str:
    """Stripe Connect Express OAuth init URL.

    State is a CSRF token + user identity (caller derives + verifies on
    callback). redirect_uri must exactly match the configured callback URL
    in the Stripe Dashboard.
    """
    base = "https://connect.stripe.com/express/oauth/authorize"
    return (
        f"{base}?response_type=code&client_id={os.environ.get('STRIPE_CONNECT_CLIENT_ID', 'ca_TODO')}"
        f"&scope=read_write&state={state}&redirect_uri={redirect_uri}"
    )


def exchange_oauth_code(code: str) -> dict:
    """POST to Stripe to exchange the OAuth code for an account id."""
    _set_key()
    return stripe.OAuth.token(grant_type="authorization_code", code=code)


def account_status(account_id: str) -> dict:
    _set_key()
    return stripe.Account.retrieve(account_id)


def create_tip_checkout(
    *,
    account_id: str,
    amount_cents: int,
    currency: str,
    drop_id: str,
    success_url: str,
    cancel_url: str,
) -> dict:
    """Create a Stripe Checkout Session with destination_charge to the
    creator's connected account. 0% platform fee per ADR-053.
    """
    _set_key()
    return stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": currency,
                "product_data": {"name": f"Tip for {drop_id}"},
                "unit_amount": amount_cents,
            },
            "quantity": 1,
        }],
        payment_intent_data={
            "transfer_data": {"destination": account_id},
            "application_fee_amount": 0,  # 0% platform cut per ADR-053
            "metadata": {"drop_id": drop_id, "kind": "tip"},
        },
        success_url=success_url,
        cancel_url=cancel_url,
    )


def verify_webhook(*, payload: bytes, sig_header: str) -> stripe.Event:
    """Verify the Stripe-Signature header. Raises stripe.SignatureVerificationError on mismatch."""
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    return stripe.Webhook.construct_event(payload, sig_header, secret)
