"""Tests for SessionManager."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from core.conscious.session import SessionManager


@pytest.fixture
def mock_redis() -> AsyncMock:
    return AsyncMock()


@pytest.mark.asyncio
async def test_get_or_create_session_new(mock_redis: AsyncMock) -> None:
    mock_redis.hgetall.return_value = {}
    mgr = SessionManager(redis=mock_redis, timeout_minutes=30)
    session = await mgr.get_or_create("sess-1", channel="web_pwa")
    assert session["channel"] == "web_pwa"
    assert "history" in session


@pytest.mark.asyncio
async def test_get_existing_session(mock_redis: AsyncMock) -> None:
    existing = {
        b"channel": b"signal",
        b"history": json.dumps([{"role": "user", "content": "hi"}]).encode(),
        b"created_at": b"2026-03-19T10:00:00",
    }
    mock_redis.hgetall.return_value = existing
    mgr = SessionManager(redis=mock_redis, timeout_minutes=30)
    session = await mgr.get_or_create("sess-1", channel="signal")
    assert session["channel"] == "signal"
    assert len(session["history"]) == 1


@pytest.mark.asyncio
async def test_append_turn(mock_redis: AsyncMock) -> None:
    mock_redis.hgetall.return_value = {}
    mock_redis.hget.return_value = b"[]"
    mgr = SessionManager(redis=mock_redis, timeout_minutes=30)
    await mgr.get_or_create("sess-1", channel="web_pwa")
    await mgr.append_turn("sess-1", role="user", content="Good morning")
    # Verify hset was called to persist
    mock_redis.hset.assert_called()


@pytest.mark.asyncio
async def test_get_history_returns_list(mock_redis: AsyncMock) -> None:
    """get_history deserializes JSON from Redis."""
    history = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    mock_redis.hget.return_value = json.dumps(history).encode()
    mgr = SessionManager(redis=mock_redis, timeout_minutes=30)

    result = await mgr.get_history("sess-1")
    assert len(result) == 2
    assert result[0]["role"] == "user"
    assert result[1]["content"] == "hi"


@pytest.mark.asyncio
async def test_get_history_empty_returns_empty_list(mock_redis: AsyncMock) -> None:
    """get_history with no data returns empty list."""
    mock_redis.hget.return_value = None
    mgr = SessionManager(redis=mock_redis, timeout_minutes=30)

    result = await mgr.get_history("nonexistent")
    assert result == []


@pytest.mark.asyncio
async def test_ttl_refresh_on_existing_session(mock_redis: AsyncMock) -> None:
    """Accessing an existing session refreshes its TTL."""
    existing = {
        b"channel": b"web_pwa",
        b"history": b"[]",
    }
    mock_redis.hgetall.return_value = existing
    mgr = SessionManager(redis=mock_redis, timeout_minutes=45)
    await mgr.get_or_create("sess-1", channel="web_pwa")

    # expire should be called with the configured timeout
    mock_redis.expire.assert_called()
    call_args = mock_redis.expire.call_args_list[-1]
    assert call_args[0][1] == 45 * 60  # timeout in seconds
