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
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import fakeredis
from pydantic_ai.exceptions import ModelHTTPError

from bus.schemas.events import AlfredResponse
from core.conscious.engine import ConsciousEngine
from core.conscious.failures import FAILED_REPLY
from core.conscious.runner import process_request_entry
from shared.redis_streams import forward_range
from shared.streams import (
    ANSWERED_KEY,
    DEAD_LETTER_STREAM,
    USER_REQUESTS_STREAM,
    USER_RESPONSES_STREAM,
)

if TYPE_CHECKING:
    import pytest

_EVENT_ID = "11111111-1111-1111-1111-111111111111"
_REQUEST = (
    f'{{"event_id":"{_EVENT_ID}","event_type":"user_request",'
    '"timestamp":"2026-09-03T03:00:00Z","source":"web","channel":"web_pwa",'
    '"session_id":"s1","identity_claim":"sir","content_type":"text",'
    '"content":"turn off the lights"}'
)
_GROUP = "conscious-engine"


def _engine() -> AsyncMock:
    engine = AsyncMock()
    engine.process_request = AsyncMock(
        return_value=AlfredResponse(
            source="conscious-engine", channel="web_pwa", session_id="s1", text="Done, sir."
        )
    )
    engine.failure_reply = ConsciousEngine.failure_reply
    return engine


async def _pending(
    fields: dict[Any, Any] | None = None, *, entry_id: str = "*"
) -> tuple[fakeredis.FakeAsyncRedis, bytes | str]:
    """A request read by the group and not yet ACKed — the state the runner sees."""
    redis = fakeredis.FakeAsyncRedis()
    await redis.xgroup_create(USER_REQUESTS_STREAM, _GROUP, id="0", mkstream=True)
    added = await redis.xadd(USER_REQUESTS_STREAM, fields or {"event": _REQUEST}, id=entry_id)
    await redis.xreadgroup(_GROUP, "worker-1", {USER_REQUESTS_STREAM: ">"})
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
        **kw,
    )


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


async def test_an_old_first_delivery_still_runs() -> None:
    """The window bounds *re*deliveries; a live read is answered whatever its id."""
    now_ms = int(time.time() * 1000)
    redis, entry_id = await _pending(entry_id=f"{now_ms - 301_000}-0")
    engine = _engine()

    await _run(redis, entry_id, engine, delivery=1, now_ms=now_ms)

    engine.process_request.assert_awaited_once()


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
