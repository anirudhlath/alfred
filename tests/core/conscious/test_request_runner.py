"""The Conscious Engine's per-entry runner: answer once, redeliver, cap, dead-letter.

Regression (kept): the reclaim block once called XAUTOCLAIM and only logged the
count, so the same stuck user requests were re-claimed every 60 seconds forever.
Reclaimed entries go through this same runner.

Spec 6 ("Exactly one answer") and 6b ("Turns"): a transient failure is left
un-ACKed for redelivery; a permanent one, the third failed delivery, or a
redelivery past the replay window is answered with FAILED_REPLY, ACKed and
dead-lettered. The answered marker makes a crash between publishing and ACKing
harmless.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock

import fakeredis
import pytest
from pydantic_ai.exceptions import ModelHTTPError

from bus.schemas.events import AlfredResponse
from core.conscious.effects import EffectLedger, ledger_reply
from core.conscious.engine import ConsciousEngine
from core.conscious.failures import FAILED_REPLY
from core.conscious.runner import process_request_entry
from shared.redis_streams import forward_range, reclaim_due
from shared.streams import (
    ANSWERED_KEY,
    DEAD_LETTER_STREAM,
    USER_REQUESTS_STREAM,
    USER_RESPONSES_STREAM,
)

_EVENT_ID = "11111111-1111-1111-1111-111111111111"
_REQUEST = (
    f'{{"event_id":"{_EVENT_ID}","event_type":"user_request",'
    '"timestamp":"2026-09-03T03:00:00Z","source":"web","channel":"web_pwa",'
    '"session_id":"s1","identity_claim":"sir","content_type":"text",'
    '"content":"turn off the lights"}'
)
_GROUP = "conscious-engine"
_CONSUMER = "worker-1"


def _engine() -> AsyncMock:
    engine = AsyncMock()
    engine.process_request = AsyncMock(
        return_value=AlfredResponse(
            source="conscious-engine", channel="web_pwa", session_id="s1", text="Done, sir."
        )
    )
    engine.failure_reply = ConsciousEngine.failure_reply
    engine.record_partial_turn = AsyncMock(side_effect=ConsciousEngine.failure_reply)
    return engine


async def _acted(redis: fakeredis.FakeAsyncRedis, *, ok: bool = True) -> str:
    """The request already turned a light on; returns the reply its ledger gives."""
    ledger = EffectLedger(redis)
    index = await ledger.started(_EVENT_ID, "home.light_turn_on", {"entity_id": "light.porch"})
    await ledger.finished(_EVENT_ID, index, ok=ok)
    return ledger_reply(await ledger.read(_EVENT_ID))


async def _pending(
    fields: dict[Any, Any] | None = None, *, entry_id: str = "*"
) -> tuple[fakeredis.FakeAsyncRedis, bytes | str]:
    """A request read by the group and not yet ACKed — the state the runner sees."""
    redis = fakeredis.FakeAsyncRedis()
    await redis.xgroup_create(USER_REQUESTS_STREAM, _GROUP, id="0", mkstream=True)
    added = await redis.xadd(USER_REQUESTS_STREAM, fields or {"event": _REQUEST}, id=entry_id)
    await redis.xreadgroup(_GROUP, _CONSUMER, {USER_REQUESTS_STREAM: ">"})
    return redis, added


async def _run(
    redis: fakeredis.FakeAsyncRedis, entry_id: bytes | str, engine: Any, **kw: Any
) -> None:
    eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
    [(_id, fields)] = await forward_range(redis, USER_REQUESTS_STREAM, min_id=eid, max_id=eid)
    await process_request_entry(
        entry_id,
        fields,
        engine=engine,
        redis=redis,
        stream=USER_REQUESTS_STREAM,
        group=_GROUP,
        consumer=_CONSUMER,
        **kw,
    )


class _Clock:
    """fakeredis's stream model reads one clock for delivery and idle times."""

    def __init__(self) -> None:
        self.ms = int(time.time() * 1000)

    def advance(self, ms: int) -> None:
        self.ms += ms


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    import fakeredis.model._stream as fake_stream

    c = _Clock()
    monkeypatch.setattr(fake_stream, "current_time", lambda: c.ms)
    return c


async def _times_delivered(redis: fakeredis.FakeAsyncRedis) -> list[int]:
    pending = await redis.xpending_range(USER_REQUESTS_STREAM, _GROUP, min="-", max="+", count=10)
    return [int(p["times_delivered"]) for p in pending]


async def _pending_count(redis: fakeredis.FakeAsyncRedis) -> int:
    summary = await redis.xpending(USER_REQUESTS_STREAM, _GROUP)
    return int(summary["pending"])


