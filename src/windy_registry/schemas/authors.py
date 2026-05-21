"""authors.py — schemas for author profile + follow graph."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuthorProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    handle: str
    display_name: str
    passport: str | None
    integrity_band: str | None
    clearance_level: str | None
    follower_count: int
    drop_count: int
    lifetime_tips_cents: int
    public_tips_disabled: bool
    joined_at: datetime


class FollowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    author_handle: str


class FollowRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    follower_user_id: UUID
    followed_handle: str
    created_at: datetime


class FollowList(BaseModel):
    items: list[FollowRow]
    total: int
