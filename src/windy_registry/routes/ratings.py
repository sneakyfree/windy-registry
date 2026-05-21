"""ratings.py — POST /drops/{id}/rating + GET /drops/{id}/ratings.

WD-20. Per ADR-053 §"Ratings, reviews, and quality signals":
  - One rating per (user_id, drop_id); UPSERT on conflict
  - Bayesian smoothing: (review_count * raw_avg + min_count * prior_mean)
                       / (review_count + min_count)
    with prior_mean=3.5, min_count=5
  - Histogram pre-computed at read time (v1; materialized view if traffic grows)
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..middleware.auth import AuthUser, get_current_user
from ..models import Drop, Rating
from ..schemas.ratings import RatingAggregate, RatingList, RatingRequest, RatingRow

router = APIRouter(prefix="/api/v1/drops", tags=["ratings"])

PRIOR_MEAN = 3.5
MIN_COUNT = 5


def _user_uuid(user: AuthUser) -> UUID:
    digest = hashlib.sha256(user.subject.encode("utf-8")).digest()[:16]
    return UUID(bytes=digest)


@router.post(
    "/{drop_id}/rating",
    response_model=RatingRow,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Drop not found"},
    },
)
async def upsert_rating(
    drop_id: str,
    body: RatingRequest,
    user: AuthUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RatingRow:
    """UPSERT a rating. Subsequent calls from the same user replace the row."""
    if await session.get(Drop, drop_id) is None:
        raise HTTPException(status_code=404, detail={"error": "drop_not_found"})

    uid = _user_uuid(user)

    # Dialect-aware UPSERT — both Postgres + SQLite supported for tests.
    dialect_name = session.bind.dialect.name if session.bind else "postgresql"
    insert_stmt = (pg_insert if dialect_name == "postgresql" else sqlite_insert)(Rating).values(
        user_id=uid,
        drop_id=drop_id,
        stars=body.stars,
        review=body.review,
    )
    upsert = insert_stmt.on_conflict_do_update(
        index_elements=["user_id", "drop_id"],
        set_={"stars": body.stars, "review": body.review, "updated_at": func.now()},
    )
    await session.execute(upsert)
    await session.flush()

    row = await session.get(Rating, (uid, drop_id))
    return RatingRow.model_validate(row, from_attributes=True)


@router.get("/{drop_id}/ratings", response_model=RatingList)
async def list_ratings(
    drop_id: str,
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
) -> RatingList:
    """Aggregate + recent reviews."""
    if await session.get(Drop, drop_id) is None:
        raise HTTPException(status_code=404, detail={"error": "drop_not_found"})

    # Aggregate stats.
    agg_row = (await session.execute(
        select(func.avg(Rating.stars), func.count()).where(Rating.drop_id == drop_id)
    )).one()
    raw_avg = float(agg_row[0]) if agg_row[0] is not None else None
    rating_count = int(agg_row[1] or 0)
    review_count = (await session.execute(
        select(func.count()).where(Rating.drop_id == drop_id, Rating.review.isnot(None))
    )).scalar_one()
    bayesian = (
        (rating_count * (raw_avg or PRIOR_MEAN) + MIN_COUNT * PRIOR_MEAN)
        / (rating_count + MIN_COUNT)
    )

    # Histogram (1..5).
    hist_rows = (await session.execute(
        select(Rating.stars, func.count()).where(Rating.drop_id == drop_id).group_by(Rating.stars)
    )).all()
    histogram = {s: 0 for s in range(1, 6)}
    for stars, count in hist_rows:
        histogram[int(stars)] = int(count)

    # Recent reviews (capped).
    limit = min(max(limit, 1), 100)
    recent = (await session.execute(
        select(Rating)
        .where(Rating.drop_id == drop_id, Rating.review.isnot(None))
        .order_by(Rating.updated_at.desc())
        .limit(limit)
    )).scalars().all()

    return RatingList(
        aggregate=RatingAggregate(
            drop_id=drop_id,
            stars_avg_raw=raw_avg,
            bayesian_score=round(bayesian, 3),
            review_count=int(review_count),
            rating_count=rating_count,
            histogram=histogram,
        ),
        recent=[RatingRow.model_validate(r, from_attributes=True) for r in recent],
    )