async def _responses(redis: fakeredis.FakeAsyncRedis) -> list[AlfredResponse]:
    return [
        AlfredResponse.model_validate_json(fields[b"event"])
        for _id, fields in await forward_range(redis, USER_RESPONSES_STREAM)
    ]


async def _dead_letters(redis: fakeredis.FakeAsyncRedis) -> list[dict[bytes | str, Any]]:
    return [fields for _id, fields in await forward_range(redis, DEAD_LETTER_STREAM)]


async def test_success_publishes_marks_answered_acks_and_calls_after_answer() -> None:
    redis, entry_id = await _pending()
    after_answer = AsyncMock()

    await _run(redis, entry_id, _engine(), after_answer=after_answer)

    assert [r.text for r in await _responses(redis)] == ["Done, sir."]
    assert await redis.exists(ANSWERED_KEY.format(_EVENT_ID))
    assert 0 < await redis.ttl(ANSWERED_KEY.format(_EVENT_ID)) <= 3600
    assert await _pending_count(redis) == 0
    after_answer.assert_awaited_once()


async def test_acks_and_skips_an_entry_with_no_event_field() -> None:
    """Unparseable entries must leave the PEL, or they are reclaimed forever."""
    redis, entry_id = await _pending({"junk": "x"})
    engine = _engine()

    await _run(redis, entry_id, engine)

    engine.process_request.assert_not_awaited()
    assert await _pending_count(redis) == 0


async def test_an_answered_request_is_acked_without_running() -> None:
    redis, entry_id = await _pending()
    await redis.set(ANSWERED_KEY.format(_EVENT_ID), "1")
    engine = _engine()

    await _run(redis, entry_id, engine, delivery=2)

    engine.process_request.assert_not_awaited()
    assert await _responses(redis) == []
    assert await _pending_count(redis) == 0


async def test_a_429_on_the_first_delivery_stays_pending() -> None:
    redis, entry_id = await _pending()
    engine = _engine()
    engine.process_request.side_effect = ModelHTTPError(429, "m")

    await _run(redis, entry_id, engine, delivery=1)

    assert await _pending_count(redis) == 1
    assert await _responses(redis) == []
    assert await _dead_letters(redis) == []


async def test_a_429_on_the_third_delivery_answers_once_and_dead_letters() -> None:
    redis, entry_id = await _pending()
    engine = _engine()
    engine.process_request.side_effect = ModelHTTPError(429, "m")

    await _run(redis, entry_id, engine, delivery=3)

    [reply] = await _responses(redis)
    assert reply.text == FAILED_REPLY
    assert reply.mood == "concerned"
    assert (reply.channel, reply.session_id) == ("web_pwa", "s1")
    assert await _pending_count(redis) == 0
    assert await redis.exists(ANSWERED_KEY.format(_EVENT_ID))
    [letter] = await _dead_letters(redis)
    assert letter[b"reason"] == b"exhausted"
    assert letter[b"deliveries"] == b"3"


async def test_a_400_answers_at_once_and_dead_letters() -> None:
    redis, entry_id = await _pending()
    engine = _engine()
    engine.process_request.side_effect = ModelHTTPError(400, "m")

    await _run(redis, entry_id, engine, delivery=1)

    assert [r.text for r in await _responses(redis)] == [FAILED_REPLY]
    assert await _pending_count(redis) == 0
    [letter] = await _dead_letters(redis)
    assert letter[b"reason"] == b"permanent"


async def test_a_redelivery_past_the_replay_window_is_not_run() -> None:
    """A five-minute-late "unlock the door" must not happen (Review Focus 3)."""
    now_ms = int(time.time() * 1000)
    redis, entry_id = await _pending(entry_id=f"{now_ms - 301_000}-0")
    engine = _engine()

    await _run(redis, entry_id, engine, delivery=2, now_ms=now_ms)

    engine.process_request.assert_not_awaited()
    assert [r.text for r in await _responses(redis)] == [FAILED_REPLY]
    assert await _pending_count(redis) == 0
    [letter] = await _dead_letters(redis)
    assert letter[b"reason"] == b"expired"


async def test_an_old_first_delivery_is_not_run_either() -> None:
    """A request read live after an outage is as late as a redelivered one."""
    now_ms = int(time.time() * 1000)
    redis, entry_id = await _pending(entry_id=f"{now_ms - 301_000}-0")
    engine = _engine()

    await _run(redis, entry_id, engine, delivery=1, now_ms=now_ms)

    engine.process_request.assert_not_awaited()
    assert [r.text for r in await _responses(redis)] == [FAILED_REPLY]
    assert await _pending_count(redis) == 0
    [letter] = await _dead_letters(redis)
    assert letter[b"reason"] == b"expired"


