# SUBSTRATE.md — windy-registry operational substrate

Per ADR-048 (substrate-as-code). Documents the operational realities every deploy must respect.

## Deploy target

- **Host:** Shared EC2 `54.88.113.79` at `/opt/windy-registry/deploy-prod/`, alongside the other Windy services (windy-pro, eternitas, windy-clone, windy-mail, windy-search, windy-call/text/cell).
- **Reverse proxy:** The host's shared `deploy-caddy-1` container (deployed under the windy-mail compose). Vhost routes are added via Caddy's admin API on port 2019 and persisted to `/config/caddy/autosave.json`. TLS auto via Let's Encrypt + zerossl fallback.
- **Network:** `deploy_backend` (external, bridge). Our api container attaches here so Caddy can resolve `windy-registry-api:8500`.
- **Public domain:** `api.windydrops.com` (registry API) + `drops.windydrops.com` (R2 bundle CDN, separate origin).
- **Container DNS names:** `windy-registry-api`, `windy-registry-postgres`, `windy-registry-redis` (set via `container_name:` so Caddy DNS resolution survives recreates).

## Compose invocation

Per `feedback_compose_project_name_collision` (ADR-046) — `name:` directive set explicitly:
- Dev: `name: windy-registry`
- Prod: `name: windy-registry-prod`

Per `feedback_windy_chat_compose_invocation` — BOTH compose files + `--env-file`:

```bash
docker compose \
  -f deploy/docker-compose.yml \
  -f deploy/docker-compose.prod.yml \
  --env-file deploy/.env.production \
  up -d --force-recreate
```

Per `feedback_compose_restart_envfile` — env changes require `--force-recreate`, NOT `restart`.

## /version contract

`GET /version` returns the MF1 contract shape. Verified by `tests/test_version.py`. Reference impl: `eternitas/src/eternitas/routes/version.py` (PR #74). Cron at `kit-army-config/.github/workflows/deployed-state.yml` polls it every 30 min.

## R2 bucket

- **Bucket:** `windydrops-bundles` (per AUDIT_2026-05-21.md Gap #1 — `<product>-<purpose>` convention)
- **Account:** `193b347aedeaafe35de0b5a534b2d9aa`
- **Region:** `wnam`
- **Public via:** `drops.windydrops.com` (CF-proxied; SSL automatic)
- **Provisioning:** `tools/r2-provision.sh` (lands with WD-13)

## Secrets

All credentials live in `~/kit-army-config/ACCESS_LOCKBOX.md`. NEVER commit token values. Reference by NAME in this repo (per `feedback_no_secrets_in_public_docs`).

## Caddy reload discipline

Per `feedback_caddy_inode_binding_v2`: write full Caddyfile via `sudo tee`, then `caddy reload`. NEVER `caddy restart` — restart regresses inode-bound hostnames on the shared instance.

## Backup (MF4)

Will land with WD-22. Pattern: nightly `pg_dump | gzip | aws s3 cp` to `s3://windy-backups-windyregistry-prod` (SSE-S3 + versioning + lifecycle 30d→90d Glacier→expire 365d). Restore drill documented in `docs/runbooks/backup-restore.md`.

## Auto-deploy

Will land with WD-12.B: rsync-from-runner pattern per `feedback_mind_auto_deploy_unwired` resolution. Workflow at `.github/workflows/deploy.yml`.

## Strand reference

`SUBSTRATE.md` lives next to deploy/ and is updated as each WD-12 → WD-22 strand lands. Read this file before any prod-touching change.
