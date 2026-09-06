"""POST /api/actions/{request_id}/confirm — auth-gated confirmation endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from bus.schemas.events import ActionRequest
from core.channels.web_server import create_app
from shared.streams import ACTIONS_STREAM

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


async def _aiter(items: list[str]) -> AsyncIterator[str]:
    for item in items:
        yield item


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
    redis.getdel = AsyncMock(return_value=action.model_dump_json().encode())

    resp = web_client.post(f"/api/actions/{action.request_id}/confirm")

    assert resp.status_code == 200
    assert resp.json() == {"status": "confirmed"}
    stream, fields = redis.xadd.call_args[0]
    assert stream == ACTIONS_STREAM
    republished = ActionRequest.model_validate_json(fields["event"])
    assert republished.confirmed is True
    assert republished.request_id == action.request_id
    redis.getdel.assert_awaited_once_with(f"alfred:pending_actions:{action.request_id}")


def test_confirm_missing_or_expired_returns_404(web_client: TestClient) -> None:
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.getdel = AsyncMock(return_value=None)

    resp = web_client.post("/api/actions/ghost-id/confirm")

    assert resp.status_code == 404
    redis.xadd.assert_not_called()


def test_confirm_requires_auth() -> None:
    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = AsyncMock()
    client = TestClient(app)  # no auth cookie

    resp = client.post("/api/actions/any-id/confirm")

    assert resp.status_code == 401


def test_get_pending_action_returns_payload(web_client: TestClient) -> None:
    action = _pending_action().model_copy(update={"reason": "You asked me to."})
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.get = AsyncMock(return_value=action.model_dump_json().encode())
    redis.ttl = AsyncMock(return_value=42)

    resp = web_client.get(f"/api/actions/{action.request_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"] == action.request_id
    assert body["tool_name"] == "home.unlock_door"
    assert body["reason"] == "You asked me to."
    assert body["ttl_seconds"] == 42
    assert "expires_at" in body
    redis.getdel.assert_not_called()


def test_get_pending_action_missing_returns_404(web_client: TestClient) -> None:
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.get = AsyncMock(return_value=None)

    resp = web_client.get("/api/actions/ghost-id")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Pending action not found or expired"


def test_list_pending_actions(web_client: TestClient) -> None:
    action = _pending_action()
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.scan_iter = MagicMock(
        return_value=_aiter([f"alfred:pending_actions:{action.request_id}"])
    )
    redis.get = AsyncMock(return_value=action.model_dump_json().encode())
    redis.ttl = AsyncMock(return_value=250)

    resp = web_client.get("/api/actions/pending")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["actions"]) == 1
    assert body["actions"][0]["request_id"] == action.request_id
    assert body["actions"][0]["ttl_seconds"] == 250


def test_pending_reads_require_auth() -> None:
    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = AsyncMock()
    client = TestClient(app)  # no auth cookie

    assert client.get("/api/actions/pending").status_code == 401
    assert client.get("/api/actions/any-id").status_code == 401
