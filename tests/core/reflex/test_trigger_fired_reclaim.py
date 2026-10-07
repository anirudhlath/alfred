"""Reflex's TriggerFired loop must reclaim its PEL — and process what it reclaims.

``_consume_trigger_fired`` reads ``alfred:events`` with ``XREADGROUP '>'`` and
ACKs only on success, so a TriggerFired whose handling raises (notification
dispatch failed, say) is never redelivered on its own: the reminder the user
asked for is lost and a pending entry leaks forever (issue #204).

The reclaim must use ``reclaim_replayable``, NOT ``reclaim_stale`` — a
TriggerFired is an instruction to *act*, and acting on an hours-old one drives
the system from history. That is the opposite of the Memory Ingestor's
deliberate exception (``tests/core/memory/test_ingestor_reclaim.py``, whose
``FakeRedis`` this double follows).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from bus.schemas.events import ReflexProposal, TriggerFired
from shared.streams import EVENTS_STREAM

# An id from 1970 — far outside reclaim_replayable's 5-minute replay window.
ANCIENT_ID = b"1-1"


def _fresh_id() -> bytes:
    """An id minted now, so a reclaim pass still considers it replayable."""
    return f"{int(time.time() * 1000)}-0".encode()


def _payload() -> dict[bytes, bytes]:
    event = TriggerFired(trigger_id="t-1", trigger_name="take medicine", trigger_type="time")
    return {b"event": event.model_dump_json().encode()}


class FakeRedis:
    """Minimal consumer-group double: new entries once, then a real PEL.

    ``xreadgroup`` delivers each entry exactly once (like ``>``), moving it into
    ``pending``; ``xack`` removes it; ``xautoclaim`` returns whatever is still
    pending, oldest first. A mock that redelivered on its own would hide the bug.

    Deliberately *ignores* ``min_idle_time``, ``start_id`` and ``block``: every
    pending entry is claimable on every pass and reads never block, so a passing
    test here says nothing about idle thresholds, cursors or timing.
    """

    def __init__(
        self,
        entries: list[tuple[bytes, dict[bytes, bytes]]],
        shutdown: asyncio.Event,
        *,
        max_reads: int = 2,
    ) -> None:
        self._new = list(entries)
        self._shutdown = shutdown
        self._max_reads = max_reads
        self.pending: dict[bytes, dict[bytes, bytes]] = {}
        self.acked: list[bytes] = []
        self.reads = 0
        self.claims = 0
        self.blocks: list[int | None] = []

    async def xgroup_create(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    async def xreadgroup(
        self,
        _group: str,
        _consumer: str,
        _streams: dict[str, str],
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[bytes, list[tuple[bytes, dict[bytes, bytes]]]]]:
        self.reads += 1
        self.blocks.append(block)
        if self.reads >= self._max_reads:
            self._shutdown.set()
        if not self._new:
            return []
        entry_id, data = self._new.pop(0)
        self.pending[entry_id] = data
        return [(EVENTS_STREAM.encode(), [(entry_id, data)])]

    async def xack(self, _stream: str, _group: str, entry_id: bytes) -> int:
        self.pending.pop(entry_id, None)
        self.acked.append(entry_id)
        return 1

    async def xautoclaim(
        self,
        _stream: str,
        _group: str,
        _consumer: str,
        min_idle_time: int = 0,
        start_id: str = "0-0",
        count: int = 10,
    ) -> list[Any]:
        self.claims += 1
        claimed = list(self.pending.items())[:count]
        return ["0-0", claimed, []]


def _publisher(side_effect: Any = None) -> AsyncMock:
    publisher = AsyncMock()
    publisher.publish = AsyncMock(side_effect=side_effect)
    return publisher


async def _run(
    redis: FakeRedis,
    shutdown: asyncio.Event,
    publisher: AsyncMock | None = None,
    *,
    monkeypatch: pytest.MonkeyPatch,
    reclaim_every: int | None = 2,
) -> AsyncMock:
    """Drive _consume_trigger_fired against the double until it shuts itself down."""
    import core.reflex.__main__ as reflex_main

    monkeypatch.setattr(reflex_main, "_shutdown", shutdown)
    if reclaim_every is not None:
        monkeypatch.setattr(reflex_main, "_PEL_RECLAIM_EVERY", reclaim_every)
    publisher = publisher or _publisher()
    engine = AsyncMock()
    engine.process_trigger_fired = AsyncMock(return_value=ReflexProposal(decision="none"))

    await asyncio.wait_for(
        reflex_main._consume_trigger_fired(
            redis,  # type: ignore[arg-type]
            engine,
            AsyncMock(),
            publisher,
        ),
        timeout=5,
    )
    return publisher


@pytest.mark.asyncio
async def test_a_failed_trigger_fired_is_reclaimed_and_reprocessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: a failed reminder must come back, not vanish."""
    shutdown = asyncio.Event()
    entry_id = _fresh_id()
    redis = FakeRedis([(entry_id, _payload())], shutdown)
    publisher = _publisher([RuntimeError("notification dispatch unavailable"), None])

    await _run(redis, shutdown, publisher, monkeypatch=monkeypatch)

    assert publisher.publish.await_count == 2, "reclaimed entry was never reprocessed"
    assert redis.claims == 1
    assert redis.acked == [entry_id]
    assert redis.pending == {}


