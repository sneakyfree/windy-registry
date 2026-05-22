# Seed Echo HQ into a fresh registry

The first official drop. Bootstraps the catalog for cold deploys + sandbox resets.

## Canonical source

`sneakyfree/windy-control-panel:packages/official-drops/echo-hq/`. Contains:
- `SKILL.md` — manifest (YAML frontmatter)
- `render.html` — thin wrapper around render.js per ADR-053 postMessage protocol
- `render.js` — the dashboard logic
- `styles.css` — cyberpunk styling

Bump the manifest's `version` field whenever the bundle's contents change.

## One-shot upload (run from a workstation with the lockbox R2 keypair)

```bash
# 1. Bundle the four files into a zip at /tmp/windy-echo-hq-<ver>.zip
cd ~/windy-control-panel/packages/official-drops/echo-hq
VER=$(awk '/^version:/ {print $2; exit}' SKILL.md)
mkdir -p /tmp/echo-hq-stage && cp SKILL.md render.html render.js styles.css /tmp/echo-hq-stage/
( cd /tmp/echo-hq-stage && zip -r /tmp/windy-echo-hq-$VER.zip . )
SHA=$(shasum -a 256 /tmp/windy-echo-hq-$VER.zip | awk '{print $1}')
echo "version=$VER sha256=$SHA"

# 2. Upload to R2 (windycloud-userdata keypair — see ACCESS_LOCKBOX.md §R2)
export AWS_ACCESS_KEY_ID=<R2_ACCESS_KEY_ID>
export AWS_SECRET_ACCESS_KEY=<R2_SECRET_ACCESS_KEY>
export AWS_DEFAULT_REGION=auto
R2_ENDPOINT=https://193b347aedeaafe35de0b5a534b2d9aa.r2.cloudflarestorage.com
PREFIX=s3://windydrops-bundles/windy-echo-hq/$VER

aws --endpoint-url=$R2_ENDPOINT s3 cp /tmp/echo-hq-stage/render.html $PREFIX/render.html --content-type text/html
aws --endpoint-url=$R2_ENDPOINT s3 cp /tmp/echo-hq-stage/render.js   $PREFIX/render.js   --content-type application/javascript
aws --endpoint-url=$R2_ENDPOINT s3 cp /tmp/echo-hq-stage/styles.css  $PREFIX/styles.css  --content-type text/css
aws --endpoint-url=$R2_ENDPOINT s3 cp /tmp/echo-hq-stage/SKILL.md    $PREFIX/SKILL.md    --content-type text/plain
aws --endpoint-url=$R2_ENDPOINT s3 cp /tmp/windy-echo-hq-$VER.zip    $PREFIX/windy-echo-hq-$VER.zip --content-type application/zip

# 3. Verify public reachability
curl -sS -o /dev/null -w "render.html: HTTP %{http_code}\n" https://drops.windydrops.com/windy-echo-hq/$VER/render.html
```

If the SHA256 differs from `tools/seed_echo_hq.py`'s `SHA256` constant, update the script before running step 4.

## Seed the registry row

```bash
# Run inside the api container (alembic upgrade head has already run)
docker exec windy-registry-api python /app/tools/seed_echo_hq.py
```

The script is idempotent — re-running on a registry that already has
windy-echo-hq is a no-op.

## Verify

```bash
curl -sS https://api.windydrops.com/api/v1/drops | jq '.items[0].id'
# "windy-echo-hq"
```
