"""publish.py — request/response schemas for POST /api/v1/drops."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: dict[str, Any] = Field(
        ...,
        description="Full SKILL.md frontmatter as a dict (parsed YAML).",
    )
    bundle_url: HttpUrl = Field(
        ...,
        description=(
            "Public URL of the immutable bundle on R2 "
            "(drops.windydrops.com/<id>/<version>/<id>-<version>.zip)."
        ),
    )
    bundle_sha256: str = Field(
        ...,
        pattern=r"^[a-f0-9]{64}$",
        description="SHA-256 of the bundle bytes (lowercase hex).",
    )


class PublishedDrop(BaseModel):
    drop_id: str
    version: str
    manifest: dict[str, Any]
    bundle_url: str
    bundle_sha256: str
    signature_verified: bool
    signer_passport: str | None
    signer_integrity_band: str | None
    signer_clearance_level: str | None
    published_at: datetime
