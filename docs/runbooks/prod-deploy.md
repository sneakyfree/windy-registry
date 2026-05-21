# Prod deploy runbook (one-time, then auto)

The artifacts to ship windy-registry to production are all in this repo. The one-time setup steps that a CI runner can't do for you are:

## 1. Provision R2 (~2 min)

```bash
export CF_TOKEN="cfut_…"   # from ~/kit-army-config/ACCESS_LOCKBOX.md §TheWindstormCloudflareGodToken
bash tools/r2-provision.sh
```

Creates `windydrops-bundles` bucket + `drops.windydrops.com` custom domain + CORS rules. Idempotent. See `docs/runbooks/r2-setup.md`.

## 2. Provision the host (~10 min)

Pick a Linux host. EC2 t3.small in us-east-1 is the ecosystem default. Install:

```bash
# Docker + compose
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Caddy (for TLS termination in front of port 8500)
sudo apt-get install -y caddy

# Initial /opt/windy-registry directory
sudo mkdir -p /opt/windy-registry
sudo chown $USER /opt/windy-registry
```

## 3. Populate `deploy/.env.production` from the lockbox (~5 min)

Copy `deploy/.env.example` to `/opt/windy-registry/deploy/.env.production`. From `~/kit-army-config/ACCESS_LOCKBOX.md` fill in:

| Lockbox section | env var |
|---|---|
| R2 Distribution Buckets (windycloud-userdata) | `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` |
| Stripe Connect | `STRIPE_SECRET_KEY`, `STRIPE_CONNECT_CLIENT_ID`, `STRIPE_WEBHOOK_SECRET` |
| Postgres for windy-registry | `DATABASE_URL` (use a random 32-char password) |

## 4. Set deploy secrets in GitHub (~3 min)

```bash
gh secret set DEPLOY_HOST -b "<host-IP-or-DNS>"
gh secret set DEPLOY_USER -b "ubuntu"
gh secret set DEPLOY_SSH_KEY < ~/.ssh/your-deploy-key.pem
```

## 5. First deploy

Push to main (or run `gh workflow run deploy.yml`). The workflow does the rest:
- snapshot
- rsync
- inject COMMIT_SHA + BUILD_TIMESTAMP
- compose up --force-recreate
- alembic upgrade head
- probe /version + /health/full

## 6. DNS + Caddy (~5 min)

Point `api.windydrops.com` A record at the host IP. Restart Caddy (`sudo systemctl reload caddy` — NEVER `restart`, per `feedback_caddy_inode_binding_v2`).

## 7. Verify

```bash
curl https://api.windydrops.com/version
# → { service: "windy-registry", version: "...", commit_sha: "...", ... }

curl https://api.windydrops.com/health/full
# → { status: "ok", database: "ok", r2_bucket: "ok", jwks: { pro: "ok", eternitas: "ok" } }
```

## 8. Flip kit-army-config to `live`

In `~/kit-army-config/services.yaml`, change the `windy-registry` entry's `status` from `pending` to `live`. The cron will start polling within 30 min.

## 9. Backup + integrity refresh systemd units (~3 min)

```bash
sudo cp /opt/windy-registry/deploy/backup/pg_backup.{service,timer} /etc/systemd/system/
sudo cp /opt/windy-registry/deploy/integrity-refresh.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pg_backup.timer integrity-refresh.timer
systemctl list-timers pg_backup.timer integrity-refresh.timer
```

## 10. Smoke-test the publish loop (~5 min)

```bash
# As an SDK user:
mkdir /tmp/test-drop && cd /tmp/test-drop
windy-drops new --type skill ./my-test-drop
cd my-test-drop
export WINDY_REGISTRY_TOKEN="<your Pro JWT>"
windy-drops publish .
# → ✓ published your-handle-my-test-drop@0.1.0
```

Then go to `https://windydrops.com/d/your-handle-my-test-drop` and confirm the card renders.

## Rollback

The GHA workflow snapshots `/opt/windy-registry/` to `/opt/windy-registry.pre-<timestamp>/` before each rsync. To roll back:

```bash
ssh deploy-host
sudo systemctl stop docker-compose@windy-registry  # or however it's wrapped
sudo mv /opt/windy-registry /opt/windy-registry.failed
sudo mv /opt/windy-registry.pre-<timestamp> /opt/windy-registry
cd /opt/windy-registry
sudo docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.prod.yml --env-file deploy/.env.production up -d --force-recreate
```

## What this closes

| Gap | Status after these steps |
|---|---|
| G3 (no prod deploy) | ✅ — deploy workflow runs on every push to main |
| G7 (no /version poll) | ✅ — kit-army-config flipped to live |
| G18 (Caddy install) | ✅ — covered in step 2 |
| G20 (lockbox token wiring) | ✅ — covered in step 3 |
| WD-13 (R2 bucket actually exists) | ✅ — step 1 |

After these one-time steps, every subsequent code push deploys itself.
