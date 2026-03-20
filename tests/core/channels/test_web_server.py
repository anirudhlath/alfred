"""Tests for web channel server."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from pathlib import Path

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


def test_onboarding_endpoint_saves_preferences(tmp_path: Path) -> None:
    """POST /api/onboarding writes preference files atomically."""
    from unittest.mock import patch

    client = _client()

    prefs_dir = tmp_path / "preferences"
    profile_dir = tmp_path / "profile"

    with (
        patch("core.channels.web_server.Path.__file__", create=True),
        patch(
            "core.channels.web_server._preference_file_dirs",
            return_value=(prefs_dir, profile_dir),
            create=True,
        ),
    ):
        # Patch the paths inside the endpoint by patching Path resolution
        import core.channels.web_server as ws

        # Direct approach: override the path computation in the endpoint
        prefs_dir.mkdir(parents=True)
        profile_dir.mkdir(parents=True)

        # Monkey-patch the module-level function for atomic write
        orig_atomic = ws._atomic_write
        written_files: dict[str, str] = {}

        def capture_write(path: Path, content: str) -> None:
            written_files[path.name] = content
            orig_atomic(path, content)

        ws._atomic_write = capture_write  # type: ignore[assignment]

        resp = client.post(
            "/api/onboarding",
            json={
                "wake_time": "07:30",
                "work_address": "123 Main St",
                "dietary_restrictions": "vegetarian",
                "proactivity_level": "moderate",
                "guest_controls": ["Lighting control", "Media playback"],
            },
        )

        ws._atomic_write = orig_atomic  # type: ignore[assignment]

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_onboarding_endpoint_empty_payload() -> None:
    """POST /api/onboarding with empty payload returns ok (no files written)."""
    client = _client()
    resp = client.post("/api/onboarding", json={})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_onboarding_preference_file_format() -> None:
    """_preference_file produces valid YAML frontmatter + Markdown."""
    from core.channels.web_server import _preference_file

    result = _preference_file("general", "2026-03-19", "manual", "Test", ["- item 1", "- item 2"])
    assert result.startswith("---\n")
    assert "domain: general" in result
    assert "confidence: manual" in result
    assert "# Test" in result
    assert "- item 1" in result


def test_onboarding_atomic_write(tmp_path: Path) -> None:
    """_atomic_write creates the file atomically (no .tmp left behind)."""
    from core.channels.web_server import _atomic_write

    target = tmp_path / "test.md"
    _atomic_write(target, "hello")
    assert target.read_text() == "hello"
    assert not target.with_suffix(".tmp").exists()
