"""library.py — UserLibrary model. The user's installed-drops pointer list."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class UserLibrary(Base):
    """Install = a row mapping (user_id, drop_id, version, installed_at).

    Per ADR-053 §"What install means in v1": install does NOT copy bytes
    into the user's storage; it adds a pointer. Surfaces query
    `?type=control-panel-template` to know what to load.
    """

    __tablename__ = "user_library"

    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    drop_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("drops.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    auto_update: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
