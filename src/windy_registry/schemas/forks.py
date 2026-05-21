"""forks.py — request/response schemas for /drops/{id}/fork + /forks."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ForkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_id: str
    new_name: str | None = None


class ForkRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    source_drop_id: str
    fork_drop_id: str
    is_published: bool
    forked_at: datetime


class ForkList(BaseModel):
    items: list[ForkRecord]
    total: int
