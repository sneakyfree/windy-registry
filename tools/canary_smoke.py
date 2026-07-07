"""canary_smoke.py — content-checking prod canary for the Windy Drops platform.

Status codes are not enough: on 2026-07-04 a Cloudflare redirect pointed the
entire bundle CDN at the marketing site and every URL kept returning clean
HTTP 200 HTML for three days. This canary asserts *content* — content types,
bundle SHA-256, no-redirect fetches — so that failure mode pages instead of
hiding.

Checks (against production by default; override with CANARY_REGISTRY_URL):
  1. /version           → 200, environment=production
  2. /api/v1/drops      → 200, ≥2 items
  3. first drop detail  → bundle_url fetch WITHOUT following redirects
                          must be a direct 200 application/zip whose bytes
                          hash to the recorded bundle_sha256
  4. render.html        → direct 200 text/html (no redirect)
  5. preview_url        → 200 image/png for every catalog item that has one
  6. /{id}/oembed       → 200 with provider_name "Windy Drops"

Exit 0 all-green; exit 1 with one line per failure otherwise.

Run in prod:   docker exec windy-registry-api python /app/tools/canary_smoke.py
Run anywhere:  python tools/canary_smoke.py  (needs httpx)
"""

from __future__ import annotations

import hashlib
import os
import sys

import httpx

REGISTRY = os.environ.get("CANARY_REGISTRY_URL", "https://api.windydrops.com").rstrip("/")
TIMEOUT = 30.0

failures: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


def _json(r: httpx.Response) -> dict | list | None:
    """Parse JSON or record a failure — HTML where JSON belongs IS the
    failure mode this canary exists to catch."""
    try:
        return r.json()
    except Exception:
        fail(f"{r.request.url} returned non-JSON body (content-type={r.headers.get('content-type')!r})")
        return None


def check_version(client: httpx.Client) -> None:
    r = client.get(f"{REGISTRY}/version")
    if r.status_code != 200:
        fail(f"/version → {r.status_code}")
        return
    body = _json(r)
    env = body.get("environment") if isinstance(body, dict) else None
    if env != "production":
        fail(f"/version environment={env!r} (expected production)")


def check_catalog(client: httpx.Client) -> list[dict]:
    r = client.get(f"{REGISTRY}/api/v1/drops")
    if r.status_code != 200:
        fail(f"catalog → {r.status_code}")
        return []
    body = _json(r)
    items = body.get("items", []) if isinstance(body, dict) else []
    if len(items) < 2:
        fail(f"catalog has {len(items)} drops (expected ≥2 official drops)")
    return items


def check_bundle(client: httpx.Client, drop_id: str) -> None:
    r = client.get(f"{REGISTRY}/api/v1/drops/{drop_id}")
    if r.status_code != 200:
        fail(f"detail {drop_id} → {r.status_code}")
        return
    detail = _json(r)
    if not isinstance(detail, dict):
        return
    bundle_url = detail.get("bundle_url")
    want_sha = detail.get("bundle_sha256")
    if not bundle_url or not want_sha:
        fail(f"detail {drop_id} missing bundle_url/bundle_sha256")
        return

    # follow_redirects=False is the point: the 2026-07-04 breakage was a
    # 302 to the marketing site that looked healthy to any -L fetch.
    b = client.get(bundle_url, follow_redirects=False)
    if b.status_code != 200:
        fail(f"bundle {drop_id} → {b.status_code} (redirect or missing; must be direct 200)")
        return
    ctype = b.headers.get("content-type", "")
    if "application/zip" not in ctype:
        fail(f"bundle {drop_id} content-type={ctype!r} (expected application/zip)")
        return
    got_sha = hashlib.sha256(b.content).hexdigest()
    if got_sha != want_sha:
        fail(f"bundle {drop_id} sha256 mismatch: recorded {want_sha[:12]}…, served {got_sha[:12]}…")

    render_url = bundle_url.rsplit("/", 1)[0] + "/render.html"
    h = client.get(render_url, follow_redirects=False)
    if h.status_code != 200:
        fail(f"render.html {drop_id} → {h.status_code} (must be direct 200)")
    elif "text/html" not in h.headers.get("content-type", ""):
        fail(f"render.html {drop_id} content-type={h.headers.get('content-type')!r}")


def check_previews(client: httpx.Client, items: list[dict]) -> None:
    for item in items:
        url = item.get("preview_url")
        if not url:
            continue
        r = client.get(url, follow_redirects=False)
        if r.status_code != 200 or "image/" not in r.headers.get("content-type", ""):
            fail(
                f"preview {item['id']} → {r.status_code} "
                f"{r.headers.get('content-type')!r} (expected direct 200 image/*)"
            )


def check_oembed(client: httpx.Client, drop_id: str) -> None:
    r = client.get(f"{REGISTRY}/api/v1/drops/{drop_id}/oembed")
    body = _json(r) if r.status_code == 200 else None
    if r.status_code != 200 or not isinstance(body, dict) or body.get("provider_name") != "Windy Drops":
        fail(f"oembed {drop_id} → {r.status_code}")


def main() -> int:
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
            check_version(client)
            items = check_catalog(client)
            if items:
                check_bundle(client, items[0]["id"])
                check_oembed(client, items[0]["id"])
                check_previews(client, items)
    except Exception as e:  # network down, DNS, TLS — still a red canary
        fail(f"unhandled: {type(e).__name__}: {e}")

    if failures:
        for f in failures:
            print(f"CANARY FAIL: {f}")
        return 1
    print(f"canary OK — {REGISTRY} all content checks green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
