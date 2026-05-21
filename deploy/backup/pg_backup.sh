#!/usr/bin/env bash
# pg_backup.sh — WD-22. Nightly Postgres backup → S3 with lifecycle
# (30d → 90d Glacier → expire 365d). Mirrors the eternitas pattern (MF4).
#
# Run via the systemd timer in deploy/backup/pg_backup.timer. Restore drill
# instructions in docs/runbooks/backup-restore.md (read AND test once a
# month per MF4 — silent backups are theatre).

set -euo pipefail

: "${PGHOST:=postgres}"
: "${PGPORT:=5432}"
: "${PGUSER:=windyregistry}"
: "${PGDATABASE:=windyregistry}"
: "${PGPASSWORD:?PGPASSWORD is required}"
: "${BACKUP_BUCKET:=windy-backups-windyregistry-prod}"
: "${BACKUP_PREFIX:=}"
: "${AWS_REGION:=us-east-1}"

timestamp="$(date -u +%Y-%m-%dT%H%M%SZ)"
filename="windyregistry-${timestamp}.sql.gz"
s3_uri="s3://${BACKUP_BUCKET}/${BACKUP_PREFIX}${filename}"

echo "[pg_backup] dumping ${PGDATABASE}@${PGHOST}:${PGPORT} → ${s3_uri}"

# pg_dump custom format compresses internally; we pipe through gzip too because
# the custom format is then-gzipped. Restore via:
#   aws s3 cp s3://... - | gunzip | pg_restore -d <new_db>
export PGPASSWORD
pg_dump \
  -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" \
  --format=custom --no-owner --no-acl \
  | gzip -9 \
  | aws s3 cp - "$s3_uri" \
      --region "$AWS_REGION" \
      --expected-size 5368709120 \
      --sse AES256

echo "[pg_backup] uploaded $(date -u +%Y-%m-%dT%H:%M:%SZ)"
