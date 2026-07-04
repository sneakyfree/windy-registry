"""drops.py — POST /api/v1/drops (publish endpoint). WD-18.

Future endpoints in this router:
  GET    /api/v1/drops             (WD-16 browse + search)
  GET    /api/v1/drops/{id}        (WD-16 detail)
  GET    /api/v1/drops/{id}/forks  (WD-19)
  POST   /api/v1/drops/{id}/fork   (WD-19)
  DELETE /api/v1/drops/{id}        (WD-9 withdraw)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from windy_drops_spec import DropManifest

from ..database import get_session
from ..middleware.auth import AuthUser, get_current_user
from ..models import Drop, DropVersion, Fork
from ..schemas.forks import ForkList, ForkRecord, ForkRequest
from ..schemas.publish import PublishedDrop, PublishRequest
from ..services.signature_verify import verify_signature

router = APIRouter(prefix="/api/v1/drops", tags=["drops"])


def _author_passports(manifest: dict[str, Any]) -> list[str]:
    """Extract all author passports declared on the manifest."""
    authors = manifest.get("author") or []
    if not isinstance(authors, list):
        return []
    return [a.get("passport") for a in authors if isinstance(a, dict) and a.get("passport")]


async def _caller_owns_drop(
    session: AsyncSession, drop_id: str, caller_passport: str | None
) -> bool:
    """True iff `caller_passport` appears in any existing version's author[].

    Ownership is passport-based — a caller with no passport owns nothing.
    Used to gate re-publishing new versions of, and withdrawing, an existing
    drop.
    """
    if caller_passport is None:
        return False
    versions = (
        await session.execute(
            select(DropVersion.manifest).where(DropVersion.drop_id == drop_id)
        )
    ).scalars().all()
    for manifest in versions:
        for a in (manifest or {}).get("author") or []:
            if isinstance(a, dict) and a.get("passport") == caller_passport:
                return True
    return False


@router.post(
    "",
    response_model=PublishedDrop,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"description": "Existing drop received a new version"},
        400: {"description": "Manifest fails schema validation"},
        401: {"description": "Bearer token missing / invalid"},
        403: {"description": "Caller does not own any declared author passport"},
        409: {"description": "(id, version) collision — versions are immutable"},
        422: {"description": "Signature missing where required, or signature invalid"},
    },
)
async def publish(
    body: PublishRequest,
    user: AuthUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PublishedDrop:
    """Publish a new version of a drop.

    Per ADR-053 §"Publishing":
      - Validate manifest against windy.drop.v1 schema
      - If pricing.type == "paid", require signature (v1.1 enforcement; v1
        rejects ahead of time so authors don't get surprised at v1.1 ship)
      - Author ownership: caller passport must match an entry in manifest.author[]
      - (id, version) is immutable; collision → 409
      - First publish of an id creates the Drop row; subsequent versions
        update current_version
    """
    # 1. Schema validation via Pydantic (windy_drops_spec).
    try:
        DropManifest.model_validate(body.manifest)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "schema_invalid", "issues": e.errors()},
        ) from e

    drop_id = body.manifest["id"]
    version_str = body.manifest["version"]
    drop_type = body.manifest["type"]
    pricing = body.manifest.get("pricing") or {}
    pricing_type = pricing.get("type", "free")

    # 2. Paid drops require signature (v1.1 anti-abuse enforced ahead of v1.1).
    has_sig = isinstance(body.manifest.get("signature"), dict)
    if pricing_type == "paid" and not has_sig:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "paid_requires_signature"},
        )

    # 3a. No impersonation — if the manifest claims authorship-by-passport, the
    #     caller must own one of those passports. Ownership is passport-based, so
    #     a passportless caller owns nothing and cannot publish AS a passport
    #     holder. (Previously this whole block was skipped when user.passport was
    #     None, letting any human Pro JWT publish as anyone.)
    declared_passports = _author_passports(body.manifest)
    if declared_passports and (
        user.passport is None or user.passport not in declared_passports
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "caller_passport_not_in_authors"},
        )

    # 3b. No id-hijack — publishing a NEW version of an EXISTING drop requires the
    #     caller to own an author passport on a prior version. The first publish
    #     of a fresh id is open; taking over someone else's id is not.
    if await session.get(Drop, drop_id) is not None and not await _caller_owns_drop(
        session, drop_id, user.passport
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "not_author"},
        )

    # G10: opt-in bundle SHA re-verify (set WINDY_VERIFY_BUNDLE_BYTES=1 in prod).
    from ..services.signature_verify import verify_bundle_bytes
    if not await verify_bundle_bytes(str(body.bundle_url), body.bundle_sha256):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "bundle_sha_mismatch",
                    "message": "re-fetched bundle bytes do not hash to the claimed SHA-256"},
        )

    # 4. Verify signature if present.
    sig_verified = False
    signer_passport: str | None = None
    signer_band: str | None = None
    signer_level: str | None = None
    if has_sig:
        result = await verify_signature(body.manifest, body.bundle_sha256)
        if not result.valid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "signature_invalid", "reason": result.error},
            )
        sig_verified = True
        signer_passport = result.signer_passport
        signer_band = result.signer_integrity_band
        signer_level = result.signer_clearance_level

    # 5. Collision detection.
    exists = await session.execute(
        select(DropVersion).where(
            DropVersion.drop_id == drop_id,
            DropVersion.version == version_str,
        )
    )
    if exists.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "version_already_published", "drop_id": drop_id, "version": version_str},
        )

    # 6. Insert / update the Drop row.
    drop = await session.get(Drop, drop_id)
    if drop is None:
        drop = Drop(
            id=drop_id,
            type=drop_type,
            current_version=version_str,
            forked_from=body.manifest.get("forked_from"),
        )
        session.add(drop)
    else:
        drop.current_version = version_str

    # 7. Insert the DropVersion row.
    version_row = DropVersion(
        drop_id=drop_id,
        version=version_str,
        manifest=body.manifest,
        bundle_url=str(body.bundle_url),
        bundle_sha256=body.bundle_sha256,
        signature_verified=sig_verified,
        signer_passport=signer_passport,
        signer_integrity_band=signer_band,
        signer_clearance_level=signer_level,
    )
    session.add(version_row)
    await session.flush()  # commit happens in get_session() dependency teardown

    # 8. Emit drop.published webhook event (WD-21).
    from ..services.webhook_dispatcher import dispatch_event
    await dispatch_event(
        session,
        "drop.published",
        {
            "drop_id": drop_id,
            "version": version_str,
            "type": drop_type,
            "signer_passport": signer_passport,
        },
        skip_async=True,  # v1: record-only; real POSTs land in prod once subscribers come online
    )

    return PublishedDrop(
        drop_id=drop_id,
        version=version_str,
        manifest=body.manifest,
        bundle_url=str(body.bundle_url),
        bundle_sha256=body.bundle_sha256,
        signature_verified=sig_verified,
        signer_passport=signer_passport,
        signer_integrity_band=signer_band,
        signer_clearance_level=signer_level,
        published_at=version_row.published_at if version_row.published_at else __import__("datetime").datetime.now(),
    )


# ---- WD-19: fork + lineage endpoints ----

@router.post(
    "/{drop_id}/fork",
    response_model=ForkRecord,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Source drop does not exist"},
        409: {"description": "new_id collides with an existing drop"},
        410: {"description": "Source drop is withdrawn"},
    },
)
async def fork_drop(
    drop_id: str,
    body: ForkRequest,
    user: AuthUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ForkRecord:
    """Register lineage for a fork ahead of publish.

    The SDK's `windy-drops fork` calls this to claim the new id + bump the
    source's fork_count immediately. Publish (WD-18) later links the
    DropVersion row.

    is_published stays False until the fork's first version lands; a cron
    can sweep unpublished forks older than 7 days (TBD).
    """
    source = await session.get(Drop, drop_id)
    if source is None:
        raise HTTPException(status_code=404, detail={"error": "source_not_found"})
    if source.withdrawn_at is not None:
        raise HTTPException(status_code=410, detail={"error": "source_withdrawn"})

    collision = await session.get(Drop, body.new_id)
    if collision is not None:
        raise HTTPException(status_code=409, detail={"error": "new_id_collision"})

    # Existing lineage row would also collide; check via composite PK.
    existing_fork = await session.get(Fork, (drop_id, body.new_id))
    if existing_fork is not None:
        raise HTTPException(status_code=409, detail={"error": "lineage_already_registered"})

    fork = Fork(source_drop_id=drop_id, fork_drop_id=body.new_id, is_published=False)
    session.add(fork)
    await session.flush()
    return ForkRecord.model_validate(fork, from_attributes=True)


@router.get("/{drop_id}/forks", response_model=ForkList)
async def list_forks(
    drop_id: str,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
) -> ForkList:
    """List forks of a drop (lineage UI)."""
    source = await session.get(Drop, drop_id)
    if source is None:
        raise HTTPException(status_code=404, detail={"error": "drop_not_found"})

    rows = (await session.execute(
        select(Fork)
        .where(Fork.source_drop_id == drop_id)
        .order_by(Fork.forked_at.desc())
        .limit(min(max(limit, 1), 200))
    )).scalars().all()
    total = (await session.execute(
        select(func.count()).select_from(Fork).where(Fork.source_drop_id == drop_id)
    )).scalar_one()
    return ForkList(
        items=[ForkRecord.model_validate(r, from_attributes=True) for r in rows],
        total=total,
    )


# ---- WD-9: withdraw endpoint ----

@router.delete(
    "/{drop_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: {"description": "Drop not found"},
        403: {"description": "Caller does not own this drop"},
    },
)
async def withdraw_drop(
    drop_id: str,
    user: AuthUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Hide a drop from search + trending (sets withdrawn_at).

    Per ADR-053 §"Withdrawing": bundles stay on R2 so already-installed users
    keep working; re-publishing the same id requires explicit confirmation
    (handled SDK-side by inspecting withdrawn_at).
    """
    drop = await session.get(Drop, drop_id)
    if drop is None:
        raise HTTPException(status_code=404, detail={"error": "drop_not_found"})

    # Ownership check — caller's passport must appear in some version's author[].
    # Fail-closed: a passportless caller owns nothing, so it cannot withdraw a
    # drop. (Previously the whole check was skipped when user.passport was None,
    # letting any human Pro JWT withdraw anyone's drop.)
    if not await _caller_owns_drop(session, drop_id, user.passport):
        raise HTTPException(status_code=403, detail={"error": "not_author"})

    from datetime import UTC, datetime
    drop.withdrawn_at = datetime.now(UTC)
    await session.flush()


# ---- WD-23: sandboxed preview ----


@router.get(
    "/{drop_id}/preview",
    response_class=HTMLResponse,
    responses={404: {"description": "Drop not found"}},
)
async def preview(
    drop_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Serve a sandboxed preview page for a drop.

    Returns HTML hosting an iframe to drops.windydrops.com (separate
    origin), sandbox="allow-scripts" only, CSP-locked, postMessage
    protocol injects mock data per the drop's type.
    """
    drop = await session.get(Drop, drop_id)
    if drop is None:
        raise HTTPException(status_code=404, detail={"error": "drop_not_found"})

    # mock_data override from manifest.preview_mock_data is fetched from R2
    # in v1.1; for now we use the in-process defaults so the harness works
    # without R2 round-trips.

    from ..config import get_settings
    settings = get_settings()
    from ..services.sandbox_host import build_preview_html
    html = build_preview_html(
        drop_id=drop_id,
        version=drop.current_version,
        drop_type=drop.type,
        public_bundle_domain=settings.r2_public_domain,
        mock_data=None,
    )
    return HTMLResponse(content=html, status_code=200)


# ---- F10 + F11: oembed + og metadata endpoints ----

@router.get("/{drop_id}/oembed")
async def oembed(
    drop_id: str,
    format: str = "json",
    session: AsyncSession = Depends(get_session),
):
    """oEmbed discovery target for /d/{id} (referenced from WD-24 CF Pages
    Function via <link rel='alternate' type='application/json+oembed'>).
    See https://oembed.com/."""
    if format != "json":
        raise HTTPException(status_code=501, detail={"error": "only_json_format_supported"})
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
        raise HTTPException(status_code=500, detail={"error": "missing_current_version"})
    from ..services.i18n import resolve_i18n
    manifest = version_row.manifest or {}
    name = resolve_i18n(manifest.get("name"), "en") or drop_id
    authors_list = manifest.get("author") or []
    author_name = ""
    if isinstance(authors_list, list) and authors_list and isinstance(authors_list[0], dict):
        author_name = authors_list[0].get("name", "")
    return {
        "version": "1.0",
        "type": "rich",
        "title": name,
        "author_name": author_name,
        "provider_name": "Windy Drops",
        "provider_url": "https://windydrops.com",
        "html": (
            f'<iframe src="https://api.windydrops.com/api/v1/drops/{drop_id}/preview"'
            ' width="600" height="400" frameborder="0" sandbox="allow-scripts"></iframe>'
        ),
        "width": 600,
        "height": 400,
    }


@router.get("/{drop_id}/og")
async def og_metadata(
    drop_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Canonical OpenGraph metadata for a drop. The CF Pages Function at
    windydrops.com/d/{id} can call this OR build the OG inline; both must
    return identical shapes."""
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
        raise HTTPException(status_code=500, detail={"error": "missing_current_version"})
    from ..config import get_settings
    from ..services.i18n import resolve_i18n
    settings = get_settings()
    manifest = version_row.manifest or {}
    name = resolve_i18n(manifest.get("name"), "en") or drop_id
    subtitle = resolve_i18n(manifest.get("subtitle"), "en") or f"{drop.type} drop on Windy Drops"
    return {
        "title": name,
        "description": subtitle,
        "image_url": f"https://{settings.r2_public_domain}/{drop_id}/{drop.current_version}/preview.png",
        "canonical_url": f"https://windydrops.com/d/{drop_id}",
        "embed_iframe_url": f"https://api.windydrops.com/api/v1/drops/{drop_id}/preview",
        "type": drop.type,
        "site_name": "Windy Drops",
    }
