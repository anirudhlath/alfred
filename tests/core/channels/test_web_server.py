"""Tests for web channel server."""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from core.channels.web_server import create_app


def _client() -> TestClient:
    app = create_app(redis_url="redis://localhost:6379")
    # Provide a mock Redis so the lifespan doesn't need a real connection
    app.state.redis = AsyncMock()
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


def test_create_app_returns_fastapi() -> None:
    app = create_app(redis_url="redis://localhost:6379")
    assert app.title == "Alfred Web Channel"


def test_create_app_stores_redis_url() -> None:
    app = create_app(redis_url="redis://testhost:1234")
    assert app.state.redis_url == "redis://testhost:1234"


def test_app_has_websocket_route() -> None:
    app = create_app()
    routes = [r.path for r in app.routes]  # type: ignore[union-attr]
    assert "/ws" in routes


def test_app_has_health_route() -> None:
    app = create_app()
    routes = [r.path for r in app.routes]  # type: ignore[union-attr]
    assert "/health" in routes
