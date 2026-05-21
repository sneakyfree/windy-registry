# Federation Spec (WD-34 sketch, v1 contract-only)

Other ecosystems can adopt the Windy Drops format and federate with the canonical Windy Drops registry. v1 ships the contract; the actual cross-fetch implementation lands in v2.

## Why federation works

Drops are portable across registries because:

1. **Eternitas signing.** A drop signed by an Eternitas Passport is verifiable by any party that trusts Eternitas's JWKS — no registry-specific trust required.
2. **Globally-unique ids.** `drop_id` is namespaced kebab-case (typically `<author-slug>-<drop-slug>`). Cross-registry id collisions don't happen accidentally.
3. **Absolute bundle URLs.** `bundle_url` in a manifest is fully qualified — a peer registry can fetch the bytes directly.

## v2 endpoints (contract; v1 returns 501)

### `GET /api/v1/federation/peers`

Public. Returns a list of registered peer registries.

```json
{ "peers": [
    { "id": "peer-001",
      "public_domain": "registry.example.com",
      "discovery_url": "https://registry.example.com/.well-known/eternitas-federation.json",
      "trust": "eternitas",
      "registered_at": "2026-..."
    }
] }
```

### `GET /api/v1/federation/drops/{peer}/{drop_id}`

Auth-optional. Fetches the drop manifest from `peer`'s registry, verifies the Eternitas signature, caches locally, and returns the same shape as `GET /api/v1/drops/{id}`.

v1: returns `501` with the message and a pointer to this doc.

## Peer discovery document

Peers MUST publish `/.well-known/eternitas-federation.json` declaring:

```json
{
  "registry_api_base": "https://registry.example.com/api/v1",
  "bundle_domain": "drops.registry.example.com",
  "trust_anchor": {
    "name": "eternitas",
    "jwks_url": "https://api.eternitas.ai/.well-known/eternitas-keys"
  },
  "supported_formats": ["windy.drop.v1"],
  "supported_drop_types": [
    "control-panel-template", "skill", "tool", "theme", "voice-pack", "workflow"
  ],
  "supports_paid_drops": false,
  "abuse_contact": "abuse@example.com"
}
```

## Identity portability

A user's library is registry-local. v2 introduces federation-aware install:

- User browses peer A's marketplace
- User clicks Install on a drop published originally by peer A
- Local registry calls `GET /federation/drops/{peer-a}/{drop_id}`
- Local registry caches the manifest + verifies the signature
- Local registry installs into the user's library, pointing at peer A's bundle URL

The user's library row tracks `source_registry: <peer-id>` so usage analytics stay attributed correctly.

## Out of scope (v2, not v3)

- Cross-registry tipping (Stripe doesn't span registries cleanly)
- Cross-registry paid install (Stripe + tax + refund complexity)
- Federated trending feed (each registry maintains its own)
- Federated webhook delivery

## Trust model

Federation does NOT imply trust transitivity. Each registry verifies signatures against the **canonical Eternitas JWKS** (or its own pinned trust anchor). A peer registry compromising its own JWKS does not compromise the canonical Eternitas trust — signatures rooted in Eternitas's canonical key remain verifiable everywhere.

## Demo

A 50-line Python script outside the Windy ecosystem can today:

1. Fetch any drop's manifest via `GET https://api.windydrops.com/api/v1/drops/{id}`
2. Verify the Eternitas signature against `https://api.eternitas.ai/.well-known/eternitas-keys`
3. Download the bundle from the URL in the manifest
4. "Install" by writing to `~/.example-ecosystem/library/`

That's federation at the consumer level even before v2 ships server-side peer fetch.

## Strand reference

Sketched by WD-34 in `sneakyfree/windy-drops/docs/DNA_STRAND_MASTER_PLAN.md`. The v2 implementation is a future strand (numbering TBD).
