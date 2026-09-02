"""Reclaimed pending entries must not be acted on once they are stale.

Recovering the PEL is necessary (un-ACKed events were being dropped forever), but a
home event is only meaningful while it is current. Replaying a three-week-old
"Apple TV started playing" would turn the lights on in an empty room at 4am.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.reflex.runner import MAX_REPLAY_AGE_MS, is_replayable, reclaim_replayable


def test_a_fresh_entry_is_replayable() -> None:
    assert is_replayable(b"1000000-0", now_ms=1_000_000 + 30_000)


def test_an_entry_older_than_the_window_is_not() -> None:
    assert not is_replayable(b"1000000-0", now_ms=1_000_000 + MAX_REPLAY_AGE_MS + 1)


def test_boundary_is_inclusive() -> None:
    assert is_replayable(b"1000000-0", now_ms=1_000_000 + MAX_REPLAY_AGE_MS)


def test_accepts_str_entry_ids() -> None:
    assert is_replayable("1000000-0", now_ms=1_000_100)


def test_unparseable_entry_id_is_not_replayable() -> None:
    """Fail closed — an id we cannot date could be arbitrarily old."""
    assert not is_replayable(b"not-an-id", now_ms=1_000_000)


@pytest.mark.asyncio
async def test_reclaim_returns_only_fresh_entries_and_drops_the_rest() -> None:
    """Stale entries are ACKed away so the PEL drains without the house acting on them."""
    now = 2_000_000
    fresh = (f"{now - 1000}-0".encode(), {b"event": b"{}"})
    stale = (f"{now - MAX_REPLAY_AGE_MS - 1}-0".encode(), {b"event": b"{}"})

    redis = AsyncMock()
    redis.xautoclaim.return_value = ["0-0", [stale, fresh], []]

    replayable = await reclaim_replayable(
        redis, "alfred:home:state_changed", "reflex-engine", "worker-1", now_ms=now
    )

    assert replayable == [fresh]
    redis.xack.assert_awaited_once_with("alfred:home:state_changed", "reflex-engine", stale[0])


@pytest.mark.asyncio
async def test_reclaim_with_nothing_pending_is_a_noop() -> None:
    redis = AsyncMock()
    redis.xautoclaim.return_value = ["0-0", [], []]

    assert await reclaim_replayable(redis, "s", "g", "c", now_ms=1) == []
    redis.xack.assert_not_awaited()
