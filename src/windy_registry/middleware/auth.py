"""auth.py — dual-JWKS Bearer authentication.

Validates Bearer tokens against either:
  * Pro account-server JWKS (RS256 — human users; per `feedback_jwks_split_brain`,
    use `account.windyword.ai`, NOT the legacy `api.*` host which still serves
    a stale RS256 keypair)
  * Eternitas JWKS (ES256 — agents)

Try Pro first because RS256 verification is cheaper, fall back to Eternitas on
`kid` mismatch. JWKS responses cache for 5 minutes per `feedback_jwks_split_brain`.

Strand: WD-15. Field shapes (passport, integrity_band, clearance_level) align
with `windy-connect/docs/bundle-spec-v1.md` per AUDIT_2026-05-21.md Bucket 3.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwk, jwt
from jose.exceptions import JWTError

from ..config import Settings, get_settings

# 5-minute cache TTL — matches windy-chat pattern from earlier
# `feedback_jwks_split_brain` memory.
_JWKS_TTL_SECONDS = 300

_jwks_cache: dict[str, tuple[float, dict[str, Any]]] = {}


@dataclass(frozen=True)
class AuthUser:
    """Resolved caller identity. `passport` is non-null for agent tokens."""

    subject: str
    issuer: str | None
    tier: str  # "human" | "agent"
    passport: str | None
    integrity_band: str | None
    clearance_level: str | None
    raw_claims: dict[str, Any]


# HTTPBearer auto-handles 403 on missing tokens, but we want 401 + reuse for
# the optional dep, so we wrap manually with auto_error=False.
_bearer = HTTPBearer(auto_error=False)


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


def _find_key(jwks: dict[str, Any], kid: str | None) -> dict[str, Any] | None:
    for key in jwks.get("keys", []):
        if kid is None or key.get("kid") == kid:
            return key
    return None


async def _try_verify(token: str, jwks_url: str, algorithms: list[str]) -> dict[str, Any] | None:
    """Returns claims if verification succeeds; None if the kid doesn't match
    this JWKS (so the caller can try the next one).

    G19: if the kid isn't in the cached JWKS, refresh the cache ONCE and
    retry before giving up — handles silent key rotation upstream without
    forcing every request through a stale cache.
    """
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    jwks = await _fetch_jwks(jwks_url)
    key_dict = _find_key(jwks, kid)
    if key_dict is None:
        _jwks_cache.pop(jwks_url, None)  # G19: bust + retry
        jwks = await _fetch_jwks(jwks_url)
        key_dict = _find_key(jwks, kid)
        if key_dict is None:
            return None
    try:
        return jwt.decode(
            token,
            jwk.construct(key_dict).to_pem().decode(),
            algorithms=algorithms,
            options={"verify_aud": False},
        )
    except JWTError:
        return None


async def _resolve_user(
    credentials: HTTPAuthorizationCredentials | None,
    settings: Settings,
) -> AuthUser | None:
    if credentials is None:
        return None
    token = credentials.credentials

    # Pro first (RS256 = faster); fall back to Eternitas (ES256).
    claims = await _try_verify(token, settings.pro_jwks_url, ["RS256"])
    if claims is not None:
        return AuthUser(
            subject=str(claims.get("sub", "")),
            issuer=str(claims.get("iss", "")) or None,
            tier="human",
            passport=None,
            integrity_band=None,
            clearance_level=None,
            raw_claims=claims,
        )

    claims = await _try_verify(token, settings.eternitas_jwks_url, ["ES256"])
    if claims is not None:
        return AuthUser(
            subject=str(claims.get("sub", "")),
            issuer=str(claims.get("iss", "")) or None,
            tier="agent",
            passport=str(claims.get("passport", "")) or None,
            integrity_band=claims.get("integrity_band"),
            clearance_level=claims.get("clearance_level"),
            raw_claims=claims,
        )

    return None


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> AuthUser:
    """Required auth — 401 on missing/invalid token."""
    user = await _resolve_user(credentials, settings)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.user = user
    return user


async def get_current_user_optional(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> AuthUser | None:
    """Optional auth — None if missing/invalid. For endpoints that personalize
    (e.g., trending feed) but don't require login."""
    user = await _resolve_user(credentials, settings)
    if user is not None:
        request.state.user = user
    return user


def reset_jwks_cache_for_tests() -> None:
    """Test helper — clear the JWKS cache between tests."""
    _jwks_cache.clear()
