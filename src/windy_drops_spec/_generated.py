# GENERATED FILE. DO NOT EDIT.
# Run `uvx --with datamodel-code-generator python codegen.py` from
# python/artifact-spec/ to regenerate.
# Source: schemas/windy.drop.v1.json (WD-0 of DNA_STRAND_MASTER_PLAN.md).
#
# This file ships in the windy-drops-spec package on PyPI. Both this
# binding and the TypeScript sibling (@windy/drops-artifact-spec on npm)
# are codegen'd from the same JSON Schema. A manifest accepted by one
# MUST be accepted by the other (enforced by WD-11 conformance harness).

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, RootModel


class Consume(RootModel[str]):
    root: str = Field(..., pattern="^[a-z][a-z0-9.]*\\.v[0-9]+$")


class Surface(RootModel[str]):
    root: str = Field(..., min_length=1)


class Tag(RootModel[str]):
    root: str = Field(..., max_length=64, min_length=1)


class DropId(RootModel[str]):
    root: str = Field(
        ..., max_length=128, min_length=1, pattern="^[a-z0-9]+(-[a-z0-9]+)*$"
    )
    """
    Lowercase kebab-case slug. Allowed chars: [a-z0-9-]. Must start with [a-z0-9].
    """


class I18nString1(RootModel[str]):
    root: str = Field(..., max_length=200, min_length=1)


class I18nString2(BaseModel):
    model_config = ConfigDict(
        extra="allow",
    )
    __pydantic_extra__: dict[str, str]
    default: str = Field(
        ..., pattern="^[a-z]{2,3}(-[A-Z][a-z]{3})?(-[A-Z]{2}|-[0-9]{3})?$"
    )
    """
    BCP 47 locale tag pointing to the canonical fallback key in this object.
    """


class DropType(StrEnum):
    """
    v1 reserved drop types. New types are additive in v1.x via ADR + consumer surface.
    """

    control_panel_template = "control-panel-template"
    skill = "skill"
    tool = "tool"
    theme = "theme"
    voice_pack = "voice-pack"
    workflow = "workflow"


class Type(StrEnum):
    human = "human"
    agent = "agent"


