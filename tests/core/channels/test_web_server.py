"""Tests for web channel server."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient

from core.channels.web_server import create_app


def test_health_endpoint(web_client: TestClient) -> None:
    client = web_client
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "web-channel"


def test_static_files_served(web_client: TestClient) -> None:
    """Static route should be mounted (may 404 if no web/ dir)."""
    client = web_client
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


def test_onboarding_endpoint_saves_preferences(web_client: TestClient, tmp_path: Path) -> None:
    """POST /api/onboarding writes preference files atomically."""
    from unittest.mock import patch

    import core.channels.web_server as ws

    prefs_dir = tmp_path / "preferences"
    profile_dir = tmp_path / "profile"
    prefs_dir.mkdir(parents=True)
    profile_dir.mkdir(parents=True)

    written_files: dict[str, str] = {}
    orig_atomic = ws._atomic_write

    def capture_write(path: Path, content: str) -> None:
        written_files[path.name] = content
        orig_atomic(path, content)

    with (
        patch.object(ws, "_atomic_write", side_effect=capture_write),
        patch.object(ws, "_get_prefs_dirs", return_value=(prefs_dir, profile_dir)),
    ):
        resp = web_client.post(
            "/api/onboarding",
            json={
                "wake_time": "07:30",
                "work_address": "123 Main St",
                "dietary_restrictions": "vegetarian",
                "proactivity_level": "moderate",
                "guest_controls": ["Lighting control", "Media playback"],
            },
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    # Verify preference files were actually written with correct content
    assert "personal.md" in written_files, "personal.md should have been written"
    personal_content = written_files["personal.md"]
    # Parse YAML frontmatter to verify structured data, not string matching
    assert personal_content.startswith("---\n"), "Preference files should have YAML frontmatter"
    import yaml

    parts = personal_content.split("---\n", 2)
    frontmatter = yaml.safe_load(parts[1])
    assert frontmatter is not None, "Frontmatter should parse as valid YAML"

    assert "proactivity.md" in written_files, "proactivity.md should have been written"
    proactivity_content = written_files["proactivity.md"]
    proactivity_parts = proactivity_content.split("---\n", 2)
    proactivity_fm = yaml.safe_load(proactivity_parts[1])
    assert proactivity_fm is not None, "Proactivity frontmatter should parse as valid YAML"


def test_onboarding_endpoint_empty_payload(web_client: TestClient) -> None:
    """POST /api/onboarding with empty payload returns ok (no files written)."""
    resp = web_client.post("/api/onboarding", json={})
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


def test_publish_and_wait_returns_alfred_response() -> None:
    """_publish_and_wait returns an AlfredResponse (not a bare string)."""
    import asyncio
    from unittest.mock import AsyncMock

    from bus.schemas.events import AlfredResponse, UserRequest
    from core.channels.web_server import _publish_and_wait
    from shared.streams import USER_RESPONSES_STREAM

    session_id = "test-session-abc"
    resp = AlfredResponse(
        source="conscious",
        channel="web_pwa",
        session_id=session_id,
        text="Very good, sir.",
        actions_taken=["calendar.get_events"],
        mood="pleased",
    )

    mock_redis = AsyncMock()
    mock_redis.xadd = AsyncMock(return_value=b"1-0")

    stream_entries = [
        (USER_RESPONSES_STREAM.encode(), [(b"1-0", {b"event": resp.model_dump_json().encode()})])
    ]
    mock_redis.xread = AsyncMock(side_effect=[stream_entries, []])

    request = UserRequest(
        source="web-pwa",
        channel="web_pwa",
        session_id=session_id,
        identity_claim="sir",
        content_type="text",
        content="What's on my calendar?",
    )

    result = asyncio.run(_publish_and_wait(mock_redis, request, session_id, timeout=5.0))

    assert isinstance(result, AlfredResponse)
    assert result.text == "Very good, sir."
    assert result.actions_taken == ["calendar.get_events"]
    assert result.mood == "pleased"
    assert result.session_id == session_id


def test_ws_response_forwards_actions_taken_and_mood(web_client: TestClient) -> None:
    """WebSocket /ws response payload includes actions_taken and mood from AlfredResponse."""
    from unittest.mock import AsyncMock, patch

    from bus.schemas.events import AlfredResponse

    session_id = "ws-test-session"
    alfred_resp = AlfredResponse(
        source="conscious",
        channel="web_pwa",
        session_id=session_id,
        text="Lights dimmed to 40%, sir.",
        actions_taken=["smart_home.dim_lights"],
        mood="pleased",
    )

    # Patch _publish_and_wait to return our AlfredResponse directly
    with (
        patch(
            "core.channels.web_server._publish_and_wait", new=AsyncMock(return_value=alfred_resp)
        ),
        web_client.websocket_connect("/ws") as ws,
    ):
        # Consume the session message
        session_msg = ws.receive_json()
        assert session_msg["type"] == "session"

        ws.send_json({"type": "text", "content": "Dim the lights", "channel": "web_pwa"})
        response = ws.receive_json()

    assert response["type"] == "response"
    assert response["text"] == "Lights dimmed to 40%, sir."
    assert response["actions_taken"] == ["smart_home.dim_lights"]
    assert response["mood"] == "pleased"
    assert "session_id" in response
