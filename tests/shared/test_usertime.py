"""Tests for shared.usertime — user timezone resolution helpers."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from shared import usertime
from shared.streams import TRIGGERS_CHANGED_CHANNEL, USER_TIMEZONE_KEY
from shared.usertime import (
    get_user_timezone,
    is_valid_timezone,
    last_user_timezone,
    set_user_timezone,
)


@pytest.fixture(autouse=True)
def _forget_the_last_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usertime, "_last_resolved", None)


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


def test_the_last_zone_defaults_to_the_configured_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_TIMEZONE", "Europe/London")
    assert last_user_timezone() == "Europe/London"
    monkeypatch.delenv("ALFRED_TIMEZONE")
    assert last_user_timezone() == "UTC"


@pytest.mark.asyncio
async def test_the_last_zone_is_the_one_last_resolved_or_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALFRED_TIMEZONE", raising=False)
    r = AsyncMock()
    r.get = AsyncMock(return_value=b"America/Denver")
    await get_user_timezone(r)
    assert last_user_timezone() == "America/Denver"

    await set_user_timezone(r, "Asia/Kolkata")
    assert last_user_timezone() == "Asia/Kolkata"

    await set_user_timezone(r, "Not/AZone")
    assert last_user_timezone() == "Asia/Kolkata"