class Author(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    name: str = Field(..., max_length=200, min_length=1)
    callsign: str | None = Field(None, max_length=64, min_length=1)
    passport: str | None = Field(None, pattern="^E[THX]\\d{2}-[A-Z0-9]{4}-[A-Z0-9]{4}$")
    """
    Eternitas passport. Presence enables signature verification on publish.
    """
    type: Type | None = "human"
    operator: str | None = Field(None, pattern="^E[THX]\\d{2}-[A-Z0-9]{4}-[A-Z0-9]{4}$")
    """
    Required when type='agent'. The human operator's passport (for credit chain + integrity compounding).
    """


class Dependency(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    id: str = Field(
        ..., max_length=128, min_length=1, pattern="^[a-z0-9]+(-[a-z0-9]+)*$"
    )
    """
    Lowercase kebab-case slug. Allowed chars: [a-z0-9-]. Must start with [a-z0-9].
    """
    type: DropType
    version: str | None = Field(None, min_length=1)
    """
    SemVer range (e.g., '^1.0.0', '~2.1', '>=1.0 <2.0'). Resolved at install time.
    """


class Type1(StrEnum):
    """
    free + tip-jar ship in v1. paid in v1.1. subscription reserved (not in v1.1).
    """

    free = "free"
    tip_jar = "tip-jar"
    paid = "paid"
    subscription = "subscription"


class AmountCents(RootModel[int]):
    root: int = Field(..., ge=100, le=100000)
    """
    Integer cents. Required when type=paid; null otherwise.
    """


class Currency(RootModel[str]):
    root: str = Field(..., pattern="^[A-Z]{3}$")
    """
    ISO 4217 currency code (e.g., USD, EUR, JPY).
    """


class Pricing(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Type1
    """
    free + tip-jar ship in v1. paid in v1.1. subscription reserved (not in v1.1).
    """
    amount_cents: AmountCents | None = None
    """
    Integer cents. Required when type=paid; null otherwise.
    """
    currency: Currency | None = None
    """
    Required when type=paid; null otherwise.
    """


class StripeAccount(RootModel[str]):
    root: str = Field(..., pattern="^acct_[a-zA-Z0-9]+$")
    """
    Stripe Connect account id. Filled by SDK after creator OAuth.
    """


class Monetization(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    tips_enabled: bool | None = False
    """
    Opt-in to receive tips on this drop. Requires author Stripe Connect.
    """
    stripe_account: StripeAccount | None = None
    """
    Stripe Connect account id. Filled by SDK after creator OAuth.
    """
    payout_currency: str | None = Field("usd", pattern="^[a-z]{3}$")
    """
    ISO 4217 currency code (lowercase, Stripe convention).
    """
    refund_window_days: int | None = Field(7, ge=7, le=30)
    """
    Creator-set 7-30. Only meaningful for paid drops.
    """


class Royalty(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    forks_inherit_price: bool | None = True
    """
    Whether forks of paid drops inherit the parent's price.
    """
    fork_revenue_share_pct: int | None = Field(50, ge=0, le=100)
    """
    Percent of paid-fork sales paid to the original author. Default 50.
    """


class IntegrityBand(StrEnum):
    """
    Snapshot of signer's Eternitas integrity band at signing time. Snapshot only — registry does NOT update later.
    """

    critical = "critical"
    poor = "poor"
    fair = "fair"
    good = "good"
    exceptional = "exceptional"


class ClearanceLevel(StrEnum):
    """
    Snapshot of signer's Eternitas clearance level.
    """

    registered = "registered"
    verified = "verified"
    cleared = "cleared"
    top_secret = "top_secret"
    eternal = "eternal"


class Signer(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    passport: str = Field(..., pattern="^E[THX]\\d{2}-[A-Z0-9]{4}-[A-Z0-9]{4}$")
    """
    Eternitas passport — ET (agent), EH (human), EX (hybrid). Format: PREFIX-XXXX-XXXX with birth year.
    """
    integrity_band: IntegrityBand | None = None
    """
    Snapshot of signer's Eternitas integrity band at signing time. Snapshot only — registry does NOT update later.
    """
    clearance_level: ClearanceLevel | None = None
    """
    Snapshot of signer's Eternitas clearance level.
    """


class Signature(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    algorithm: Literal["ES256"]
    """
    v1 supports ES256 only.
    """
    signer: Signer
    signed_at: AwareDatetime
    """
    RFC 3339 timestamp.
    """
    signed_digest: str = Field(..., pattern="^sha256:[a-f0-9]{64}$")
    """
    sha256(canonical_manifest_sans_signature || bundle_sha256_hex).
    """
    signature: str = Field(..., pattern="^[A-Za-z0-9+/]+={0,2}$")
    """
    Base64-encoded raw ES256 signature (R||S, 64 bytes → 88 chars base64).
    """


class ControlPanelExtension(BaseModel):
    model_config = ConfigDict(
        extra="allow",
    )
    refresh_interval_ms: int | None = Field(5000, ge=100, le=3600000)
    """
    How often the template requests fresh Vitals payloads.
    """
    supports_remote_fleet: bool | None = True
    """
    Whether this template renders the per-agent fleet panel.
    """


class DropManifest(BaseModel):
    """
    Canonical schema for windy.drop.v1 SKILL.md frontmatter. Source of truth; both TypeScript (Zod) and Python (Pydantic v2) bindings codegen from this file. Strand: WD-0 of DNA_STRAND_MASTER_PLAN.md.
    """

    model_config = ConfigDict(
        extra="allow",
    )
    schema_: Literal["windy.drop.v1"] = Field(..., alias="schema")
    """
    Self-identifying version of the manifest format. Consumers MUST reject majors they do not understand.
    """
    id: str = Field(
        ..., max_length=128, min_length=1, pattern="^[a-z0-9]+(-[a-z0-9]+)*$"
    )
    """
    Stable globally-unique slug. Case-sensitive, kebab-case. Convention: <author-slug>-<drop-slug>.
    """
    name: I18nString1 | I18nString2
    """
    Display name. Plain UTF-8 string OR i18n object with a 'default' locale pointer.
    """
    subtitle: I18nString1 | I18nString2 | None = None
    """
    One-line description. Same i18n shape as 'name'.
    """
    type: DropType
    """
    Reserved drop type for v1. New types are additive; surfaces that don't understand a type filter it out.
    """
    version: str = Field(
        ...,
        pattern="^(0|[1-9]\\d*)\\.(0|[1-9]\\d*)\\.(0|[1-9]\\d*)(?:-((?:0|[1-9]\\d*|\\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\\.(?:0|[1-9]\\d*|\\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?(?:\\+([0-9a-zA-Z-]+(?:\\.[0-9a-zA-Z-]+)*))?$",
    )
    """
    SemVer 2.0.0. Each (id, version) is immutable once published.
    """
    forked_from: DropId | None = None
    """
    Drop id of the original this was forked from. null for originals. Filled automatically by SDK on `windy-drops fork`.
    """
    author: list[Author] = Field(..., min_length=1)
    """
    Array of authors. Each is {name, callsign?, passport?, type?, operator?}. type='agent' requires operator (per ADR-053).
    """
    license: str = Field(..., max_length=100, min_length=1)
    """
    SPDX license identifier (e.g., MIT, Apache-2.0, CC-BY-4.0, proprietary).
    """
    consumes: list[Consume] | None = Field([], validate_default=True)
    """
    Protocols + versions the drop needs at runtime (e.g., 'windy.vitals.v1').
    """
    surfaces: list[Surface] | None = Field([], validate_default=True)
    """
    Which consumer surfaces accept this drop (open-ended; surfaces register their own ids).
    """
    entry: str | None = Field(None, min_length=1)
    """
    Entry-point file (type-dependent; absent for content-only types).
    """
    depends_on: list[Dependency] | None = Field([], validate_default=True)
    """
    Other drops this one composes with. Resolved at install time by the registry.
    """
    tags: list[Tag] | None = Field([], validate_default=True)
    """
    Free-form tags for search.
    """
    preview: str | None = Field(None, min_length=1)
    """
    Path (bundle-relative) to preview image. Recommended 1200x630 PNG.
    """
    preview_mock_data: str | None = Field(None, min_length=1)
    """
    Path (bundle-relative) to mock data JSON used by the live-preview sandbox.
    """
    locale_hint: str | None = Field(
        None, pattern="^[a-z]{2,3}(-[A-Z][a-z]{3})?(-[A-Z]{2}|-[0-9]{3})?$"
    )
    """
    Primary BCP 47 locale. Used for filtering + sort.
    """
    pricing: Pricing | None = None
    """
    Pricing block. Defaults to free.
    """
    monetization: Monetization | None = None
    """
    Monetization controls (tip-jar opt-in, Stripe account, payout currency).
    """
    royalty: Royalty | None = None
    """
    Royalty rules for forks. Meaningful when pricing.type=paid.
    """
    signature: Signature | None = None
    """
    Eternitas signature block. Optional; required by registry only when pricing.type=paid (v1.1).
    """
    control_panel: ControlPanelExtension | None = None
    """
    Type-specific extension for type=control-panel-template. See ADR-054.
    """
    skill: dict[str, Any] | None = None
    """
    Type-specific extension for type=skill. Reserved.
    """
    tool: dict[str, Any] | None = None
    """
    Type-specific extension for type=tool. Reserved.
    """
    theme: dict[str, Any] | None = None
    """
    Type-specific extension for type=theme. Reserved.
    """
    voice_pack: dict[str, Any] | None = None
    """
    Type-specific extension for type=voice-pack. Reserved.
    """
    workflow: dict[str, Any] | None = None
    """
    Type-specific extension for type=workflow. Reserved.
    """
