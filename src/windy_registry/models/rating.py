"""rating.py — Rating model (1-5 stars + optional review)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class Rating(Base):
    """One rating per (user_id, drop_id). Updates on subsequent POSTs."""

    __tablename__ = "ratings"
    __table_args__ = (
        CheckConstraint("stars BETWEEN 1 AND 5", name="rating_stars_range"),
    )

    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    drop_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("drops.id", ondelete="CASCADE"), primary_key=True
    )
    stars: Mapped[int] = mapped_column(Integer, nullable=False)
    review: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
