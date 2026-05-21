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
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from windy_drops_spec import DropManifest

from ..database import get_session
from ..middleware.auth import AuthUser, get_current_user
from ..models import Drop, DropVersion
from ..schemas.publish import PublishedDrop, PublishRequest
from ..services.signature_verify import verify_signature

router = APIRouter(prefix="/api/v1/drops", tags=["drops"])


def _author_passports(manifest: dict[str, Any]) -> list[str]:
    """Extract all author passports declared on the manifest."""
    authors = manifest.get("author") or []
    if not isinstance(authors, list):
        return []
    return [a.get("passport") for a in authors if isinstance(a, dict) and a.get("passport")]


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

    # 3. Author ownership — caller's passport must appear in author[].
    declared_passports = _author_passports(body.manifest)
    if user.passport is not None:
        if declared_passports and user.passport not in declared_passports:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "caller_passport_not_in_authors"},
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

    # 8. TODO(WD-21): emit drop.published webhook event.

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
