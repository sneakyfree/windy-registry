# contracts/ — Windy Drops registry agent-OPS manifest

`ops.mcp.v1.json` is the **canonical source of truth** for the Windy Drops
registry's remote agent-ops surface, governed by the Agent Control Doctrine
(**ADR-060** in `sneakyfree/windy-contracts`).

**Brand vs dev-name:** consumer brand is *Windy Drops*; this deployed service
is `windy-registry` (matches `/version` + the fleet-version key). The SDK /
artifact-spec / CLI live in the separate `windy-drops` repo.

**`ops`, not `control` (§2):** the product API (browse/publish/install drops,
library, ratings, Stripe payouts, federation) stays OUT of the ops surface.
This contract is health + deploy identity + the deep readiness probe
(`/health/full` checks Postgres + R2 + both JWKS).

- Remote agents attach over Streamable HTTP; the shim forwards the caller's
  **EPT** verbatim. Gaps (logs/config/selftest/redeploy) = punch list.
- Proven live: the woven packet drove production api.windydrops.com and
  surfaced a real R2-bucket http-404 degraded signal (2026-07-13).
- Change control: additive → `v1.1` via PR; breaking → new `v2` + tell Grant.
