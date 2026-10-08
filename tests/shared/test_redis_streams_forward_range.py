"""forward_range() forwards the id bounds to XRANGE."""

from __future__ import annotations

from unittest.mock import AsyncMock

from shared.redis_streams import forward_range


async def test_forward_range_defaults_to_whole_stream() -> None:
    redis = AsyncMock()
    redis.xrange = AsyncMock(return_value=[("1-0", {"event": "{}"})])

    out = await forward_range(redis, "alfred:events")

    assert out == [("1-0", {"event": "{}"})]
    redis.xrange.assert_awaited_once_with("alfred:events", min="-", max="+", count=None)


async def test_forward_range_forwards_bounds() -> None:
    redis = AsyncMock()
    redis.xrange = AsyncMock(return_value=[])

    await forward_range(redis, "alfred:events", min_id="100-0", max_id="200-0", count=10)

    redis.xrange.assert_awaited_once_with("alfred:events", min="100-0", max="200-0", count=10)
