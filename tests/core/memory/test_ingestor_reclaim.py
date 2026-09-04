"""The Memory Ingestor must reclaim its PEL — and process what it reclaims.

``run_ingestor`` reads with ``XREADGROUP '>'`` and ACKs only on success, so an
entry whose ingest raises (a transient CUDA OOM on the embed path, say) is never
redelivered on its own: it is lost and it leaks a pending entry. Passive
observation takes this loop from ~0 to ~250 entries/day, which is what makes the
gap matter.

The reclaim must use ``reclaim_stale``, NOT ``reclaim_replayable`` — the latter's
5-minute replay window encodes "a stale state change isn't worth *acting on*",
which is right for the reflex loop and wrong for memory: a ten-minute-old
observation is still worth remembering.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from bus.schemas.events import ReflexObservation
from shared.streams import REFLEX_OBSERVATIONS_STREAM

if TYPE_CHECKING:
    from core.memory.significance import SignificanceScorer

# An id from 1970 — far outside reclaim_replayable's 5-minute replay window.
ANCIENT_ID = b"1-1"


def _payload(entity_id: str = "light.hallway") -> dict[bytes, bytes]:
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={"entity_id": entity_id, "old_state": "off", "new_state": "on"},
    )
    return {b"event": obs.model_dump_json().encode()}


class FakeRedis:
    """Minimal consumer-group double: new entries once, then a real PEL.

    ``xreadgroup`` delivers each entry exactly once (like ``>``), moving it into
    ``pending``; ``xack`` removes it; ``xautoclaim`` returns whatever is still
    pending. That is the whole point of the test — a mock that redelivers would
    hide the bug.
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
        if self.reads >= self._max_reads:
            self._shutdown.set()
        if not self._new:
            return []
        entry_id, data = self._new.pop(0)
        self.pending[entry_id] = data
        return [(REFLEX_OBSERVATIONS_STREAM.encode(), [(entry_id, data)])]

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


async def _run(
    redis: FakeRedis,
    shutdown: asyncio.Event,
    ingest: AsyncMock | None = None,
    *,
    monkeypatch: pytest.MonkeyPatch,
    reclaim_every: int | None = 2,
) -> AsyncMock:
    """Drive run_ingestor against the double until it shuts itself down."""
    from core.memory import ingestor

    if reclaim_every is not None:
        monkeypatch.setattr(ingestor, "_PEL_RECLAIM_EVERY", reclaim_every)
    ingest = ingest or AsyncMock()
    monkeypatch.setattr(ingestor, "ingest_observation", ingest)

    scorer: SignificanceScorer = AsyncMock()
    await asyncio.wait_for(
        ingestor.run_ingestor(redis, AsyncMock(), scorer, shutdown_event=shutdown),  # type: ignore[arg-type]
        timeout=5,
    )
    return ingest


@pytest.mark.asyncio
async def test_a_failed_ingest_is_reclaimed_and_reprocessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: a failure must come back, not vanish."""
    shutdown = asyncio.Event()
    redis = FakeRedis([(b"100-0", _payload())], shutdown)
    ingest = AsyncMock(side_effect=[RuntimeError("CUDA out of memory"), None])

    await _run(redis, shutdown, ingest, monkeypatch=monkeypatch)

    assert ingest.await_count == 2, "reclaimed entry was counted but never reprocessed"
    assert redis.acked == [b"100-0"]
    assert redis.pending == {}


@pytest.mark.asyncio
async def test_a_transient_failure_leaves_the_entry_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A still-failing entry stays in the PEL for the next pass — never ACKed away."""
    shutdown = asyncio.Event()
    redis = FakeRedis([(b"100-0", _payload())], shutdown)
    ingest = AsyncMock(side_effect=RuntimeError("CUDA out of memory"))

    await _run(redis, shutdown, ingest, monkeypatch=monkeypatch)

    assert redis.acked == []
    assert b"100-0" in redis.pending


@pytest.mark.asyncio
async def test_an_observation_older_than_the_replay_window_is_still_ingested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reclaim_replayable would ACK-and-discard this. Memory is not action."""
    shutdown = asyncio.Event()
    redis = FakeRedis([(ANCIENT_ID, _payload())], shutdown)
    ingest = AsyncMock(side_effect=[RuntimeError("embedder busy"), None])

    await _run(redis, shutdown, ingest, monkeypatch=monkeypatch)

    assert ingest.await_count == 2, "an old observation was dropped instead of remembered"
    assert redis.acked == [ANCIENT_ID]


@pytest.mark.asyncio
async def test_an_unparseable_entry_is_acked_rather_than_reclaimed_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Poison entries: a payload that can never parse must leave the PEL."""
    shutdown = asyncio.Event()
    redis = FakeRedis([(b"100-0", {b"event": b"not json {{{"})], shutdown)
    ingest = AsyncMock()

    await _run(redis, shutdown, ingest, monkeypatch=monkeypatch)

    ingest.assert_not_awaited()
    assert redis.acked == [b"100-0"]
    assert redis.pending == {}


@pytest.mark.asyncio
async def test_an_entry_without_an_event_field_is_acked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the pre-existing skip path must still ACK."""
    shutdown = asyncio.Event()
    redis = FakeRedis([(b"100-0", {b"junk": b"1"})], shutdown)

    await _run(redis, shutdown, monkeypatch=monkeypatch)

    assert redis.acked == [b"100-0"]
    assert redis.pending == {}


@pytest.mark.asyncio
async def test_reclaim_is_periodic_not_every_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """XAUTOCLAIM every 5s block would hammer Redis — the cadence guard is real."""
    shutdown = asyncio.Event()
    redis = FakeRedis([], shutdown, max_reads=3)

    await _run(redis, shutdown, monkeypatch=monkeypatch, reclaim_every=None)

    assert redis.claims == 0


def test_reclaim_cadence_is_roughly_one_minute() -> None:
    from core.memory.ingestor import _PEL_RECLAIM_EVERY

    assert _PEL_RECLAIM_EVERY == 12  # 12 x 5s block
