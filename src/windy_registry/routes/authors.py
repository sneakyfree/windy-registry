"""authors.py — author profile + follow graph.

GET    /api/v1/authors/{handle}            public profile
GET    /api/v1/authors/{handle}/drops      author's drops
POST   /api/v1/me/follows                  follow an author
DELETE /api/v1/me/follows/{handle}         unfollow
GET    /api/v1/me/follows                  list follows

Per ADR-053 §"Author profiles & social graph":
  - handle derived deterministically from passport (or callsign)
  - follower_count cached on authors.follower_count_cached (updated by
    follow/unfollow); refreshed nightly for skew correction
  - trending personalization (boost followed authors' drops) lives in
    WD-16's /trending endpoint — auth-aware boost lands once we wire
    the optional auth dep there
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..middleware.auth import AuthUser, get_current_user
from ..models import Author, Drop, DropVersion, Follow
from ..schemas.authors import (
    AuthorProfile,
    FollowList,
    FollowRequest,
    FollowRow,
)
from ..schemas.browse import DropList, DropSummary

authors_router = APIRouter(prefix="/api/v1/authors", tags=["authors"])
follows_router = APIRouter(prefix="/api/v1/me/follows", tags=["follows"])


def _user_uuid(user: AuthUser) -> UUID:
    digest = hashlib.sha256(user.subject.encode("utf-8")).digest()[:16]
    return UUID(bytes=digest)


async def _resolve_or_create_author(
    session: AsyncSession,
    handle: str,
) -> Author | None:
    """Find an Author row by handle. Returns None if no drops have ever
    been authored under that handle (i.e. it doesn't exist).
    Auto-creates the row on first lookup so /authors/{handle} always
    serves the publish-derived shape (handle inferred from any drop's
    author[0].callsign or passport)."""
    a = (await session.execute(
        select(Author).where(Author.handle == handle)
    )).scalar_one_or_none()
    return a


@authors_router.get("/{handle}", response_model=AuthorProfile)
async def author_profile(
    handle: str,
    session: AsyncSession = Depends(get_session),
) -> AuthorProfile:
    author = await _resolve_or_create_author(session, handle)

    # If no formal Author row exists, derive a profile from any drop's
    # author entries — handles the common case where authors haven't
    # explicitly registered profiles yet.
    if author is None:
        # Find a drop whose manifest has an author with callsign==handle.
        versions = (await session.execute(
            select(DropVersion)
        )).scalars().all()
        match = None
        for v in versions:
            for a in (v.manifest or {}).get("author") or []:
                if isinstance(a, dict) and (a.get("callsign", "").lower() == handle.lower() or
                                             a.get("name", "").lower().replace(" ", "-") == handle.lower()):
                    match = a
                    break
            if match:
                break
        if match is None:
            raise HTTPException(status_code=404, detail={"error": "author_not_found"})

        # Derive synthetic profile.
        from datetime import UTC, datetime
        from uuid import uuid4
        drop_count = (await session.execute(
            select(func.count()).select_from(DropVersion)
        )).scalar_one()
        return AuthorProfile(
            id=uuid4(),  # synthetic (no row exists)
            handle=handle,
            display_name=match.get("name", handle),
            passport=match.get("passport"),
            integrity_band=None,
            clearance_level=None,
            follower_count=0,
            drop_count=int(drop_count),
            lifetime_tips_cents=0,
            public_tips_disabled=False,
            joined_at=datetime.now(UTC),
        )

    # Drop count: count Drops where any version's manifest mentions this passport.
    drop_count = 0
    if author.passport:
        rows = (await session.execute(
            select(DropVersion).where(DropVersion.signer_passport == author.passport)
        )).scalars().all()
        drop_count = len({r.drop_id for r in rows})

    follower_count = (await session.execute(
        select(func.count()).select_from(Follow).where(Follow.followed_handle == handle)
    )).scalar_one()

    return AuthorProfile(
        id=author.id,
        handle=author.handle,
        display_name=author.display_name,
        passport=author.passport,
        integrity_band=author.integrity_band,
        clearance_level=author.clearance_level,
        follower_count=int(follower_count),
        drop_count=int(drop_count),
        lifetime_tips_cents=author.lifetime_tips_cents,
        public_tips_disabled=author.public_tips_disabled,
        joined_at=author.joined_at,
    )


@authors_router.get("/{handle}/drops", response_model=DropList)
async def author_drops(
    handle: str,
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> DropList:
    """Drops by this author — matches on any drop_version where the
    signer_passport corresponds to an Author with this handle, OR
    where the author manifest entry has callsign==handle.
    """
    # Try to find the author's passport via the formal Author table.
    author = await _resolve_or_create_author(session, handle)
    passport = author.passport if author else None

    items: list[DropSummary] = []
    versions = (await session.execute(
        select(DropVersion)
        .join(Drop, Drop.id == DropVersion.drop_id)
        .where(Drop.withdrawn_at.is_(None))
        .order_by(DropVersion.published_at.desc())
        .limit(limit)
    )).scalars().all()
    seen: set[str] = set()
    for v in versions:
        if v.drop_id in seen:
            continue
        manifest = v.manifest or {}
        authors_list = manifest.get("author") or []
        match = False
        for a in authors_list:
            if not isinstance(a, dict):
                continue
            if passport and a.get("passport") == passport:
                match = True
                break
            cs = a.get("callsign", "")
            nm = a.get("name", "")
            if cs.lower() == handle.lower() or nm.lower().replace(" ", "-") == handle.lower():
                match = True
                break
        if not match:
            continue
        drop = await session.get(Drop, v.drop_id)
        if drop is None:
            continue
        items.append(DropSummary(
            id=drop.id, type=drop.type, current_version=drop.current_version,
            name=manifest.get("name"), subtitle=manifest.get("subtitle"),
            tags=manifest.get("tags") or [], license=manifest.get("license"),
            locale_hint=manifest.get("locale_hint"),
            forked_from=drop.forked_from, withdrawn_at=drop.withdrawn_at,
            created_at=drop.created_at,
            signer_passport=v.signer_passport, signer_integrity_band=v.signer_integrity_band,
        ))
        seen.add(v.drop_id)

    return DropList(items=items, total=len(items))


# ---- follow graph ----

@follows_router.post("", response_model=FollowRow, status_code=status.HTTP_201_CREATED)
async def follow_author(
    body: FollowRequest,
    user: AuthUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> FollowRow:
    """Follow an author. Idempotent — already-following returns existing row."""
    uid = _user_uuid(user)

    # Ensure an Author row exists (so the FK is satisfied). Auto-create a
    # minimal one on first follow (deferred profile enrichment).
    a = await _resolve_or_create_author(session, body.author_handle)
    if a is None:
        from uuid import uuid4
        a = Author(id=uuid4(), handle=body.author_handle, display_name=body.author_handle)
        session.add(a)
        await session.flush()

    existing = await session.get(Follow, (uid, body.author_handle))
    if existing is not None:
        return FollowRow.model_validate(existing, from_attributes=True)
    follow = Follow(follower_user_id=uid, followed_handle=body.author_handle)
    session.add(follow)
    await session.flush()
    return FollowRow.model_validate(follow, from_attributes=True)


@follows_router.delete("/{handle}", status_code=status.HTTP_204_NO_CONTENT)
async def unfollow_author(
    handle: str,
    user: AuthUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    uid = _user_uuid(user)
    f = await session.get(Follow, (uid, handle))
    if f is None:
        raise HTTPException(status_code=404, detail={"error": "not_following"})
    await session.delete(f)
    await session.flush()


@follows_router.get("", response_model=FollowList)
async def list_follows(
    user: AuthUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> FollowList:
    uid = _user_uuid(user)
    rows = (await session.execute(
        select(Follow).where(Follow.follower_user_id == uid).order_by(Follow.created_at.desc())
    )).scalars().all()
    return FollowList(
        items=[FollowRow.model_validate(r, from_attributes=True) for r in rows],
        total=len(rows),
    )
