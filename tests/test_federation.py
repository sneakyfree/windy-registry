"""test_federation.py — WD-34 acceptance tests for federation stubs."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client() -> TestClient:
    from windy_registry.main import create_app
    return TestClient(create_app())


def test_peers_returns_empty_list() -> None:
    r = _client().get("/api/v1/federation/peers")
    assert r.status_code == 200
    assert r.json() == {"peers": []}


def test_cross_registry_drop_returns_501_with_pointer() -> None:
    r = _client().get("/api/v1/federation/drops/example.com/foo")
    assert r.status_code == 501
    body = r.json()
    assert body["detail"]["error"] == "federation_not_implemented_v1"
    assert "see_also" in body["detail"]
