"""library.py — request/response schemas for /me/library endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class InstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drop_id: str
    version: str | None = None  # default: drops.current_version
    auto_update: bool = True


class UninstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drop_id: str


class LibraryRow(BaseModel):
    user_id: UUID
    drop_id: str
    version: str
    installed_at: datetime
    auto_update: bool


class LibraryList(BaseModel):
    items: list[LibraryRow]
    total: int
