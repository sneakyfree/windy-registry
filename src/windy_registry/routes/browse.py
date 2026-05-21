"""browse.py — GET /api/v1/drops + /trending + /{id} + /.well-known/r2-config.

WD-16. The trending algorithm is a v1 sketch — tuned empirically post-launch.
Embeddings + vector similarity land at M9+ (see ADR-053 §"AI integration roadmap").
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..database import get_session
from ..models import Drop, DropVersion, Fork, Rating, UserLibrary
from ..schemas.browse import DropDetail, DropList, DropSummary, R2Config

router = APIRouter(tags=["drops"])
public_router = APIRouter(tags=["meta"])


def _summary_from(
    drop: Drop,
    *,
    manifest: dict | None,
    install_count: int = 0,
    fork_count: int = 0,
    rating_avg: float | None = None,
    rating_count: int = 0,
    signer_passport: str | None = None,
    signer_integrity_band: str | None = None,
) -> DropSummary:
    m = manifest or {}
    return DropSummary(
        id=drop.id,
        type=drop.type,
        current_version=drop.current_version,
        name=m.get("name"),
        subtitle=m.get("subtitle"),
        tags=m.get("tags") or [],
        license=m.get("license"),
        locale_hint=m.get("locale_hint"),
        preview_url=None,  # filled per ADR-053 §"Bundle storage" once R2 wires up
        forked_from=drop.forked_from,
        withdrawn_at=drop.withdrawn_at,
        created_at=drop.created_at,
        install_count=install_count,
        fork_count=fork_count,
        rating_avg=rating_avg,
        rating_count=rating_count,
        signer_passport=signer_passport,
        signer_integrity_band=signer_integrity_band,
    )


@router.get("/api/v1/drops", response_model=DropList)
async def browse(
    type: str | None = Query(None),
    q: str | None = Query(None, min_length=1, max_length=200),
    tag: str | None = Query(None),
    lang: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> DropList:
    """Paginated browse with optional filters.

    Cursor encodes (published_at_iso, drop_id) — opaque to callers.
    """
    # Build the base query: drops + their current_version manifest.
    cv = DropVersion
    stmt = (
        select(Drop, cv)
        .join(cv, (cv.drop_id == Drop.id) & (cv.version == Drop.current_version))
        .where(Drop.withdrawn_at.is_(None))
    )
    if type:
        stmt = stmt.where(Drop.type == type)
    if tag:
        # JSON path query — works in both Postgres (JSONB) and SQLite (JSON via cast).
        stmt = stmt.where(func.json_extract(cv.manifest, f"$.tags").like(f"%{tag}%"))
    if lang:
        stmt = stmt.where(func.json_extract(cv.manifest, "$.locale_hint") == lang)
    if q:
        # naive LIKE search on id + flattened name (both Postgres + SQLite work).
        stmt = stmt.where(
            or_(
                Drop.id.ilike(f"%{q}%"),
                func.json_extract(cv.manifest, "$.name").like(f"%{q}%"),
                func.json_extract(cv.manifest, "$.subtitle").like(f"%{q}%"),
            )
        )

    # Cursor pagination on (created_at desc, id).
    if cursor:
        try:
            iso, last_id = cursor.split("|", 1)
            from datetime import datetime
            ts = datetime.fromisoformat(iso)
            stmt = stmt.where(
                (Drop.created_at < ts) | ((Drop.created_at == ts) & (Drop.id > last_id))
            )
        except Exception:
            raise HTTPException(status_code=400, detail={"error": "invalid_cursor"})

    stmt = stmt.order_by(desc(Drop.created_at), Drop.id).limit(limit)
    rows = (await session.execute(stmt)).all()
    items = [_summary_from(d, manifest=v.manifest, signer_passport=v.signer_passport,
                            signer_integrity_band=v.signer_integrity_band)
             for d, v in rows]

    # Count is bounded to avoid scanning the full table for large catalogs.
    count_stmt = select(func.count()).select_from(Drop).where(Drop.withdrawn_at.is_(None))
    if type:
        count_stmt = count_stmt.where(Drop.type == type)
    total = (await session.execute(count_stmt)).scalar_one()

    next_cursor = None
    if len(rows) == limit:
        last_drop, _ = rows[-1]
        next_cursor = f"{last_drop.created_at.isoformat()}|{last_drop.id}"

    return DropList(items=items, total=total, cursor=cursor, next_cursor=next_cursor)


@router.get("/api/v1/drops/trending", response_model=DropList)
async def trending(
    type: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> DropList:
    """v1 trending — weighted by:
      installs_last_30d * 1.0
      + retention_30d   * 2.0
      + bayesian_rating * 1.5  (TODO when ratings ship)
      + fork_count      * 0.5
      + integrity_weight * 0.5 (TODO)
    For v1 we use a simplified score: install_count + fork_count.
    Tune empirically post-launch.
    """
    cv = DropVersion
    install_count_subq = (
        select(UserLibrary.drop_id, func.count().label("install_count"))
        .group_by(UserLibrary.drop_id)
        .subquery()
    )
    fork_count_subq = (
        select(Fork.source_drop_id, func.count().label("fork_count"))
        .group_by(Fork.source_drop_id)
        .subquery()
    )
    rating_subq = (
        select(
            Rating.drop_id,
            func.avg(Rating.stars).label("rating_avg"),
            func.count().label("rating_count"),
        )
        .group_by(Rating.drop_id)
        .subquery()
    )

    stmt = (
        select(
            Drop,
            cv,
            func.coalesce(install_count_subq.c.install_count, 0).label("ic"),
            func.coalesce(fork_count_subq.c.fork_count, 0).label("fc"),
            rating_subq.c.rating_avg,
            func.coalesce(rating_subq.c.rating_count, 0).label("rc"),
        )
        .join(cv, (cv.drop_id == Drop.id) & (cv.version == Drop.current_version))
        .outerjoin(install_count_subq, install_count_subq.c.drop_id == Drop.id)
        .outerjoin(fork_count_subq, fork_count_subq.c.source_drop_id == Drop.id)
        .outerjoin(rating_subq, rating_subq.c.drop_id == Drop.id)
        .where(Drop.withdrawn_at.is_(None))
    )
    if type:
        stmt = stmt.where(Drop.type == type)

    # Score = installs + 0.5*forks; ORDER BY in SQL.
    score = (
        func.coalesce(install_count_subq.c.install_count, 0)
        + 0.5 * func.coalesce(fork_count_subq.c.fork_count, 0)
    )
    stmt = stmt.order_by(desc(score), desc(Drop.created_at)).limit(limit)

    rows = (await session.execute(stmt)).all()
    items = [
        _summary_from(
            d, manifest=v.manifest,
            install_count=int(ic), fork_count=int(fc),
            rating_avg=float(ra) if ra is not None else None,
            rating_count=int(rc),
            signer_passport=v.signer_passport,
            signer_integrity_band=v.signer_integrity_band,
        )
        for d, v, ic, fc, ra, rc in rows
    ]
    return DropList(items=items, total=len(items))


@router.get("/api/v1/drops/{drop_id}", response_model=DropDetail)
async def drop_detail(
    drop_id: str,
    session: AsyncSession = Depends(get_session),
) -> DropDetail:
    drop = await session.get(Drop, drop_id)
    if drop is None:
        raise HTTPException(status_code=404, detail={"error": "drop_not_found"})
    version_row = (await session.execute(
        select(DropVersion).where(
            DropVersion.drop_id == drop_id,
            DropVersion.version == drop.current_version,
        )
    )).scalar_one_or_none()
    if version_row is None:
        raise HTTPException(status_code=500, detail={"error": "missing_current_version_row"})

    install_count = (await session.execute(
        select(func.count()).select_from(UserLibrary).where(UserLibrary.drop_id == drop_id)
    )).scalar_one()
    fork_count = (await session.execute(
        select(func.count()).select_from(Fork).where(Fork.source_drop_id == drop_id)
    )).scalar_one()
    rating_row = (await session.execute(
        select(
            func.avg(Rating.stars),
            func.count(),
        ).where(Rating.drop_id == drop_id)
    )).one()

    summary = _summary_from(
        drop,
        manifest=version_row.manifest,
        install_count=int(install_count),
        fork_count=int(fork_count),
        rating_avg=float(rating_row[0]) if rating_row[0] is not None else None,
        rating_count=int(rating_row[1] or 0),
        signer_passport=version_row.signer_passport,
        signer_integrity_band=version_row.signer_integrity_band,
    )
    return DropDetail(
        **summary.model_dump(),
        manifest=version_row.manifest,
        bundle_url=version_row.bundle_url,
        bundle_sha256=version_row.bundle_sha256,
        signature_verified=version_row.signature_verified,
    )


@public_router.get("/.well-known/r2-config", response_model=R2Config)
async def r2_config(settings: Settings = Depends(get_settings)) -> R2Config:
    """Public R2 client config — the SDK fetches this to know where to upload."""
    return R2Config(
        account_id=settings.r2_account_id,
        bucket=settings.r2_bucket,
        public_domain=settings.r2_public_domain,
    )
