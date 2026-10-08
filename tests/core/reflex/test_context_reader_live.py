"""ContextReader feeds Reflex the raw snapshot and the user's timezone (#285)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

from core.reflex.context_reader import ContextReader
from sdk.alfred_sdk.context import ContextEntry, ContextSnapshot

if TYPE_CHECKING:
    import pytest


async def test_get_snapshot_reads_live_state_fresh_every_time() -> None:
    snapshot = ContextSnapshot(
        controllable={"light": [ContextEntry(entity_id="light.a", state="on")]}
    )
    with patch(
        "core.reflex.context_reader.read_live_state", new=AsyncMock(return_value=snapshot)
    ) as read:
        reader = ContextReader(redis=AsyncMock())
        assert await reader.get_snapshot() is snapshot
        assert await reader.get_snapshot() is snapshot

    assert read.await_count == 2  # no cache: #283 removed it on purpose


async def test_get_snapshot_is_none_without_live_state() -> None:
    with patch("core.reflex.context_reader.read_live_state", new=AsyncMock(return_value=None)):
        assert await ContextReader(redis=AsyncMock()).get_snapshot() is None


async def test_get_user_timezone_reads_the_stored_zone() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=b"America/Chicago")

    assert await ContextReader(redis=redis).get_user_timezone() == "America/Chicago"


async def test_get_user_timezone_falls_back_to_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_TIMEZONE", raising=False)
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    assert await ContextReader(redis=redis).get_user_timezone() == "UTC"
