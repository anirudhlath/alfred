"""POST /api/actions/{request_id}/confirm — auth-gated confirmation endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from bus.schemas.events import ActionRequest
from core.channels.web_server import create_app
from shared.streams import ACTIONS_STREAM


def _pending_action() -> ActionRequest:
    return ActionRequest(
        source="conscious-engine",
        target_service="home-service",
        tool_name="home.unlock_door",
        parameters={"entity_id": "lock.front_door"},
    )


def test_confirm_happy_path(web_client: TestClient) -> None:
    action = _pending_action()
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.get = AsyncMock(return_value=action.model_dump_json().encode())

    resp = web_client.post(f"/api/actions/{action.request_id}/confirm")

    assert resp.status_code == 200
    assert resp.json() == {"status": "confirmed"}
    stream, fields = redis.xadd.call_args[0]
    assert stream == ACTIONS_STREAM
    republished = ActionRequest.model_validate_json(fields["event"])
    assert republished.confirmed is True
    assert republished.request_id == action.request_id
    redis.delete.assert_awaited_once_with(f"alfred:pending_actions:{action.request_id}")


def test_confirm_missing_or_expired_returns_404(web_client: TestClient) -> None:
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.get = AsyncMock(return_value=None)

    resp = web_client.post("/api/actions/ghost-id/confirm")

    assert resp.status_code == 404
    redis.xadd.assert_not_called()


def test_confirm_requires_auth() -> None:
    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = AsyncMock()
    client = TestClient(app)  # no auth cookie

    resp = client.post("/api/actions/any-id/confirm")

    assert resp.status_code == 401
