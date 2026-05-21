"""test_version.py — WD-12 acceptance tests for /version (MF1 contract)."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from windy_registry.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_version_returns_mf1_contract_shape(client: TestClient) -> None:
    r = client.get("/version")
    assert r.status_code == 200
    body = r.json()
    # MF1 contract — every field present.
    assert set(body.keys()) == {
        "service",
        "version",
        "commit_sha",
        "commit_sha_short",
        "build_timestamp",
        "started_at",
        "environment",
    }


def test_version_service_name(client: TestClient) -> None:
    assert client.get("/version").json()["service"] == "windy-registry"


def test_version_no_auth_required(client: TestClient) -> None:
    r = client.get("/version")
    assert r.status_code == 200, "MF1 says /version must answer without auth"


def test_version_unset_commit_sha_is_null(client: TestClient) -> None:
    body = client.get("/version").json()
    # In tests (no COMMIT_SHA env), commit_sha must be null (not "", not "0").
    assert body["commit_sha"] is None
    assert body["commit_sha_short"] is None


def test_version_started_at_is_rfc3339(client: TestClient) -> None:
    body = client.get("/version").json()
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", body["started_at"])


def test_version_two_calls_same_started_at(client: TestClient) -> None:
    """started_at is captured at module import, not per-request."""
    a = client.get("/version").json()["started_at"]
    b = client.get("/version").json()["started_at"]
    assert a == b


def test_version_in_openapi_spec_under_health_tag(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    op = spec["paths"]["/version"]["get"]
    assert "health" in op["tags"]


def test_health_endpoint_returns_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_full_returns_structured_stub(client: TestClient) -> None:
    r = client.get("/health/full")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "database" in body
    assert "r2_bucket" in body
    assert "jwks" in body
    assert set(body["jwks"]) == {"pro", "eternitas"}
