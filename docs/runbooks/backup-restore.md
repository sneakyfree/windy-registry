# Postgres backup + restore runbook (WD-22 / MF4)

Per MF4 — backups are theatre until you've tested a restore. **Restore drill cadence: monthly.**

## What's running

`pg_backup.service` (oneshot) runs nightly at **03:06 UTC** via `pg_backup.timer`. Each invocation produces `s3://windy-backups-windyregistry-prod/windyregistry-<ISO8601>.sql.gz` (SSE-S3, versioned, lifecycle: 30d Glacier → 120d Deep Archive → 365d expire).

```
03:06 UTC pg_backup.service
  ├── pg_dump --format=custom ...  (registry Postgres)
  ├── gzip -9                       (compress)
  └── aws s3 cp - s3://...          (SSE-S3)
```

## Setup (one-time, prod)

```bash
# 1. Create the S3 bucket with versioning + SSE-S3.
aws s3 mb s3://windy-backups-windyregistry-prod --region us-east-1
aws s3api put-bucket-versioning \
  --bucket windy-backups-windyregistry-prod \
  --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption \
  --bucket windy-backups-windyregistry-prod \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

# 2. Apply the lifecycle policy.
aws s3api put-bucket-lifecycle-configuration \
  --bucket windy-backups-windyregistry-prod \
  --lifecycle-configuration file://deploy/backup/lifecycle.json

# 3. IAM user for backup uploads (read-only on Postgres role + write-only to bucket).
#    Save credentials in /etc/windy-registry-backup.env (see template below).

# 4. Install systemd units.
sudo cp deploy/backup/pg_backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pg_backup.timer

# 5. Verify the timer fires (next scheduled run shown).
systemctl list-timers pg_backup.timer
```

`/etc/windy-registry-backup.env` template:

```bash
PGHOST=postgres
PGPORT=5432
PGUSER=windyregistry
PGDATABASE=windyregistry
PGPASSWORD=...          # from ~/kit-army-config/secrets/
BACKUP_BUCKET=windy-backups-windyregistry-prod
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=...   # backup IAM user; from lockbox
AWS_SECRET_ACCESS_KEY=...
```

## Smoke test (after setup; run before declaring backups done)

```bash
sudo systemctl start pg_backup.service
sudo journalctl -u pg_backup.service --no-pager -n 30
# expect: "[pg_backup] uploaded <timestamp>" + exit 0
aws s3 ls s3://windy-backups-windyregistry-prod/ | tail -3
# expect: today's .sql.gz at non-zero size
```

## Restore drill (monthly, MANDATORY per MF4)

```bash
# 1. Fetch the latest backup (or a specific one).
LATEST=$(aws s3 ls s3://windy-backups-windyregistry-prod/ \
  --region us-east-1 | sort | tail -1 | awk '{print $4}')
aws s3 cp "s3://windy-backups-windyregistry-prod/$LATEST" \
  /tmp/restore.sql.gz --region us-east-1

# 2. Spin up a side Postgres to restore into (so production is untouched).
docker run --rm -d --name pg-restore-test \
  -e POSTGRES_USER=restore -e POSTGRES_PASSWORD=restore \
  -e POSTGRES_DB=restore -p 5433:5432 postgres:16-alpine
sleep 5  # wait for postgres startup

# 3. pg_restore from the gzip'd custom-format dump.
gunzip -c /tmp/restore.sql.gz \
  | PGPASSWORD=restore pg_restore \
      -h localhost -p 5433 -U restore -d restore \
      --no-owner --no-acl --clean --if-exists

# 4. Sanity-check row counts vs production.
PGPASSWORD=restore psql -h localhost -p 5433 -U restore -d restore \
  -c "SELECT 'drops', COUNT(*) FROM drops UNION ALL
      SELECT 'drop_versions', COUNT(*) FROM drop_versions UNION ALL
      SELECT 'user_library', COUNT(*) FROM user_library UNION ALL
      SELECT 'authors', COUNT(*) FROM authors UNION ALL
      SELECT 'follows', COUNT(*) FROM follows UNION ALL
      SELECT 'ratings', COUNT(*) FROM ratings UNION ALL
      SELECT 'tips', COUNT(*) FROM tips
      ORDER BY 1;"

# 5. Teardown.
docker stop pg-restore-test
rm /tmp/restore.sql.gz
```

**Record the restore drill outcome in `~/kit-army-config/docs/restore-drill-log.md`** so the cadence is auditable.

## RPO / RTO

- **RPO:** ≤24h (nightly snapshots; point-in-time recovery is a v2 ask).
- **RTO:** ≤30 min (restore drill above takes ~15 min on a recent dataset; budget 30 min for cold setup).
- **Versioning** is enabled on the bucket, so accidental deletion is recoverable for 90 noncurrent days.

## Alerts

Add Prometheus / CW alert on the systemd `OnFailure=`:
- if `pg_backup.service` fails 2 nights in a row → page oncall.
- if no upload in the last 36 hours → page oncall.

The cron at `kit-army-config/.github/workflows/deployed-state.yml` should add a backup-freshness check (TODO).

## Related

- ADR-053 (mentions MF4 indirectly via AUDIT_2026-05-21.md "Additional observations")
- MF4 reference: `~/kit-army-config/docs/marathon-foundations-program-2026-05-11.md`
- eternitas backup pattern: `~/eternitas/deploy/backup/`
- WD-22 strand: `sneakyfree/windy-drops/docs/DNA_STRAND_MASTER_PLAN.md`
