"""author.py — Author + Follow models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class Author(Base):
    """Public author profile derived from Eternitas passport.

    handle is derived deterministically from passport (or callsign in v1.1).
    Eternitas integrity_band is refreshed nightly; not request-time.
    """

    __tablename__ = "authors"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    handle: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    passport: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    integrity_band: Mapped[str | None] = mapped_column(String(32), nullable=True)
    clearance_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    integrity_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stripe_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stripe_charges_enabled: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    stripe_payouts_enabled: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    stripe_connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    public_tips_disabled: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    follower_count_cached: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    lifetime_tips_cents: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Follow(Base):
    """User follows Author for trending personalization + new-drop notifications."""

    __tablename__ = "follows"

    follower_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    followed_handle: Mapped[str] = mapped_column(
        String(64), ForeignKey("authors.handle", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
