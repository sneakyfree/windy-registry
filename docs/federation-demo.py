#!/usr/bin/env python3
"""federation-demo.py — F19. A standalone client that demonstrates federation
at the consumer level even before WD-34's server-side peer fetch ships.

Run:
    python federation-demo.py kit-oc5-echo-hq

What it does:
  1. Fetches the drop manifest from https://api.windydrops.com/api/v1/drops/<id>
  2. Verifies the Eternitas signature (if present) against the canonical JWKS
     at https://api.eternitas.ai/.well-known/eternitas-keys
  3. Downloads the bundle from the bundle_url in the manifest
  4. Verifies the bundle SHA-256
  5. "Installs" by writing to ~/.example-ecosystem/library/<id>/<version>/

This is federation-at-the-edge: ANY ecosystem can adopt the same flow.
The trust root is Eternitas, not Windy Drops. v2's
GET /federation/drops/{peer}/{id} server-side fetch is sketched in
windy-registry/src/.../routes/federation.py.

Dependencies:
    pip install httpx cryptography
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.serialization import load_pem_public_key

REGISTRY = "https://api.windydrops.com"
ETERNITAS_JWKS = "https://api.eternitas.ai/.well-known/eternitas-keys"
LIBRARY_ROOT = Path.home() / ".example-ecosystem" / "library"


def canonicalize(value) -> str:
    """Match canonical.py from the windy-drops SDK + registry."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def verify_signature(manifest: dict, bundle_sha256: str) -> bool:
    sig = manifest.get("signature")
    if not sig:
        print("  (drop is unsigned; trust deferred to author display name)")
        return True
    if sig.get("algorithm") != "ES256":
        print(f"  ✗ unsupported algorithm: {sig.get('algorithm')}")
        return False

    manifest_sans_sig = {k: v for k, v in manifest.items() if k != "signature"}
    canonical = canonicalize(manifest_sans_sig)
    message = (canonical + bundle_sha256).encode("utf-8")
    expected_digest = "sha256:" + hashlib.sha256(message).hexdigest()
    if sig.get("signed_digest") != expected_digest:
        print(f"  ✗ digest mismatch (claimed {sig.get('signed_digest')}, recomputed {expected_digest})")
        return False

    raw_sig = base64.b64decode(sig["signature"])
    if len(raw_sig) != 64:
        print(f"  ✗ signature length wrong: {len(raw_sig)} bytes")
        return False
    r_int = int.from_bytes(raw_sig[:32], "big")
    s_int = int.from_bytes(raw_sig[32:], "big")
    der_sig = encode_dss_signature(r_int, s_int)

    jwks = httpx.get(ETERNITAS_JWKS, timeout=10.0).json()
    for key in jwks.get("keys", []):
        if key.get("alg") not in (None, "ES256"):
            continue
        # Reconstruct PEM from JWK (P-256, x/y).
        try:
            from jose import jwk as jose_jwk  # use jose if available
            pem = jose_jwk.construct(key).to_pem()
            pub = load_pem_public_key(pem)
        except Exception:
            continue
        if not isinstance(pub, ec.EllipticCurvePublicKey):
            continue
        try:
            pub.verify(der_sig, message, ec.ECDSA(hashes.SHA256()))
            print(f"  ✓ signature verified (signer: {sig['signer']['passport']})")
            return True
        except InvalidSignature:
            continue
    print("  ✗ no key in Eternitas JWKS verified this signature")
    return False


def install(drop_id: str) -> int:
    print(f"Fetching {drop_id} from {REGISTRY}…")
    r = httpx.get(f"{REGISTRY}/api/v1/drops/{drop_id}", timeout=15.0)
    r.raise_for_status()
    detail = r.json()
    manifest = detail["manifest"]
    bundle_url = detail["bundle_url"]
    expected_sha = detail["bundle_sha256"]
    version = detail["current_version"]

    print(f"  manifest version: {version}")
    print(f"  bundle: {bundle_url}")

    print("Verifying signature…")
    if not verify_signature(manifest, expected_sha):
        return 2

    print("Downloading bundle…")
    bundle_resp = httpx.get(bundle_url, timeout=60.0)
    bundle_resp.raise_for_status()
    actual_sha = hashlib.sha256(bundle_resp.content).hexdigest()
    if actual_sha != expected_sha:
        print(f"  ✗ bundle SHA mismatch (expected {expected_sha}, got {actual_sha})")
        return 3
    print(f"  ✓ bundle SHA matches ({len(bundle_resp.content)} bytes)")

    target = LIBRARY_ROOT / drop_id / version
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{drop_id}-{version}.zip").write_bytes(bundle_resp.content)
    (target / "SKILL.md.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"  ✓ installed at {target}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python federation-demo.py <drop-id>", file=sys.stderr)
        sys.exit(1)
    sys.exit(install(sys.argv[1]))
