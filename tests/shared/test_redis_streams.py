"""Tests for shared.redis_streams — create_redis() socket_timeout policy, reclaim_due()."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import pytest
from redis import exceptions as redis_exceptions

from shared.redis_streams import Redelivery, create_redis, reclaim_due


def test_create_redis_passes_socket_timeout_none() -> None:
    """redis-py 8 defaults socket_timeout to 5s, which breaks idle blocking stream
    reads (block=). create_redis must always pass socket_timeout=None so block=
    alone governs read timeouts.
    """
    mock_client = MagicMock()
    with patch("redis.asyncio.from_url", return_value=mock_client) as mock_from_url:
        result = create_redis("redis://localhost:6379")

    mock_from_url.assert_called_once_with(
        "redis://localhost:6379", decode_responses=False, socket_timeout=None
    )
    assert result is mock_client


def test_create_redis_forwards_decode_responses() -> None:
    """decode_responses must be forwarded verbatim (e.g. web_server.py needs False)."""
    mock_client = MagicMock()
    with patch("redis.asyncio.from_url", return_value=mock_client) as mock_from_url:
        create_redis("redis://localhost:6379", decode_responses=True)

    mock_from_url.assert_called_once_with(
        "redis://localhost:6379", decode_responses=True, socket_timeout=None
    )


# --- reclaim_due: delivery-counted redelivery with backoff (spec 6b, "Turns") ---

_STREAM, _GROUP = "s", "g"


class _Clock:
    """fakeredis's stream model reads one clock for delivery and idle times."""

    def __init__(self) -> None:
        self.ms = 1_800_000_000_000

    def advance(self, ms: int) -> None:
        self.ms += ms


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    import fakeredis.model._stream as fake_stream

    c = _Clock()
    monkeypatch.setattr(fake_stream, "current_time", lambda: c.ms)
    return c


async def _delivered_once() -> tuple[fakeredis.FakeAsyncRedis, bytes | str]:
    redis = fakeredis.FakeAsyncRedis()
    await redis.xgroup_create(_STREAM, _GROUP, id="0", mkstream=True)
    entry_id = await redis.xadd(_STREAM, {"event": "x"})
    await redis.xreadgroup(_GROUP, "c1", {_STREAM: ">"})
    return redis, entry_id


async def _times_delivered(redis: fakeredis.FakeAsyncRedis) -> list[int]:
    pending = await redis.xpending_range(_STREAM, _GROUP, min="-", max="+", count=10)
    return [int(p["times_delivered"]) for p in pending]


async def test_a_first_delivery_is_not_due_before_15s(clock: _Clock) -> None:
    redis, _ = await _delivered_once()
    clock.advance(10_000)

    assert await reclaim_due(redis, _STREAM, _GROUP, "c2") == []
    assert await _times_delivered(redis) == [1]


async def test_a_first_delivery_is_due_after_15s(clock: _Clock) -> None:
    redis, entry_id = await _delivered_once()
    clock.advance(16_000)

    [red] = await reclaim_due(redis, _STREAM, _GROUP, "c2")

    assert red == Redelivery(entry_id=entry_id, fields={b"event": b"x"}, delivery=2)
    assert await _times_delivered(redis) == [2]


async def test_a_second_delivery_waits_30s(clock: _Clock) -> None:
    redis, _ = await _delivered_once()
    clock.advance(16_000)
    await reclaim_due(redis, _STREAM, _GROUP, "c2")

    clock.advance(20_000)
    assert await reclaim_due(redis, _STREAM, _GROUP, "c2") == []

    clock.advance(11_000)
    [red] = await reclaim_due(redis, _STREAM, _GROUP, "c2")
    assert red.delivery == 3


async def test_a_trimmed_entry_leaves_the_pel_and_is_not_returned(clock: _Clock) -> None:
    """Redis 7 drops a deleted id from the PEL inside XCLAIM itself."""
    redis, entry_id = await _delivered_once()
    await redis.xdel(_STREAM, entry_id)
    clock.advance(16_000)

    assert await reclaim_due(redis, _STREAM, _GROUP, "c2") == []
    assert await _times_delivered(redis) == []


async def test_a_trimmed_entry_returned_without_fields_is_acked() -> None:
    """Older servers answer XCLAIM of a deleted id with a nil entry; redis-py
    hands that back as ``(None, None)``. The id still has to leave the PEL."""
    redis = MagicMock()
    redis.xpending_range = AsyncMock(
        return_value=[
            {
                "message_id": b"5-0",
                "consumer": b"c1",
                "time_since_delivered": 20_000,
                "times_delivered": 1,
            }
        ]
    )
    redis.xclaim = AsyncMock(return_value=[(None, None)])
    redis.xack = AsyncMock()

    assert await reclaim_due(redis, _STREAM, _GROUP, "c2") == []

    redis.xclaim.assert_awaited_once_with(
        _STREAM, _GROUP, "c2", min_idle_time=15_000, message_ids=[b"5-0"]
    )
    redis.xack.assert_awaited_once_with(_STREAM, _GROUP, b"5-0")


async def test_a_redis_error_returns_nothing() -> None:
    redis = MagicMock()
    redis.xpending_range = AsyncMock(side_effect=redis_exceptions.ConnectionError("down"))

    assert await reclaim_due(redis, _STREAM, _GROUP, "c2") == []
