"""The conscious loop's second stream: the owner's decision from the Door (#317 PR 5).

A decision resumes a parked run as a turn of its own, keyed on its approval id: the same
answered marker, side-effect ledger, delivery cap and dead letters as a user's request,
and every answer it gets, a failure's included, is a follow-up to the request that parked
the run.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import fakeredis
import pytest
from pydantic_ai.exceptions import ModelHTTPError

from bus.schemas.events import (
    AlfredResponse,
    ApprovalDecision,
    ParkedCall,
    ParkedRun,
    UserRequest,
)
from core.conscious.effects import EffectLedger, ledger_reply
from core.conscious.engine import ConsciousEngine
from core.conscious.failures import FAILED_REPLY
from core.conscious.runner import process_approval_entry
from shared.redis_streams import entry_ms, forward_range
from shared.streams import (
    ANSWERED_KEY,
    APPROVALS_STREAM,
    DEAD_LETTER_STREAM,
    USER_REQUESTS_STREAM,
    USER_RESPONSES_STREAM,
)
from tests.core.conscious.helpers import identity

_REQUEST_ID = "11111111-1111-4111-8111-111111111111"
_APPROVAL_ID = "22222222-2222-5222-8222-222222222222"
_GROUP = "conscious-engine"
_CONSUMER = "worker-1"
_RUN = ParkedRun(
    approval_id=_APPROVAL_ID,
    session_id="s1",
    channel="web_pwa",
    identity=identity("sir").model_dump(mode="json"),
    request_event_id=_REQUEST_ID,
    created_at=datetime(2026, 10, 19, 12, 0, tzinfo=UTC),
    content_type="text",
    tz_name="UTC",
    summary="The dog walker is here",
    calls=[
        ParkedCall(
            tool_call_id="call-lock",
            tool_name="home.lock_unlock",
            target_service="home-service",
            parameters={"entity_id": "lock.front"},
            reason="The dog walker is here.",
        )
    ],
    messages="[]",
)
_DECISION = ApprovalDecision(
    source="channels", approval_id=_APPROVAL_ID, decision="approve", run=_RUN
)
_UNLOCKED = "The door is unlocked, sir."


def _resumed() -> AlfredResponse:
    return AlfredResponse(
        source="conscious-engine",
        channel="web_pwa",
        session_id="s1",
        text=_UNLOCKED,
        actions_taken=["home.lock_unlock"],
        in_reply_to=_REQUEST_ID,
        followup=True,
    )


def _engine() -> AsyncMock:
    engine = AsyncMock()
    engine.resume_approval = AsyncMock(return_value=_resumed())
    engine.approval_failure_reply = ConsciousEngine.approval_failure_reply
    engine.record_partial_approval = AsyncMock()
    return engine


async def _pending(fields: dict[Any, Any] | None = None) -> tuple[fakeredis.FakeAsyncRedis, bytes]:
    """A decision read by the group and not yet ACKed: the state the runner sees."""
    redis = fakeredis.FakeAsyncRedis()
    await redis.xgroup_create(APPROVALS_STREAM, _GROUP, id="0", mkstream=True)
    entry_id: bytes = await redis.xadd(
        APPROVALS_STREAM, fields or {"event": _DECISION.model_dump_json()}
    )
    await redis.xreadgroup(_GROUP, _CONSUMER, {APPROVALS_STREAM: ">"})
    return redis, entry_id


async def _deliver(
    redis: fakeredis.FakeAsyncRedis, entry_id: bytes, engine: Any, **kw: Any
) -> None:
    eid = entry_id.decode()
    [(_id, fields)] = await forward_range(redis, APPROVALS_STREAM, min_id=eid, max_id=eid)
    await process_approval_entry(
        entry_id,
        fields,
        engine=engine,
        redis=redis,
        stream=APPROVALS_STREAM,
        group=_GROUP,
        consumer=_CONSUMER,
        **kw,
    )


async def _responses(redis: fakeredis.FakeAsyncRedis) -> list[AlfredResponse]:
    return [
        AlfredResponse.model_validate_json(fields[b"event"])
        for _id, fields in await forward_range(redis, USER_RESPONSES_STREAM)
    ]


async def _pending_count(redis: fakeredis.FakeAsyncRedis, stream: str = APPROVALS_STREAM) -> int:
    return int((await redis.xpending(stream, _GROUP))["pending"])


async def _dead_letters(redis: fakeredis.FakeAsyncRedis) -> list[dict[bytes | str, Any]]:
    return [fields for _id, fields in await forward_range(redis, DEAD_LETTER_STREAM)]


async def test_a_decision_is_answered_once_as_a_followup() -> None:
    redis, entry_id = await _pending()
    engine = _engine()
    after_answer = AsyncMock()

    await _deliver(redis, entry_id, engine, after_answer=after_answer)
    # A delivery after the answer (its ACK lost): the marker says it was answered.
    await _deliver(redis, entry_id, engine, delivery=2)

    engine.resume_approval.assert_awaited_once_with(_DECISION, received_ms=entry_ms(entry_id))
    [reply] = await _responses(redis)
    assert reply.text == _UNLOCKED
    assert reply.followup is True
    assert reply.in_reply_to == _RUN.request_event_id
    assert await redis.exists(ANSWERED_KEY.format(_APPROVAL_ID))
    assert await _pending_count(redis) == 0
    after_answer.assert_awaited_once()


async def test_a_redelivered_decision_after_acting_answers_from_the_ledger() -> None:
    """The approved call ran and the delivery died before it answered: the redelivery
    must never run the approval again (#304)."""
    redis, entry_id = await _pending()
    ledger = EffectLedger(redis)
    index = await ledger.started(_APPROVAL_ID, "home.lock_unlock", {"entity_id": "lock.front"})
    await ledger.finished(_APPROVAL_ID, index, ok=True)
    text = ledger_reply(await ledger.read(_APPROVAL_ID))
    engine = _engine()

    await _deliver(redis, entry_id, engine, delivery=2)

    engine.resume_approval.assert_not_awaited()
    [reply] = await _responses(redis)
    assert reply.text == text
    assert (reply.followup, reply.in_reply_to, reply.mood) == (True, _REQUEST_ID, "concerned")
    assert (reply.channel, reply.session_id) == ("web_pwa", "s1")
    engine.record_partial_approval.assert_awaited_once_with(_DECISION, text)
    assert await redis.exists(ANSWERED_KEY.format(_APPROVAL_ID))
    assert await _pending_count(redis) == 0
    [letter] = await _dead_letters(redis)
    assert letter[b"reason"] == b"partial"


async def test_a_failed_resume_is_retried() -> None:
    redis, entry_id = await _pending()
    engine = _engine()
    engine.resume_approval.side_effect = ModelHTTPError(503, "m")

    await _deliver(redis, entry_id, engine, delivery=1)

    assert await _responses(redis) == []
    assert await _pending_count(redis) == 1


async def test_an_exhausted_decision_gives_up_once() -> None:
    redis, entry_id = await _pending()
    engine = _engine()

    await _deliver(redis, entry_id, engine, delivery=4)

    engine.resume_approval.assert_not_awaited()
    [reply] = await _responses(redis)
    assert reply.text == FAILED_REPLY
    assert (reply.followup, reply.in_reply_to, reply.mood) == (True, _REQUEST_ID, "concerned")
    assert reply.actions_taken == []
    assert await _pending_count(redis) == 0
    [letter] = await _dead_letters(redis)
    assert letter[b"reason"] == b"exhausted"


@pytest.mark.parametrize(
    "fields",
    [{"junk": "x"}, {"event": '{"event_type": "approval_decision", "decision": "maybe"}'}],
)
async def test_an_unparseable_decision_is_dropped(fields: dict[str, str]) -> None:
    """It must leave the pending list, or it is reclaimed forever."""
    redis, entry_id = await _pending(fields)
    engine = _engine()

    await _deliver(redis, entry_id, engine)

    engine.resume_approval.assert_not_awaited()
    assert await _responses(redis) == []
    assert await _pending_count(redis) == 0


# ── the conscious loop reads both streams ────────────────────────────────────


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


def _user_request() -> UserRequest:
    return UserRequest(
        source="web-pwa",
        channel="web_pwa",
        session_id="s2",
        identity_claim="sir",
        content_type="text",
        content="what's the weather?",
    )


async def test_the_loop_answers_each_stream_with_its_own_runner(clock: _Clock) -> None:
    """One read covers requests and decisions, and a decision left pending comes back
    through its own stream's reclaim, processed as a decision."""
    import core.conscious.__main__ as conscious_main

    redis = fakeredis.FakeAsyncRedis()
    for stream in (USER_REQUESTS_STREAM, APPROVALS_STREAM):
        await redis.xgroup_create(stream, _GROUP, id="0", mkstream=True)
    await redis.xadd(USER_REQUESTS_STREAM, {"event": _user_request().model_dump_json()})
    await redis.xadd(APPROVALS_STREAM, {"event": _DECISION.model_dump_json()})
    engine = _engine()
    engine.failure_reply = ConsciousEngine.failure_reply
    engine.process_request = AsyncMock(
        return_value=AlfredResponse(
            source="conscious-engine", channel="web_pwa", session_id="s2", text="Sunny, sir."
        )
    )
    engine.resume_approval.side_effect = [ModelHTTPError(503, "m"), _resumed()]

    await conscious_main._serve_once(
        redis, engine, group=_GROUP, consumer=_CONSUMER, after_answer=None
    )

    engine.process_request.assert_awaited_once()
    engine.resume_approval.assert_awaited_once()
    assert [r.text for r in await _responses(redis)] == ["Sunny, sir."]
    assert await _pending_count(redis) == 1

    clock.advance(15_000)
    # The next request arrives, so the read returns at once; the decision is due.
    await redis.xadd(USER_REQUESTS_STREAM, {"event": _user_request().model_dump_json()})
    await conscious_main._serve_once(
        redis, engine, group=_GROUP, consumer=_CONSUMER, after_answer=None
    )

    assert engine.resume_approval.await_count == 2
    assert [r.text for r in await _responses(redis)] == ["Sunny, sir.", "Sunny, sir.", _UNLOCKED]
    assert await _pending_count(redis) == 0
    assert await _pending_count(redis, USER_REQUESTS_STREAM) == 0