@pytest.mark.parametrize("delivery", [1, 2])
async def test_an_entry_older_than_the_marker_ttl_is_dropped_unanswered(delivery: int) -> None:
    """Past the marker's TTL it may have been answered already, and an hours-late
    reply is noise: ACK and dead-letter, publish nothing."""
    now_ms = int(time.time() * 1000)
    redis, entry_id = await _pending(entry_id=f"{now_ms - 3_601_000}-0")
    engine = _engine()

    await _run(redis, entry_id, engine, delivery=delivery, now_ms=now_ms)

    engine.process_request.assert_not_awaited()
    assert await _responses(redis) == []
    assert await _pending_count(redis) == 0
    [letter] = await _dead_letters(redis)
    assert letter[b"reason"] == b"stale"


async def test_a_delivery_past_the_cap_gives_up_without_running() -> None:
    """Delivery 3 failed and its FAILED_REPLY could not be published: no 4th run."""
    redis, entry_id = await _pending()
    engine = _engine()

    await _run(redis, entry_id, engine, delivery=4)

    engine.process_request.assert_not_awaited()
    assert [r.text for r in await _responses(redis)] == [FAILED_REPLY]
    assert await _pending_count(redis) == 0
    [letter] = await _dead_letters(redis)
    assert letter[b"reason"] == b"exhausted"


async def test_an_answered_redelivery_still_runs_the_post_answer_hook() -> None:
    """The ACK failed after a good publish; the hook never ran on that delivery."""
    redis, entry_id = await _pending()
    await redis.set(ANSWERED_KEY.format(_EVENT_ID), "1")
    after_answer = AsyncMock()

    await _run(redis, entry_id, _engine(), delivery=2, after_answer=after_answer)

    after_answer.assert_awaited_once()
    assert await _pending_count(redis) == 0


async def test_a_redis_error_on_the_marker_check_is_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis, entry_id = await _pending()
    engine = _engine()
    monkeypatch.setattr(redis, "exists", AsyncMock(side_effect=ConnectionError("gone")))

    await _run(redis, entry_id, engine, delivery=1)

    engine.process_request.assert_not_awaited()
    assert await _pending_count(redis) == 1


async def test_backoff_counts_from_the_failure_not_the_delivery(
    clock: _Clock,
) -> None:
    """A turn that runs 20 s and then fails must still wait 15 s for its retry."""
    redis, entry_id = await _pending()
    engine = _engine()

    async def _slow_429(_request: object) -> AlfredResponse:
        clock.advance(20_000)
        raise ModelHTTPError(429, "m")

    engine.process_request.side_effect = _slow_429

    await _run(redis, entry_id, engine, delivery=1, now_ms=clock.ms)

    assert await reclaim_due(redis, USER_REQUESTS_STREAM, _GROUP, _CONSUMER) == []
    assert await _times_delivered(redis) == [1]
    clock.advance(15_000)
    [red] = await reclaim_due(redis, USER_REQUESTS_STREAM, _GROUP, _CONSUMER)
    assert red.delivery == 2


async def test_a_failing_after_answer_neither_raises_nor_unacks() -> None:
    redis, entry_id = await _pending()

    await _run(redis, entry_id, _engine(), after_answer=AsyncMock(side_effect=RuntimeError("x")))

    assert await _pending_count(redis) == 0
    assert len(await _responses(redis)) == 1