@pytest.mark.asyncio
async def test_a_still_failing_trigger_fired_stays_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inside the replay window a failing entry stays in the PEL — never ACKed away."""
    shutdown = asyncio.Event()
    entry_id = _fresh_id()
    redis = FakeRedis([(entry_id, _payload())], shutdown)
    publisher = _publisher(RuntimeError("notification dispatch unavailable"))

    await _run(redis, shutdown, publisher, monkeypatch=monkeypatch)

    assert publisher.publish.await_count == 2
    assert redis.acked == []
    assert entry_id in redis.pending


@pytest.mark.asyncio
async def test_a_trigger_fired_older_than_the_replay_window_is_dropped_not_replayed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reclaim_replayable, not reclaim_stale: acting on an hours-old fire is worse."""
    shutdown = asyncio.Event()
    redis = FakeRedis([], shutdown, max_reads=1)
    redis.pending[ANCIENT_ID] = _payload()

    publisher = await _run(redis, shutdown, monkeypatch=monkeypatch, reclaim_every=1)

    publisher.publish.assert_not_awaited()
    assert redis.acked == [ANCIENT_ID]
    assert redis.pending == {}


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"not json {{{", id="bad-json"),
        pytest.param(b"[1, 2, 3]", id="json-not-an-object"),
        pytest.param(json.dumps({"event_type": "trigger_fired"}).encode(), id="fails-validation"),
    ],
)
@pytest.mark.asyncio
async def test_an_unparseable_entry_is_acked_rather_than_reclaimed_forever(
    raw: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Poison entries: a payload that can never parse must leave the PEL on delivery.

    Read on the first iteration, reclaim pass on the second: the pass must find
    nothing to bring back.
    """
    shutdown = asyncio.Event()
    entry_id = _fresh_id()
    redis = FakeRedis([(entry_id, {b"event": raw})], shutdown, max_reads=3)

    publisher = await _run(redis, shutdown, monkeypatch=monkeypatch, reclaim_every=2)

    publisher.publish.assert_not_awaited()
    assert redis.claims == 1
    assert redis.acked == [entry_id], "a poison entry was left for the reclaim pass"
    assert redis.pending == {}


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({b"junk": b"1"}, id="no-event-field"),
        pytest.param(
            {b"event": json.dumps({"event_type": "trigger_created", "source": "t"}).encode()},
            id="another-event-type",
        ),
    ],
)
@pytest.mark.asyncio
async def test_entries_that_are_not_ours_are_still_acked(
    data: dict[bytes, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: alfred:events is shared, and the pre-existing skip paths ACK."""
    shutdown = asyncio.Event()
    entry_id = _fresh_id()
    redis = FakeRedis([(entry_id, data)], shutdown)

    publisher = await _run(redis, shutdown, monkeypatch=monkeypatch)

    publisher.publish.assert_not_awaited()
    assert redis.acked == [entry_id]
    assert redis.pending == {}


@pytest.mark.asyncio
async def test_the_reclaim_counter_resets_between_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """XAUTOCLAIM on every 5s block would hammer Redis — the cadence guard is real."""
    shutdown = asyncio.Event()
    redis = FakeRedis([], shutdown, max_reads=7)

    await _run(redis, shutdown, monkeypatch=monkeypatch, reclaim_every=3)

    assert redis.reads == 7
    assert redis.claims == 2


@pytest.mark.asyncio
async def test_the_reclaim_cadence_is_about_a_minute_of_real_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reads the block the loop actually hands XREADGROUP, not just the constant."""
    from core.reflex.__main__ import _PEL_RECLAIM_EVERY

    shutdown = asyncio.Event()
    redis = FakeRedis([], shutdown, max_reads=1)

    await _run(redis, shutdown, monkeypatch=monkeypatch, reclaim_every=None)

    assert redis.blocks == [5000]
    assert _PEL_RECLAIM_EVERY * redis.blocks[0] == 60_000
