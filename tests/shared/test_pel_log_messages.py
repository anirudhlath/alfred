"""PEL recovery log lines must actually render their values.

Regression: `reclaim_replayable` moved from `core/reflex/runner.py` (stdlib
logging, printf style) into `shared/redis_streams.py` (loguru, brace style).
The `%d`/`%s` placeholders stopped interpolating, so production logged the
literal string "Dropped %d stale pending entries on '%s' (older than %dms)" —
worse than silent, because the count and stream are the entire point of it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from loguru import logger

from shared.redis_streams import MAX_REPLAY_AGE_MS, reclaim_replayable


async def _reclaim_with_capture(entries: list[tuple[bytes, dict[bytes, bytes]]]) -> list[str]:
    messages: list[str] = []
    sink_id = logger.add(messages.append, level="INFO", format="{message}")
    redis = AsyncMock()
    redis.xautoclaim.return_value = ["0-0", entries, []]
    try:
        await reclaim_replayable(
            redis, "alfred:user:requests", "conscious-engine", "worker-1", now_ms=2_000_000
        )
    finally:
        logger.remove(sink_id)
    return messages


@pytest.mark.asyncio
async def test_the_drop_warning_names_the_count_and_stream() -> None:
    stale = 2_000_000 - MAX_REPLAY_AGE_MS - 1
    messages = await _reclaim_with_capture(
        [(f"{stale}-0".encode(), {b"event": b"{}"}), (f"{stale}-1".encode(), {b"event": b"{}"})]
    )

    assert any("2 stale pending entries" in m for m in messages), messages
    assert any("alfred:user:requests" in m for m in messages), messages
    assert not any("%d" in m or "%s" in m for m in messages), messages


@pytest.mark.asyncio
async def test_the_reclaim_line_names_the_count_and_stream() -> None:
    messages = await _reclaim_with_capture([(b"1999000-0", {b"event": b"{}"})])

    assert any("1 pending entries" in m for m in messages), messages
    assert any("alfred:user:requests" in m for m in messages), messages
    assert not any("%d" in m or "%s" in m for m in messages), messages
