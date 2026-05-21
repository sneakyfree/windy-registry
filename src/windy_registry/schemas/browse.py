"""browse.py — response schemas for GET /api/v1/drops + /trending + /{id}."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class DropSummary(BaseModel):
    """Card-level view for browse + trending lists."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    type: str
    current_version: str
    name: str | dict[str, str] | None = None
    subtitle: str | dict[str, str] | None = None
    tags: list[str] = []
    license: str | None = None
    locale_hint: str | None = None
    preview_url: str | None = None
    forked_from: str | None = None
    withdrawn_at: datetime | None = None
    created_at: datetime
    # Aggregates (populated from joins / cached counters).
    install_count: int = 0
    fork_count: int = 0
    rating_avg: float | None = None
    rating_count: int = 0
    signer_passport: str | None = None
    signer_integrity_band: str | None = None


class DropDetail(DropSummary):
    """Full detail — includes the manifest and the bundle pointer."""

    manifest: dict[str, Any]
    bundle_url: str
    bundle_sha256: str
    signature_verified: bool = False


class DropList(BaseModel):
    items: list[DropSummary]
    total: int
    cursor: str | None = None
    next_cursor: str | None = None


class R2Config(BaseModel):
    account_id: str | None
    bucket: str
    public_domain: str
