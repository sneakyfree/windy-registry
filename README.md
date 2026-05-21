# windy-registry

FastAPI service for the **Windy Drops** marketplace registry. Owned by strands **WD-12..WD-22** of [`sneakyfree/windy-drops/docs/DNA_STRAND_MASTER_PLAN.md`](https://github.com/sneakyfree/windy-drops/blob/main/docs/DNA_STRAND_MASTER_PLAN.md).

## What this is

The central registry service for [Windy Drops](https://windydrops.com) — the open marketplace for the Windy ecosystem. The registry:

- Validates manifests authors publish via the SDKs (`@windy/drops-sdk` on npm, `windy-drops` on PyPI)
- Stores manifests + bundle pointers (bundles live on Cloudflare R2 at `drops.windydrops.com`)
- Serves browse / search / trending / library / fork / rating endpoints
- Verifies Eternitas signatures
- Dispatches webhook events (`drop.published`, `drop.installed`, …) to subscribers
- Hosts the tip + paid-install flow (Stripe Connect Express)

## Strand status (mirrors DNA_STRAND_MASTER_PLAN.md)

| Strand | Subject | Status |
|---|---|---|
| WD-12 | repo bootstrap + FastAPI + /version + /health | done |
| WD-13 | R2 bucket + drops.windydrops.com domain | pending |
| WD-14 | Postgres schema + Alembic | pending |
| WD-15 | dual JWKS auth middleware | pending |
| WD-16 | browse + search + trending | pending |
| WD-17 | library install/uninstall/list | pending |
| WD-18 | publish + signature verify | pending |
| WD-19 | fork + lineage | pending |
| WD-20 | ratings + reviews | pending |
| WD-21 | webhook substrate | pending |
| WD-22 | Postgres backup (MF4) | pending |

## Run locally

```bash
uv venv
uv pip install -e '.[test]'
.venv/bin/uvicorn windy_registry.main:app --reload --port 8500
```

```bash
curl http://localhost:8500/version
curl http://localhost:8500/health
curl http://localhost:8500/health/full
```

## License

MIT
