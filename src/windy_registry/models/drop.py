"""drop.py — Drop, DropVersion, Fork models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class Drop(Base):
    """A drop — identified by its globally-unique id. Multiple versions per drop."""

    __tablename__ = "drops"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    current_version: Mapped[str] = mapped_column(String(64), nullable=False)
    forked_from: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("drops.id", ondelete="SET NULL"), nullable=True, index=True
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    versions: Mapped[list[DropVersion]] = relationship(
        back_populates="drop",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DropVersion(Base):
    """One immutable published version of a drop. (drop_id, version) is the natural key."""

    __tablename__ = "drop_versions"

    drop_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("drops.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    bundle_url: Mapped[str] = mapped_column(Text, nullable=False)
    bundle_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_verified: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    signer_passport: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    signer_integrity_band: Mapped[str | None] = mapped_column(String(32), nullable=True)
    signer_clearance_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    drop: Mapped[Drop] = relationship(back_populates="versions")


class Fork(Base):
    """Lineage record: fork_drop_id was forked from source_drop_id."""

    __tablename__ = "forks"

    source_drop_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("drops.id", ondelete="CASCADE"), primary_key=True
    )
    # Deliberately NOT an FK: forks claim their id before first publish
    # (is_published=False), so the drops row does not exist yet. Enforcing an
    # FK here 500'd every fork on Postgres (migration 0002 dropped it).
    fork_drop_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    is_published: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    forked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
