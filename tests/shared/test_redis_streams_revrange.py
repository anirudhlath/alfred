"""revrange() forwards the id bounds to XREVRANGE."""

from __future__ import annotations

from unittest.mock import AsyncMock

from shared.redis_streams import revrange


async def test_revrange_defaults_to_whole_stream() -> None:
    redis = AsyncMock()
    redis.xrevrange = AsyncMock(return_value=[("1-0", {"event": "{}"})])

    out = await revrange(redis, "alfred:events", count=1)

    assert out == [("1-0", {"event": "{}"})]
    redis.xrevrange.assert_awaited_once_with("alfred:events", max="+", min="-", count=1)


async def test_revrange_forwards_bounds() -> None:
    redis = AsyncMock()
    redis.xrevrange = AsyncMock(return_value=[])

    await revrange(redis, "alfred:events", count=10, max_id="200-0", min_id="100-0")

    redis.xrevrange.assert_awaited_once_with("alfred:events", max="200-0", min="100-0", count=10)
