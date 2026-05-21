"""ratings.py — request/response schemas for ratings + reviews."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RatingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stars: int = Field(..., ge=1, le=5)
    review: str | None = Field(default=None, max_length=1000)


class RatingRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: UUID
    drop_id: str
    stars: int
    review: str | None
    created_at: datetime
    updated_at: datetime


class RatingAggregate(BaseModel):
    """Bayesian-smoothed aggregate + histogram for marketplace cards."""

    drop_id: str
    stars_avg_raw: float | None
    bayesian_score: float
    review_count: int
    rating_count: int
    histogram: dict[int, int]  # {1: n, 2: n, ..., 5: n}


class RatingList(BaseModel):
    aggregate: RatingAggregate
    recent: list[RatingRow]
