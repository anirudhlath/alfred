"""reclaim_stale() — recover consumer-group messages that were never ACKed.

Regression: the Reflex Runner ACKs only on success, with a comment promising
"redelivery on next XREADGROUP cycle". XREADGROUP with ``>`` only ever delivers
NEW messages, so nothing redelivered: 199 real home events sat in the PEL for
three weeks, unprocessed and unbounded.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from shared.redis_streams import reclaim_stale


@pytest.mark.asyncio
async def test_reclaims_and_returns_stale_entries() -> None:
    redis = AsyncMock()
    redis.xautoclaim.return_value = ["0-0", [(b"1-1", {b"event": b"{}"})], []]

    claimed = await reclaim_stale(redis, "alfred:home:state_changed", "reflex-engine", "worker-1")

    assert claimed == [(b"1-1", {b"event": b"{}"})]
    kwargs: dict[str, Any] = redis.xautoclaim.call_args.kwargs
    assert kwargs["min_idle_time"] == 60_000
    assert kwargs["start_id"] == "0-0"


@pytest.mark.asyncio
async def test_returns_empty_when_nothing_is_stale() -> None:
    redis = AsyncMock()
    redis.xautoclaim.return_value = ["0-0", [], []]

    assert await reclaim_stale(redis, "s", "g", "c") == []


@pytest.mark.asyncio
async def test_survives_a_server_without_xautoclaim() -> None:
    """Older Redis lacks XAUTOCLAIM — degrade to no reclaim, never crash the loop."""
    redis = AsyncMock()
    redis.xautoclaim.side_effect = Exception("ERR unknown command 'XAUTOCLAIM'")

    assert await reclaim_stale(redis, "s", "g", "c") == []
