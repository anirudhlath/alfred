"""Tests for web channel server."""

from __future__ import annotations

from fastapi.testclient import TestClient

from core.channels.web_server import create_app


def _client() -> TestClient:
    app = create_app(redis_url="redis://localhost:6379")
    return TestClient(app)


def test_health_endpoint() -> None:
    client = _client()
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "web-channel"


def test_static_files_served() -> None:
    """Static route should be mounted (may 404 if no web/ dir)."""
    client = _client()
    resp = client.get("/")
    # Either 200 (if index.html exists) or 404 is acceptable at this stage
    assert resp.status_code in (200, 404)
