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
from loguru import logger

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
    pending, oldest first, so head-of-line effects are visible. That is the
    whole point of the test — a mock that redelivers would hide the bug.

    Deliberately *ignores* ``min_idle_time``, ``start_id`` and ``block``: every
    pending entry is claimable on every pass and reads never block. Real Redis
    would not re-claim an entry read moments ago, and would resume from
    ``start_id`` — so do not read a passing test here as evidence about idle
    thresholds, cursor behaviour or timing.
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
        self.hashes: dict[str, dict[str, int]] = {}

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
        return [(REFLEX_OBSERVATIONS_STREAM.encode(), [(entry_id, data)])]

    async def xack(self, _stream: str, _group: str, entry_id: bytes) -> int:
        self.pending.pop(entry_id, None)
        self.acked.append(entry_id)
        return 1

    async def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        self.hashes.setdefault(key, {})
        self.hashes[key][field] = self.hashes[key].get(field, 0) + amount
        return self.hashes[key][field]

    async def hdel(self, key: str, *fields: str) -> int:
        bucket = self.hashes.get(key, {})
        return sum(bucket.pop(f, None) is not None for f in fields)

    async def expire(self, _key: str, _seconds: int) -> bool:
        return True

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
    passive_scorer: SignificanceScorer = AsyncMock()
    await asyncio.wait_for(
        ingestor.run_ingestor(  # type: ignore[arg-type]
            redis, AsyncMock(), scorer, passive_scorer, shutdown_event=shutdown
        ),
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
async def test_the_reclaim_counter_resets_between_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """XAUTOCLAIM on every 5s block would hammer Redis — the cadence guard is real.

    Seven iterations at a cadence of three must reclaim exactly twice, on
    iterations 3 and 6. Driving three iterations against a cadence of twelve and
    asserting no claims only restates that 3 < 12, and never exercises the reset.
    """
    shutdown = asyncio.Event()
    redis = FakeRedis([], shutdown, max_reads=7)

    await _run(redis, shutdown, monkeypatch=monkeypatch, reclaim_every=3)

    assert redis.reads == 7
    assert redis.claims == 2


@pytest.mark.asyncio
async def test_the_reclaim_cadence_is_about_a_minute_of_real_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_PEL_RECLAIM_EVERY``'s "12 x 5s block" is only true if the block is 5s.

    Asserting ``_PEL_RECLAIM_EVERY == 12`` restates the constant and would stay
    green if the block dropped to 1s, making the real cadence 12 seconds. This
    reads the block the loop actually hands XREADGROUP.
    """
    from core.memory.ingestor import _PEL_RECLAIM_EVERY

    shutdown = asyncio.Event()
    redis = FakeRedis([], shutdown, max_reads=1)

    await _run(redis, shutdown, monkeypatch=monkeypatch, reclaim_every=None)

    assert redis.blocks == [5000]
    assert _PEL_RECLAIM_EVERY * redis.blocks[0] == 60_000


@pytest.mark.asyncio
async def test_an_xack_failure_does_not_kill_the_ingestor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The batch loop must survive a Redis blip on the ACK, like the reflex loop does.

    ``core/reflex/__main__.py`` wraps ``process_stream_entry`` + ``xack``
    together for exactly this reason. An unguarded ``xack`` here propagates out
    of ``run_ingestor`` and takes the process down.
    """
    shutdown = asyncio.Event()
    redis = FakeRedis([(b"100-0", _payload()), (b"200-0", _payload())], shutdown, max_reads=4)
    original_xack = redis.xack

    async def flaky_xack(stream: str, group: str, entry_id: bytes) -> int:
        if entry_id == b"100-0":
            raise ConnectionError("Redis went away mid-ACK")
        return await original_xack(stream, group, entry_id)

    redis.xack = flaky_xack  # type: ignore[method-assign]

    await _run(redis, shutdown, monkeypatch=monkeypatch, reclaim_every=None)

    assert b"200-0" in redis.acked, "an xack failure took the whole ingestor down"


# ---------------------------------------------------------------------------
# Delivery-attempt cap — bounded retries, bounded ZINCRBY, unstarved budget
# ---------------------------------------------------------------------------


async def _run_capturing(
    redis: FakeRedis,
    shutdown: asyncio.Event,
    ingest: AsyncMock,
    *,
    monkeypatch: pytest.MonkeyPatch,
    level: str = "WARNING",
) -> list[str]:
    messages: list[str] = []
    sink_id = logger.add(messages.append, level=level, format="{message}")
    try:
        await _run(redis, shutdown, ingest, monkeypatch=monkeypatch, reclaim_every=1)
    finally:
        logger.remove(sink_id)
    return messages


@pytest.mark.asyncio
async def test_an_entry_that_always_fails_is_dropped_at_the_attempt_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unbounded retries burn an unbounded ZINCRBY on alfred:entity:freq:observed.

    SignificanceScorer._score_novelty increments *before* EpisodicMemory.write,
    so every reclaim of an entry that dies on the embed path counts its entities
    again and novelty (1/count) is permanently deflated once the outage clears.
    The cap bounds that to _MAX_DELIVERY_ATTEMPTS.
    """
    from core.memory.ingestor import _MAX_DELIVERY_ATTEMPTS

    shutdown = asyncio.Event()
    redis = FakeRedis([], shutdown, max_reads=20)
    redis.pending[b"100-0"] = _payload()
    ingest = AsyncMock(side_effect=RuntimeError("deterministic downstream failure"))

    messages = await _run_capturing(redis, shutdown, ingest, monkeypatch=monkeypatch, level="ERROR")

    assert ingest.await_count == _MAX_DELIVERY_ATTEMPTS
    assert redis.acked == [b"100-0"], "a permanently failing entry never left the PEL"
    assert redis.pending == {}
    assert any("giving up" in m and "100-0" in m for m in messages), messages


@pytest.mark.asyncio
async def test_a_recovered_entry_forgets_its_earlier_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient failure must not spend the budget of some later, unrelated one."""
    from shared.streams import INGEST_ATTEMPTS_KEY

    shutdown = asyncio.Event()
    redis = FakeRedis([], shutdown, max_reads=4)
    redis.pending[b"100-0"] = _payload()
    ingest = AsyncMock(side_effect=[RuntimeError("CUDA out of memory"), None])

    await _run(redis, shutdown, ingest, monkeypatch=monkeypatch, reclaim_every=1)

    assert redis.acked == [b"100-0"]
    assert redis.hashes.get(INGEST_ATTEMPTS_KEY, {}) == {}, "attempt counter leaked after success"


@pytest.mark.asyncio
async def test_poison_entries_do_not_starve_the_entries_behind_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding 4: reclaim_stale rescans from 0-0 with a count budget every pass.

    Ten parseable-but-permanently-failing entries at the head consume the whole
    budget forever, so nothing behind them is ever reclaimed. The existing poison
    policy only ACK-drops entries that fail to *parse*.
    """
    shutdown = asyncio.Event()
    redis = FakeRedis([], shutdown, max_reads=20)
    for i in range(10):
        redis.pending[f"{i}-0".encode()] = _payload(f"light.poison_{i}")
    redis.pending[b"99-0"] = _payload("light.behind_the_head")

    processed: list[str] = []

    async def ingest(obs: ReflexObservation, *_args: object, **_kwargs: object) -> None:
        entity = str(obs.trigger_event["entity_id"])
        if "poison" in entity:
            raise RuntimeError("deterministic downstream failure")
        processed.append(entity)

    await _run(
        redis, shutdown, AsyncMock(side_effect=ingest), monkeypatch=monkeypatch, reclaim_every=1
    )

    assert processed == ["light.behind_the_head"], (
        "the head of the PEL starved everything behind it"
    )
    assert redis.pending == {}


@pytest.mark.asyncio
async def test_consecutive_full_reclaim_batches_are_warned_about(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wedged PEL must be observable, not merely survivable."""
    shutdown = asyncio.Event()
    redis = FakeRedis([], shutdown, max_reads=4)
    for i in range(12):
        redis.pending[f"{i}-0".encode()] = _payload(f"light.stuck_{i}")

    messages = await _run_capturing(
        redis, shutdown, AsyncMock(side_effect=RuntimeError("wedged")), monkeypatch=monkeypatch
    )

    assert any("full reclaim batch" in m for m in messages), messages
