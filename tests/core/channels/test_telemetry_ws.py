"""Telemetry WebSocket: auth gate, subscribe ack, entry fan-out."""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from core.channels.web_server import create_app
from shared.streams import AUTH_SESSION_PREFIX

_SESSION = "telemetry-test-session"


def _make_client(mock_redis: AsyncMock, *, authed: bool = True) -> TestClient:
    async def _fake_hgetall(key: str) -> dict[bytes, bytes]:
        if key == f"{AUTH_SESSION_PREFIX}{_SESSION}":
            return {b"authenticated": b"1"}
        return {}

    mock_redis.hgetall = AsyncMock(side_effect=_fake_hgetall)
    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = mock_redis
    client = TestClient(app)
    if authed:
        client.cookies.set("alfred_auth", _SESSION)
    return client


def test_telemetry_ws_rejects_unauthenticated() -> None:
    client = _make_client(AsyncMock(), authed=False)
    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect("/ws/telemetry"):
        pass
    assert exc.value.code == 4001


def test_telemetry_ws_subscribe_and_receive_entry() -> None:
    mock_redis = AsyncMock()
    batches: list[Any] = [
        [(b"alfred:events", [(b"1-0", {b"event": b'{"event_type": "state_changed"}'})])],
    ]

    async def _xread(*args: Any, **kwargs: Any) -> Any:
        if batches:
            return batches.pop(0)
        await asyncio.Event().wait()  # block forever after first batch

    mock_redis.xread = AsyncMock(side_effect=_xread)
    client = _make_client(mock_redis)
    with client.websocket_connect("/ws/telemetry") as ws:
        ws.send_text(json.dumps({"type": "subscribe", "streams": ["events", "bogus"]}))
        ack = ws.receive_json()
        assert ack == {"type": "subscribed", "streams": ["events"]}
        entry = ws.receive_json()
        assert entry == {
            "type": "entry",
            "stream": "events",
            "id": "1-0",
            "event": {"event_type": "state_changed"},
        }


def test_telemetry_ws_unsubscribe_ack() -> None:
    """Unsubscribing a stream removes it from the subscribed ack."""
    mock_redis = AsyncMock()

    async def _xread_block(*args: Any, **kwargs: Any) -> Any:
        await asyncio.Event().wait()  # block forever — pump never produces entries

    mock_redis.xread = AsyncMock(side_effect=_xread_block)
    client = _make_client(mock_redis)
    with client.websocket_connect("/ws/telemetry") as ws:
        ws.send_text(json.dumps({"type": "subscribe", "streams": ["events", "actions"]}))
        ack = ws.receive_json()
        assert set(ack["streams"]) == {"events", "actions"}

        ws.send_text(json.dumps({"type": "unsubscribe", "streams": ["actions"]}))
        ack2 = ws.receive_json()
        assert ack2 == {"type": "subscribed", "streams": ["events"]}


def test_telemetry_ws_invalid_json() -> None:
    """Non-JSON text produces an error frame; the connection stays open."""
    mock_redis = AsyncMock()

    async def _xread_block(*args: Any, **kwargs: Any) -> Any:
        await asyncio.Event().wait()

    mock_redis.xread = AsyncMock(side_effect=_xread_block)
    client = _make_client(mock_redis)
    with client.websocket_connect("/ws/telemetry") as ws:
        ws.send_text("not json")
        error = ws.receive_json()
        assert error == {"type": "error", "message": "invalid JSON"}


def test_telemetry_ws_redis_error_frame() -> None:
    """An xread failure produces a redis_error status frame; the pump stays alive."""
    mock_redis = AsyncMock()
    calls: list[int] = [0]

    async def _xread_failing(*args: Any, **kwargs: Any) -> Any:
        calls[0] += 1
        if calls[0] == 1:
            raise Exception("down")  # generic Exception avoids outer ConnectionError catch
        await asyncio.Event().wait()  # block after the error so pump parks cleanly

    mock_redis.xread = AsyncMock(side_effect=_xread_failing)
    client = _make_client(mock_redis)
    # Subscribe first so the pump runs and hits xread; patch sleep to avoid 1s delay
    with (
        client.websocket_connect("/ws/telemetry") as ws,
        patch("core.channels.telemetry_ws.asyncio.sleep", new=AsyncMock()),
    ):
        ws.send_text(json.dumps({"type": "subscribe", "streams": ["events"]}))
        ack = ws.receive_json()
        assert ack["type"] == "subscribed"
        status = ws.receive_json()
        assert status == {"type": "status", "detail": "redis_error"}


def test_telemetry_ws_unknown_stream_subscribe() -> None:
    """Subscribing only unknown streams yields an empty subscribed ack."""
    mock_redis = AsyncMock()

    async def _xread_block(*args: Any, **kwargs: Any) -> Any:
        await asyncio.Event().wait()

    mock_redis.xread = AsyncMock(side_effect=_xread_block)
    client = _make_client(mock_redis)
    with client.websocket_connect("/ws/telemetry") as ws:
        ws.send_text(json.dumps({"type": "subscribe", "streams": ["bogus"]}))
        ack = ws.receive_json()
        assert ack == {"type": "subscribed", "streams": []}
