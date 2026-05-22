"""windy_drops_spec — Python bindings for the windy.drop.v1 manifest format.

Generated Pydantic v2 models live in `_generated.py` (codegen output from
`schemas/windy.drop.v1.json`). This module re-exports the canonical names plus
hand-curated constants (drop types, pricing types, integrity bands, clearance
levels) for ergonomic imports.

Both this package and the TypeScript sibling (@windy/drops-artifact-spec on
npm) are codegen'd from the SAME JSON Schema. A manifest accepted by one MUST
be accepted by the other. Byte-identity is enforced by the WD-11 conformance
harness.

Strand: WD-2 of docs/DNA_STRAND_MASTER_PLAN.md.
"""

from __future__ import annotations

from typing import Final, Literal

from ._generated import DropManifest

__all__ = [
    "DropManifest",
    "DROP_TYPES",
    "DropType",
    "PRICING_TYPES",
    "PricingType",
    "INTEGRITY_BANDS",
    "IntegrityBand",
    "CLEARANCE_LEVELS",
    "ClearanceLevel",
]

# v1 reserved drop types. New types are additive in v1.x via ADR + consumer
# surface registration.
DROP_TYPES: Final[tuple[str, ...]] = (
    "control-panel-template",
    "skill",
    "tool",
    "theme",
    "voice-pack",
    "workflow",
)
DropType = Literal[
    "control-panel-template",
    "skill",
    "tool",
    "theme",
    "voice-pack",
    "workflow",
]

# v1 pricing types. `free` + `tip-jar` ship in v1; `paid` ships in v1.1;
# `subscription` is schema-reserved (not in v1.1 — needs new ADR).
PRICING_TYPES: Final[tuple[str, ...]] = ("free", "tip-jar", "paid", "subscription")
PricingType = Literal["free", "tip-jar", "paid", "subscription"]

# Eternitas integrity bands. Snapshot at signing time; registry does NOT
# update later (per ADR-053 §"Signing + trust").
INTEGRITY_BANDS: Final[tuple[str, ...]] = (
    "critical",
    "poor",
    "fair",
    "good",
    "exceptional",
)
IntegrityBand = Literal["critical", "poor", "fair", "good", "exceptional"]

CLEARANCE_LEVELS: Final[tuple[str, ...]] = (
    "registered",
    "verified",
    "cleared",
    "top_secret",
    "eternal",
)
ClearanceLevel = Literal["registered", "verified", "cleared", "top_secret", "eternal"]