async def test_a_crash_between_publish_and_ack_does_not_answer_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review Focus 2: XADD lands, XACK fails; the redelivery finds the marker."""
    redis, entry_id = await _pending()
    engine = _engine()
    real_xack = redis.xack
    monkeypatch.setattr(redis, "xack", AsyncMock(side_effect=ConnectionError("gone")))

    await _run(redis, entry_id, engine, delivery=1)

    assert await _pending_count(redis) == 1
    monkeypatch.setattr(redis, "xack", real_xack)
    await _run(redis, entry_id, engine, delivery=2)

    engine.process_request.assert_awaited_once()
    assert len(await _responses(redis)) == 1
    assert await _pending_count(redis) == 0


# ── the side-effect ledger (#304) ────────────────────────────────────────────


async def test_a_redelivery_that_already_acted_answers_from_its_ledger() -> None:
    """2b: a crash mid-turn left effects behind; the redelivery must not run again."""
    redis, entry_id = await _pending()
    text = await _acted(redis)
    engine = _engine()
    after_answer = AsyncMock()

    await _run(redis, entry_id, engine, delivery=2, after_answer=after_answer)

    engine.process_request.assert_not_awaited()
    [reply] = await _responses(redis)
    assert reply.text == text
    assert (reply.channel, reply.session_id) == ("web_pwa", "s1")
    engine.record_partial_turn.assert_awaited_once()
    assert engine.record_partial_turn.await_args.args[1] == text
    assert await redis.exists(ANSWERED_KEY.format(_EVENT_ID))
    assert await _pending_count(redis) == 0
    assert await _dead_letters(redis) == []
    after_answer.assert_not_awaited()


async def test_an_expired_entry_that_already_acted_answers_from_its_ledger() -> None:
    """What was done is still worth telling, past the replay window."""
    now_ms = int(time.time() * 1000)
    redis, entry_id = await _pending(entry_id=f"{now_ms - 301_000}-0")
    text = await _acted(redis)
    engine = _engine()

    await _run(redis, entry_id, engine, delivery=2, now_ms=now_ms)

    engine.process_request.assert_not_awaited()
    assert [r.text for r in await _responses(redis)] == [text]
    assert await _pending_count(redis) == 0


async def test_a_stale_entry_that_already_acted_is_still_dropped_unanswered() -> None:
    now_ms = int(time.time() * 1000)
    redis, entry_id = await _pending(entry_id=f"{now_ms - 3_601_000}-0")
    await _acted(redis)
    engine = _engine()

    await _run(redis, entry_id, engine, delivery=2, now_ms=now_ms)

    assert await _responses(redis) == []
    assert [d[b"reason"] for d in await _dead_letters(redis)] == [b"stale"]


async def test_an_answered_entry_with_a_ledger_only_acks() -> None:
    """The normal answer went out; its ledger outlives it and must not answer again."""
    redis, entry_id = await _pending()
    await _acted(redis)
    await redis.set(ANSWERED_KEY.format(_EVENT_ID), "1")

    await _run(redis, entry_id, _engine(), delivery=2)

    assert await _responses(redis) == []
    assert await _pending_count(redis) == 0


@pytest.mark.parametrize("status", [400, 503])
async def test_a_turn_that_acted_then_failed_answers_from_its_ledger(status: int) -> None:
    """5a: before classification, so neither a permanent nor a transient failure runs
    the turn again or answers FAILED_REPLY."""
    redis, entry_id = await _pending()
    engine = _engine()
    texts: list[str] = []

    async def _act_then_fail(_request: object) -> AlfredResponse:
        texts.append(await _acted(redis, ok=False))
        raise ModelHTTPError(status, "m")

    engine.process_request.side_effect = _act_then_fail

    await _run(redis, entry_id, engine, delivery=1)

    assert [r.text for r in await _responses(redis)] == texts
    assert texts[0].startswith("I ran into a problem partway through, sir.")
    assert await redis.exists(ANSWERED_KEY.format(_EVENT_ID))
    assert await _pending_count(redis) == 0
    [letter] = await _dead_letters(redis)
    assert letter[b"reason"] == b"partial"
    assert letter[b"error_type"] == b"ModelHTTPError"


async def test_a_failed_turn_with_an_empty_ledger_is_retried_as_before() -> None:
    redis, entry_id = await _pending()
    engine = _engine()
    engine.process_request.side_effect = ModelHTTPError(503, "m")

    await _run(redis, entry_id, engine, delivery=1)

    engine.record_partial_turn.assert_not_awaited()
    assert await _responses(redis) == []
    assert await _pending_count(redis) == 1


async def test_a_redis_error_reading_the_ledger_before_running_leaves_it_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis, entry_id = await _pending()
    engine = _engine()
    monkeypatch.setattr(redis, "lrange", AsyncMock(side_effect=ConnectionError("gone")))

    await _run(redis, entry_id, engine, delivery=1)

    engine.process_request.assert_not_awaited()
    assert await _responses(redis) == []
    assert await _pending_count(redis) == 1


async def test_a_redis_error_reading_the_ledger_after_a_failure_leaves_it_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never FAILED_REPLY a turn that may have acted: its redelivery reads the ledger."""
    redis, entry_id = await _pending()
    engine = _engine()

    async def _fail(_request: object) -> AlfredResponse:
        monkeypatch.setattr(redis, "lrange", AsyncMock(side_effect=ConnectionError("gone")))
        raise ModelHTTPError(400, "m")

    engine.process_request.side_effect = _fail

    await _run(redis, entry_id, engine, delivery=3)

    assert await _responses(redis) == []
    assert await _pending_count(redis) == 1
    assert await _dead_letters(redis) == []
