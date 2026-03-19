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
