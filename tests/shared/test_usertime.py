"""Tests for shared.usertime — user timezone resolution helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from shared.streams import TRIGGERS_CHANGED_CHANNEL, USER_TIMEZONE_KEY
from shared.usertime import (
    get_user_timezone,
    is_valid_timezone,
    set_user_timezone,
    user_local_now,
)


def test_is_valid_timezone() -> None:
    assert is_valid_timezone("America/Denver")
    assert is_valid_timezone("UTC")
    assert not is_valid_timezone("Not/AZone")
    assert not is_valid_timezone("")


@pytest.mark.asyncio
async def test_get_returns_stored_value() -> None:
    r = AsyncMock()
    r.get = AsyncMock(return_value=b"America/Denver")
    assert await get_user_timezone(r) == "America/Denver"
    r.get.assert_awaited_once_with(USER_TIMEZONE_KEY)


@pytest.mark.asyncio
async def test_get_falls_back_to_env_then_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    monkeypatch.setenv("ALFRED_TIMEZONE", "Europe/London")
    assert await get_user_timezone(r) == "Europe/London"
    monkeypatch.delenv("ALFRED_TIMEZONE")
    assert await get_user_timezone(r) == "UTC"


@pytest.mark.asyncio
async def test_get_ignores_invalid_stored_value() -> None:
    r = AsyncMock()
    r.get = AsyncMock(return_value="garbage")
    assert await get_user_timezone(r) == "UTC"


@pytest.mark.asyncio
async def test_set_writes_and_pokes_channel_on_change() -> None:
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    assert await set_user_timezone(r, "America/Denver") is True
    r.set.assert_awaited_once_with(USER_TIMEZONE_KEY, "America/Denver")
    r.publish.assert_awaited_once_with(TRIGGERS_CHANGED_CHANNEL, json.dumps({"op": "tz-changed"}))


@pytest.mark.asyncio
async def test_set_skips_write_when_unchanged() -> None:
    r = AsyncMock()
    r.get = AsyncMock(return_value=b"America/Denver")
    assert await set_user_timezone(r, "America/Denver") is False
    r.set.assert_not_awaited()
    r.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_rejects_invalid_timezone() -> None:
    r = AsyncMock()
    assert await set_user_timezone(r, "Not/AZone") is False
    r.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_local_now_converts_to_stored_zone() -> None:
    r = AsyncMock()
    r.get = AsyncMock(return_value=b"America/Denver")
    fixed = datetime(2026, 7, 15, 18, 0, tzinfo=UTC)  # 18:00 UTC → 12:00 MDT (UTC-6)
    local = await user_local_now(r, fixed)
    assert local.tzinfo is not None
    assert local.hour == 12  # converted to America/Denver wall-clock
    assert local == fixed  # same instant, different zone


@pytest.mark.asyncio
async def test_user_local_now_defaults_to_current_time() -> None:
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)  # → UTC
    local = await user_local_now(r)
    assert local.tzinfo is not None
