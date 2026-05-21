#!/usr/bin/env bash
# tools/r2-provision.sh — WD-13. Provision the windydrops-bundles R2 bucket
# and attach the drops.windydrops.com custom domain.
#
# Idempotent: re-running on an already-provisioned account is a no-op.
# Requires the Cloudflare god token (Workers + R2 + DNS scope) — pull from
# ~/kit-army-config/ACCESS_LOCKBOX.md §"TheWindstormCloudflareGodToken" and
# export as $CF_TOKEN before running.
#
# Per docs/runbooks/r2-setup.md. ADR-053 §"Bundle storage".

set -euo pipefail

: "${CF_TOKEN:?CF_TOKEN is required — pull from ~/kit-army-config/ACCESS_LOCKBOX.md §TheWindstormCloudflareGodToken}"
: "${CF_ACCOUNT_ID:=193b347aedeaafe35de0b5a534b2d9aa}"  # Grant's CF account
: "${BUCKET:=windydrops-bundles}"
: "${PUBLIC_DOMAIN:=drops.windydrops.com}"
: "${WINDYDROPS_ZONE_ID:=5c9c4a59c7cc34487c6558697d6d3c07}"  # per project_windy_drops.md
: "${LOCATION_HINT:=ENAM}"

API="https://api.cloudflare.com/client/v4"
H_AUTH=(-H "Authorization: Bearer $CF_TOKEN" -H "Content-Type: application/json")

echo "[r2-provision] checking bucket $BUCKET"
exists=$(curl -fsS "${H_AUTH[@]}" "$API/accounts/$CF_ACCOUNT_ID/r2/buckets/$BUCKET" \
  | jq -r '.success // false')

if [ "$exists" = "true" ]; then
  echo "[r2-provision] bucket $BUCKET already exists"
else
  echo "[r2-provision] creating bucket $BUCKET (location=$LOCATION_HINT)"
  curl -fsS "${H_AUTH[@]}" \
    "$API/accounts/$CF_ACCOUNT_ID/r2/buckets" \
    -d "{\"name\":\"$BUCKET\",\"locationHint\":\"$LOCATION_HINT\"}" \
    | jq '.success, .errors // empty'
fi

echo "[r2-provision] attaching custom domain $PUBLIC_DOMAIN → $BUCKET"
curl -fsS "${H_AUTH[@]}" \
  "$API/accounts/$CF_ACCOUNT_ID/r2/buckets/$BUCKET/domains/custom" \
  -d "{\"domain\":\"$PUBLIC_DOMAIN\",\"zoneId\":\"$WINDYDROPS_ZONE_ID\",\"enabled\":true}" \
  | jq '.success, .errors // empty' || echo "  (already attached or domain conflict — fine)"

echo "[r2-provision] applying CORS rules (windydrops.com + subdomains)"
cors_json='{
  "rules": [{
    "allowed": {
      "origins": ["https://windydrops.com", "https://*.windydrops.com", "http://localhost:*"],
      "methods": ["GET", "HEAD"],
      "headers": ["*"]
    },
    "maxAgeSeconds": 3600,
    "exposeHeaders": ["ETag"]
  }]
}'
curl -fsS "${H_AUTH[@]}" -X PUT \
  "$API/accounts/$CF_ACCOUNT_ID/r2/buckets/$BUCKET/cors" \
  -d "$cors_json" \
  | jq '.success, .errors // empty'

echo "[r2-provision] verifying public access (DNS may take a moment)"
sleep 5
status=$(curl -s -o /dev/null -w "%{http_code}" "https://$PUBLIC_DOMAIN/")
echo "[r2-provision] https://$PUBLIC_DOMAIN/ → HTTP $status (404 on empty bucket is expected)"

echo "[r2-provision] done — bucket=$BUCKET domain=$PUBLIC_DOMAIN account=$CF_ACCOUNT_ID"
