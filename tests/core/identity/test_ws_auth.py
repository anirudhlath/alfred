"""Tests for the shared WebSocket cookie-auth helper."""

from unittest.mock import AsyncMock, MagicMock

from core.identity.ws_auth import authenticate_ws_cookie
from shared.streams import AUTH_SESSION_PREFIX


def _ws_with_cookie(cookie_header: str) -> MagicMock:
    ws = MagicMock()
    ws.headers = {"cookie": cookie_header}
    return ws


async def test_valid_session_authenticates() -> None:
    redis = AsyncMock()
    redis.hgetall = AsyncMock(return_value={b"authenticated": b"1"})
    ws = _ws_with_cookie("alfred_auth=abc123; other=x")
    assert await authenticate_ws_cookie(ws, redis) is True
    redis.hgetall.assert_awaited_once_with(f"{AUTH_SESSION_PREFIX}abc123")


async def test_missing_cookie_rejected() -> None:
    redis = AsyncMock()
    ws = _ws_with_cookie("other=x")
    assert await authenticate_ws_cookie(ws, redis) is False
    redis.hgetall.assert_not_awaited()


async def test_unauthenticated_session_rejected() -> None:
    redis = AsyncMock()
    redis.hgetall = AsyncMock(return_value={})
    ws = _ws_with_cookie("alfred_auth=abc123")
    assert await authenticate_ws_cookie(ws, redis) is False


async def test_empty_cookie_value_rejected() -> None:
    redis = AsyncMock()
    ws = _ws_with_cookie("alfred_auth=")
    assert await authenticate_ws_cookie(ws, redis) is False
    redis.hgetall.assert_not_awaited()
