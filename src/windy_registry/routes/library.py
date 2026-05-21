"""library.py — /api/v1/me/library endpoints. WD-17.

Per ADR-053 §"What install means in v1":
  install = add a row (user_id, drop_id, version, installed_at) to user_library.
  install does NOT copy bytes / does NOT execute code / does NOT grant
  capabilities. Surfaces query ?type=... to know what to load.

Paid drops return 402 in v1; WD-29 (v1.1) wires the Stripe payment flow.

User id is derived from the auth token's subject. For Pro RS256 tokens
the subject is the Windy user id; for Eternitas EPTs it's the agent
passport — both treated as opaque UUID keys for library purposes.
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..middleware.auth import AuthUser, get_current_user
from ..models import Drop, UserLibrary
from ..schemas.library import InstallRequest, LibraryList, LibraryRow, UninstallRequest

router = APIRouter(prefix="/api/v1/me/library", tags=["library"])


def _user_uuid(user: AuthUser) -> UUID:
    """Derive a stable UUID from the auth subject so non-UUID Pro/Eternitas
    subjects can key into user_library.user_id (uuid column).

    Using sha256(subject)[:16] gives a deterministic UUID5-style key. Once Pro
    + Eternitas issue UUID-shaped subjects natively, this can become a
    straight uuid.UUID(user.subject).
    """
    digest = hashlib.sha256(user.subject.encode("utf-8")).digest()[:16]
    return UUID(bytes=digest)


@router.get("", response_model=LibraryList)
async def list_library(
    type: str | None = None,
    user: AuthUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LibraryList:
    """List installed drops; optionally filter by drop type (`?type=...`)."""
    uid = _user_uuid(user)
    stmt = (
        select(UserLibrary)
        .where(UserLibrary.user_id == uid)
        .order_by(UserLibrary.installed_at.desc())
    )
    if type is not None:
        stmt = stmt.join(Drop, Drop.id == UserLibrary.drop_id).where(Drop.type == type)
    rows = (await session.execute(stmt)).scalars().all()
    count_stmt = select(func.count()).select_from(UserLibrary).where(UserLibrary.user_id == uid)
    if type is not None:
        count_stmt = count_stmt.join(Drop, Drop.id == UserLibrary.drop_id).where(Drop.type == type)
    total = (await session.execute(count_stmt)).scalar_one()
    return LibraryList(
        items=[LibraryRow.model_validate(r, from_attributes=True) for r in rows],
        total=total,
    )


@router.post("/install", response_model=LibraryRow, status_code=status.HTTP_201_CREATED)
async def install(
    body: InstallRequest,
    user: AuthUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LibraryRow:
    """Install a drop into the user's library.

    Per ADR-053 §"Pricing":
      - free + tip-jar → install immediately
      - paid → returns 402 in v1 (paid_drops_v1_1); v1.1 will require a
        valid payment_intent_id.
    """
    drop = await session.get(Drop, body.drop_id)
    if drop is None:
        raise HTTPException(status_code=404, detail={"error": "drop_not_found"})
    if drop.withdrawn_at is not None:
        raise HTTPException(status_code=410, detail={"error": "drop_withdrawn"})

    # Look up the manifest of the requested version (or current_version).
    requested_version = body.version or drop.current_version
    from ..models import DropVersion
    version_row = (await session.execute(
        select(DropVersion).where(
            DropVersion.drop_id == body.drop_id,
            DropVersion.version == requested_version,
        )
    )).scalar_one_or_none()
    if version_row is None:
        raise HTTPException(status_code=404, detail={"error": "version_not_found"})

    pricing = (version_row.manifest or {}).get("pricing") or {}
    if pricing.get("type") == "paid":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"error": "paid_drops_v1_1", "message": "paid drops launching v1.1"},
        )

    uid = _user_uuid(user)
    existing = await session.get(UserLibrary, (uid, body.drop_id))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "already_installed", "drop_id": body.drop_id},
        )

    row = UserLibrary(
        user_id=uid,
        drop_id=body.drop_id,
        version=requested_version,
        auto_update=body.auto_update,
    )
    session.add(row)
    await session.flush()

    # WD-21: emit drop.installed.
    from ..services.webhook_dispatcher import dispatch_event
    await dispatch_event(
        session, "drop.installed",
        {"drop_id": body.drop_id, "version": requested_version, "user_id": str(uid)},
        skip_async=True,
    )
    return LibraryRow.model_validate(row, from_attributes=True)


@router.post("/uninstall", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall(
    body: UninstallRequest,
    user: AuthUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Remove a drop from the user's library."""
    uid = _user_uuid(user)
    result = await session.execute(
        delete(UserLibrary).where(
            and_(UserLibrary.user_id == uid, UserLibrary.drop_id == body.drop_id)
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail={"error": "not_installed"})
