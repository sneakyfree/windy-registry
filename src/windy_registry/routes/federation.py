"""federation.py — WD-34. v1 ships the CONTRACT, not the implementation.

Per ADR-053 §"Federation": v1 does NOT cross-fetch from peer registries.
The endpoints exist so external implementers know the wire shape, and
so consumers can probe federation support without 404 misinterpretation.

v2 will:
  - persist peer registrations in a federation_peers table
  - GET /api/v1/federation/drops/{peer}/{id} fetches via the peer's
    /api/v1/drops/{id} endpoint and caches the manifest locally
  - require the peer to publish a /.well-known/eternitas-federation.json
    discovery document declaring its public domain + JWKS URLs

The full spec lives in docs/federation-spec.md.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1/federation", tags=["federation"])


@router.get("/peers")
def list_peers() -> dict[str, list]:
    """Public — currently always returns an empty list. v2 will return
    registered peer registries the local registry mirrors / federates with."""
    return {"peers": []}


@router.get("/drops/{peer}/{drop_id}")
def cross_registry_drop(peer: str, drop_id: str) -> None:
    """v2 will fetch the drop manifest from a peer registry, verify the
    Eternitas signature against the canonical Eternitas JWKS, and cache
    the result. v1 returns 501 + contract pointer."""
    raise HTTPException(
        status_code=501,
        detail={
            "error": "federation_not_implemented_v1",
            "message": (
                "Federation is contract-only in v1. See docs/federation-spec.md "
                "for the v2 wire shape. Any Eternitas-signed drop is portable "
                "across any Eternitas-trusting registry; v1 just doesn't auto-fetch."
            ),
            "see_also": "https://github.com/sneakyfree/windy-registry/blob/main/docs/federation-spec.md",
        },
    )
