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
    # Default: empty stream so _last_id resolves subscriptions to "0-0" deterministically.
    if not isinstance(mock_redis.xrevrange, AsyncMock):
        mock_redis.xrevrange = AsyncMock(return_value=[])
    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = mock_redis
    client = TestClient(app)
    if authed:
        client.cookies.set("alfred_auth", _SESSION)
    return client


def test_telemetry_ws_rejects_unauthenticated() -> None:
    client = _make_client(AsyncMock(), authed=False)
    # The gate now accepts before closing with 4001 so the browser receives a real
    # close frame (close-before-accept surfaces as an HTTP 403 with no code, causing
    # the client to reconnect forever). The connect succeeds; the close is observed
    # on the first receive.
    with client.websocket_connect("/ws/telemetry") as ws, pytest.raises(WebSocketDisconnect) as exc:
        ws.receive_text()
    assert exc.value.code == 4001


def test_telemetry_ws_subscribe_resolves_concrete_start_id() -> None:
    """Subscription pins the stream's last-generated id (not the literal '$'), so
    entries landing between blocking reads are not skipped."""
    mock_redis = AsyncMock()
    mock_redis.xrevrange = AsyncMock(return_value=[(b"7-0", {b"event": b"{}"})])
    seen_start: list[str] = []

    async def _xread(streams: dict[str, str], **kwargs: Any) -> Any:
        seen_start.append(streams["alfred:events"])
        await asyncio.Event().wait()  # block after capturing the requested position

    mock_redis.xread = AsyncMock(side_effect=_xread)
    client = _make_client(mock_redis)
    with client.websocket_connect("/ws/telemetry") as ws:
        ws.send_text(json.dumps({"type": "subscribe", "streams": ["events"]}))
        ws.receive_json()  # subscribed ack
        # Give the pump a moment to issue its first xread with the resolved id.
        for _ in range(20):
            if seen_start:
                break
            ws.send_text(json.dumps({"type": "subscribe", "streams": []}))
            ws.receive_json()
    assert seen_start and seen_start[0] == "7-0"


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
