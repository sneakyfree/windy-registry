"""signature_verify.py — verify the ES256 signature on a published manifest.

The SDK derived `signed_digest = sha256(canonical_manifest_sans_sig || bundle_sha256_hex)`
and signed the same bytes with the author's Eternitas Passport private key.

To verify on the registry side:
  1. Strip the signature block from the manifest
  2. Canonicalize (same algorithm as the SDK)
  3. Recompute the digest hex
  4. Fetch the signer's public key from the Eternitas JWKS
  5. Verify the ES256 signature over (canonical || bundle_sha256_hex)
  6. (And: assert the digest in the signature block matches our re-computation)

Returns a structured result so the caller can record signer attributes
(integrity_band, clearance_level snapshot) on the drop_version row.

Strand: WD-18.
"""

from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from jose import jwk

from ..config import Settings, get_settings
from .canonical import canonicalize

_JWKS_TTL_SECONDS = 300
_jwks_cache: dict[str, tuple[float, dict[str, Any]]] = {}


@dataclass(frozen=True)
class VerifyResult:
    valid: bool
    signer_passport: str | None = None
    signer_integrity_band: str | None = None
    signer_clearance_level: str | None = None
    error: str | None = None


async def _fetch_jwks(url: str) -> dict[str, Any]:
    entry = _jwks_cache.get(url)
    if entry is not None and entry[0] > time.time():
        return entry[1]
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
    _jwks_cache[url] = (time.time() + _JWKS_TTL_SECONDS, data)
    return data


def reset_jwks_cache_for_tests() -> None:
    _jwks_cache.clear()


async def verify_bundle_bytes(bundle_url: str, expected_sha256: str) -> bool:
    """G10: optionally re-fetch the bundle from R2 + verify the SHA-256.

    Enabled when WINDY_VERIFY_BUNDLE_BYTES=1 in the env. v1 default trusts
    the SHA in the request body (publish is auth'd; the SDK that computed
    the SHA also uploaded the bytes). v1.1 hardening flips this on.
    """
    import os
    if os.environ.get("WINDY_VERIFY_BUNDLE_BYTES") != "1":
        return True
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            r = await client.get(bundle_url)
            r.raise_for_status()
            return hashlib.sha256(r.content).hexdigest() == expected_sha256
    except Exception:
        return False


async def verify_signature(
    manifest: dict[str, Any],
    bundle_sha256: str,
    settings: Settings | None = None,
) -> VerifyResult:
    """Verify the manifest's signature block. Returns VerifyResult; never raises."""
    settings = settings or get_settings()
    sig_block = manifest.get("signature")
    if not isinstance(sig_block, dict):
        return VerifyResult(valid=False, error="manifest has no signature block")

    if sig_block.get("algorithm") != "ES256":
        return VerifyResult(valid=False, error=f"unsupported algorithm: {sig_block.get('algorithm')}")

    signer = sig_block.get("signer") or {}
    passport = signer.get("passport")
    if not passport:
        return VerifyResult(valid=False, error="signature.signer.passport missing")

    sig_b64 = sig_block.get("signature")
    if not isinstance(sig_b64, str):
        return VerifyResult(valid=False, error="signature.signature missing")

    # Recompute canonical + digest.
    manifest_sans_sig = {k: v for k, v in manifest.items() if k != "signature"}
    canonical = canonicalize(manifest_sans_sig)
    message = (canonical + bundle_sha256).encode("utf-8")
    digest_hex = "sha256:" + hashlib.sha256(message).hexdigest()

    claimed_digest = sig_block.get("signed_digest")
    if claimed_digest != digest_hex:
        return VerifyResult(
            valid=False,
            error=f"digest mismatch: claimed={claimed_digest} actual={digest_hex}",
        )

    # Fetch + locate signer's key by passport claim. v1 keys the JWKS lookup
    # by 'sub' or 'kid'; ADR-053 leaves this open — the registry currently
    # accepts ANY ES256 key in the Eternitas JWKS that verifies the signature.
    # When Eternitas keys by-passport, swap this for an explicit kid lookup.
    try:
        jwks = await _fetch_jwks(settings.eternitas_jwks_url)
    except httpx.HTTPError as e:
        return VerifyResult(valid=False, error=f"failed to fetch Eternitas JWKS: {e}")

    raw_sig = base64.b64decode(sig_b64)
    if len(raw_sig) != 64:
        return VerifyResult(valid=False, error=f"signature has wrong length: {len(raw_sig)}")
    r_int = int.from_bytes(raw_sig[:32], "big")
    s_int = int.from_bytes(raw_sig[32:], "big")
    der_sig = encode_dss_signature(r_int, s_int)

    for key_dict in jwks.get("keys", []):
        if key_dict.get("alg") not in (None, "ES256"):
            continue
        try:
            pem = jwk.construct(key_dict).to_pem()
            from cryptography.hazmat.primitives.serialization import load_pem_public_key
            pub = load_pem_public_key(pem)
            if not isinstance(pub, ec.EllipticCurvePublicKey):
                continue
            pub.verify(der_sig, message, ec.ECDSA(hashes.SHA256()))
            return VerifyResult(
                valid=True,
                signer_passport=passport,
                signer_integrity_band=signer.get("integrity_band"),
                signer_clearance_level=signer.get("clearance_level"),
            )
        except (InvalidSignature, ValueError):
            continue

    return VerifyResult(valid=False, error="no matching key in Eternitas JWKS verified the signature")
