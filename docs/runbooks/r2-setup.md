# R2 bucket setup runbook (WD-13)

The registry needs `windydrops-bundles` on R2 with `drops.windydrops.com` as the public-read custom domain. The SDK publish flow (WD-8) eventually PUTs zip bundles here; consumer surfaces fetch them via the public domain.

This setup is **one-time per environment** (prod, staging if/when it lands). It touches Grant's Cloudflare account (`193b347aedeaafe35de0b5a534b2d9aa`) and so should be executed by Grant or by an agent with explicit one-time authorization — NOT by drive-by automation.

## What gets provisioned

| Resource | Purpose | Tier |
|---|---|---|
| R2 bucket `windydrops-bundles` | Immutable bundle storage | R2 free tier covers ~10 GB / 1M Class A ops |
| Custom domain `drops.windydrops.com` | Public-read URL | CF-proxied; SSL automatic |
| CORS rule | `windydrops.com` + `*.windydrops.com` + `localhost:*` may GET | required for marketplace UI preview-image fetch |

## Pre-flight

1. Cloudflare god token (Workers + R2 + DNS scope) — pull from `~/kit-army-config/ACCESS_LOCKBOX.md` §"TheWindstormCloudflareGodToken".
2. `windydrops.com` zone id (already in lockbox + `~/.claude/.../memory/project_windy_drops.md`).
3. `jq` installed locally.

## Run

```bash
export CF_TOKEN="cfut_…"   # from lockbox; NEVER commit
bash tools/r2-provision.sh
```

The script is idempotent — re-running on an already-provisioned account is a no-op (bucket exists check, domain attach errors are tolerated as "already attached").

## Verify

```bash
# 1. Bucket exists.
curl -sS -H "Authorization: Bearer $CF_TOKEN" \
  https://api.cloudflare.com/client/v4/accounts/193b347aedeaafe35de0b5a534b2d9aa/r2/buckets/windydrops-bundles \
  | jq

# 2. Public domain resolves.
dig drops.windydrops.com

# 3. Public domain responds (404 on empty bucket is expected; non-404 not-2xx is a problem).
curl -sI https://drops.windydrops.com/ | head -3

# 4. CORS preflight from windydrops.com origin.
curl -sI -X OPTIONS \
  -H 'Origin: https://windydrops.com' \
  -H 'Access-Control-Request-Method: GET' \
  https://drops.windydrops.com/x \
  | grep -i access-control-allow
```

## Wire into the registry

Update `deploy/.env.production` after provisioning:

```bash
R2_ACCOUNT_ID=193b347aedeaafe35de0b5a534b2d9aa
R2_BUCKET=windydrops-bundles
R2_PUBLIC_DOMAIN=drops.windydrops.com
# Lockbox: ACCESS_LOCKBOX.md §"R2 Distribution Buckets — Windy Desktop App Releases"
# The "windycloud-userdata" keypair works across all buckets ("Apply to all buckets" scope).
R2_ACCESS_KEY_ID=<from lockbox>
R2_SECRET_ACCESS_KEY=<from lockbox>
```

After updating the env file, the GET `/.well-known/r2-config` endpoint (WD-16) will serve these values to the SDK, so `windy-drops publish` discovers the upload target automatically.

## Bundle path convention

```
windydrops-bundles/
  <drop-id>/
    <version>/
      <drop-id>-<version>.zip     # the canonical artifact
      SKILL.md                    # unzipped for cheap fetches (optional)
      preview.png                 # extracted for OG metadata (optional)
      render.html                 # iframe target for /preview (WD-23)
```

Bundles are **immutable per version**. A new version publishes a new `<version>/` path. Withdrawals don't delete from R2 — installed users keep working.

## Cost expectations

R2 free tier:
- 10 GB storage
- 1M Class A ops / month (PUT, COPY, POST, LIST, DELETE)
- 10M Class B ops / month (GET, HEAD)
- $0 egress

A drop bundle averages ~50 KB; even at 100k drops + 100M monthly preview fetches the free tier should hold.

## Strand reference

WD-13 of `sneakyfree/windy-drops/docs/DNA_STRAND_MASTER_PLAN.md`. Bucket name correction per AUDIT_2026-05-21.md Gap #1.
