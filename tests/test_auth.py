"""test_auth.py — WD-15 acceptance tests for dual-JWKS auth middleware.

Mocks the Pro + Eternitas JWKS endpoints via httpx.MockTransport so we don't
need real upstream servers. Verifies:
  - Pro RS256 JWTs accepted (tier="human")
  - Eternitas ES256 JWTs accepted (tier="agent", populates passport/band/level)
  - Invalid signatures rejected (401)
  - Expired tokens rejected (401)
  - Missing Bearer header rejected on required dep (401)
  - Optional dep returns None on missing/invalid token (no exception)
  - JWKS cache hit on second request within TTL (no duplicate fetch)
  - Pro tried first; Eternitas fallback only on kid mismatch
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from windy_registry.config import get_settings
from windy_registry.middleware import auth as auth_module
from windy_registry.middleware.auth import (
    AuthUser,
    get_current_user,
    get_current_user_optional,
    reset_jwks_cache_for_tests,
)

# -------- key generation + JWKS helpers --------

def _rsa_keypair_and_jwks(kid: str = "pro-kid-1") -> tuple[bytes, dict]:
    """Generate an RS256 keypair and return (private_pem, jwks dict)."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub_numbers = priv.public_key().public_numbers()
    import base64
    def b64(i: int) -> str:
        b = i.to_bytes((i.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    jwks = {
        "keys": [{
            "kty": "RSA",
            "kid": kid,
            "alg": "RS256",
            "use": "sig",
            "n": b64(pub_numbers.n),
            "e": b64(pub_numbers.e),
        }]
    }
    return private_pem, jwks


def _ec_keypair_and_jwks(kid: str = "et-kid-1") -> tuple[bytes, dict]:
    """Generate an ES256 keypair and return (private_pem, jwks dict)."""
    priv = ec.generate_private_key(ec.SECP256R1())
    private_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    nums = priv.public_key().public_numbers()
    import base64
    def b64(i: int) -> str:
        b = i.to_bytes(32, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    jwks = {
        "keys": [{
            "kty": "EC",
            "kid": kid,
            "alg": "ES256",
            "use": "sig",
            "crv": "P-256",
            "x": b64(nums.x),
            "y": b64(nums.y),
        }]
    }
    return private_pem, jwks


# -------- fixtures --------

@pytest.fixture(autouse=True)
def _reset_state():
    reset_jwks_cache_for_tests()
    get_settings.cache_clear()
    yield
    reset_jwks_cache_for_tests()
    get_settings.cache_clear()


@pytest.fixture
def keys():
    pro_priv, pro_jwks = _rsa_keypair_and_jwks(kid="pro-kid-1")
    et_priv, et_jwks = _ec_keypair_and_jwks(kid="et-kid-1")
    return {
        "pro_priv": pro_priv,
        "pro_jwks": pro_jwks,
        "et_priv": et_priv,
        "et_jwks": et_jwks,
    }


@pytest.fixture
def patched_httpx(monkeypatch, keys):
    """Replace httpx.AsyncClient with one that serves our mock JWKS."""
    fetch_count = {"pro": 0, "eternitas": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "windyword" in url or "account" in url:
            fetch_count["pro"] += 1
            return httpx.Response(200, json=keys["pro_jwks"])
        if "eternitas" in url:
            fetch_count["eternitas"] += 1
            return httpx.Response(200, json=keys["et_jwks"])
        return httpx.Response(404)

    real_async_client = httpx.AsyncClient
    def make_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real_async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(auth_module.httpx, "AsyncClient", make_client)
    return fetch_count


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()

    @app.get("/protected")
    async def protected(user: AuthUser = pytest.importorskip("fastapi").Depends(get_current_user)):
        return {"sub": user.subject, "tier": user.tier, "passport": user.passport}

    @app.get("/maybe")
    async def maybe(user: AuthUser | None = pytest.importorskip("fastapi").Depends(get_current_user_optional)):
        return {"signed_in": user is not None}

    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# -------- tests --------

def test_pro_rs256_jwt_accepted(client, patched_httpx, keys):
    token = jwt.encode(
        {"sub": "wid_abc123", "exp": int(time.time()) + 3600, "iss": "https://account.windyword.ai"},
        keys["pro_priv"].decode(),
        algorithm="RS256",
        headers={"kid": "pro-kid-1"},
    )
    r = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["sub"] == "wid_abc123"
    assert body["tier"] == "human"
    assert body["passport"] is None


def test_pro_rs256_jwt_with_eternitas_passport_populates_passport(client, patched_httpx, keys):
    # A Pro token issued to an agent (or an agent-operating human) carries the
    # `eternitas_passport` claim; the middleware must surface it as `passport`
    # so the drop ownership gates apply. Ignoring it was the root of the
    # publish-as-anyone / withdraw-anyone bypass.
    token = jwt.encode(
        {
            "sub": "wid_agent",
            "exp": int(time.time()) + 3600,
            "iss": "https://account.windyword.ai",
            "eternitas_passport": "ET26-TEST-0001",
        },
        keys["pro_priv"].decode(),
        algorithm="RS256",
        headers={"kid": "pro-kid-1"},
    )
    r = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "human"
    assert body["passport"] == "ET26-TEST-0001"


def test_eternitas_es256_jwt_accepted(client, patched_httpx, keys):
    token = jwt.encode(
        {
            "sub": "ET26-TEST-0001",
            "exp": int(time.time()) + 3600,
            "iss": "https://api.eternitas.ai",
            "passport": "ET26-TEST-0001",
            "integrity_band": "fair",
            "clearance_level": "verified",
        },
        keys["et_priv"].decode(),
        algorithm="ES256",
        headers={"kid": "et-kid-1"},
    )
    r = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "agent"
    assert body["passport"] == "ET26-TEST-0001"


def test_invalid_signature_rejected(client, patched_httpx, keys):
    # Sign with a DIFFERENT key (not the one in JWKS).
    other_priv, _ = _rsa_keypair_and_jwks(kid="pro-kid-1")
    token = jwt.encode(
        {"sub": "x", "exp": int(time.time()) + 3600},
        other_priv.decode(),
        algorithm="RS256",
        headers={"kid": "pro-kid-1"},
    )
    r = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_expired_token_rejected(client, patched_httpx, keys):
    token = jwt.encode(
        {"sub": "x", "exp": int(time.time()) - 60},
        keys["pro_priv"].decode(),
        algorithm="RS256",
        headers={"kid": "pro-kid-1"},
    )
    r = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_missing_auth_header_rejected_on_required(client, patched_httpx):
    r = client.get("/protected")
    assert r.status_code == 401


def test_optional_dep_returns_unauthenticated_on_missing(client, patched_httpx):
    r = client.get("/maybe")
    assert r.status_code == 200
    assert r.json() == {"signed_in": False}


def test_optional_dep_resolves_when_token_valid(client, patched_httpx, keys):
    token = jwt.encode(
        {"sub": "wid_x", "exp": int(time.time()) + 3600},
        keys["pro_priv"].decode(),
        algorithm="RS256",
        headers={"kid": "pro-kid-1"},
    )
    r = client.get("/maybe", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == {"signed_in": True}


def test_jwks_cache_hit_on_second_request(client, patched_httpx, keys):
    token = jwt.encode(
        {"sub": "x", "exp": int(time.time()) + 3600},
        keys["pro_priv"].decode(),
        algorithm="RS256",
        headers={"kid": "pro-kid-1"},
    )
    headers = {"Authorization": f"Bearer {token}"}
    client.get("/protected", headers=headers)
    client.get("/protected", headers=headers)
    # Pro JWKS fetched exactly once (cache hit on second request).
    assert patched_httpx["pro"] == 1


def test_unknown_kid_falls_through_to_eternitas_then_fails(client, patched_httpx, keys):
    """A token with an unknown kid against both JWKS yields 401."""
    other_priv, _ = _ec_keypair_and_jwks(kid="unknown-kid")
    token = jwt.encode(
        {"sub": "x", "exp": int(time.time()) + 3600},
        other_priv.decode(),
        algorithm="ES256",
        headers={"kid": "unknown-kid"},
    )
    r = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
