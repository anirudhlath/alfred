"""The Reflex decision log: every decision that reached the model, kept with its prompt."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from redis import exceptions as redis_exceptions

from bus.schemas.events import (
    ChangeContext,
    ReflexProposal,
    StateChangedEvent,
    TriggerFired,
)
from core.reflex import decision_log
from core.reflex.decision_log import DecisionLog, DecisionRecord
from shared.streams import REFLEX_DECISION_LOG_STREAM

if TYPE_CHECKING:
    from shared.types import AioRedis

_NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
_EPOCH = datetime(2026, 10, 1, tzinfo=UTC)


def _change(context: ChangeContext | None) -> StateChangedEvent:
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="light.bedroom_lamp",
        old_state="on",
        new_state="off",
        context=context,
        timestamp=datetime(2026, 10, 24, 11, 59, tzinfo=UTC),
    )


def _trigger() -> TriggerFired:
    return TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")


_PROPOSAL = ReflexProposal(decision="none", reason="nothing to do")


async def test_record_stores_prompt_cause_and_proposal() -> None:
    redis = fakeredis.FakeAsyncRedis()
    log = DecisionLog(redis, clock=lambda: _NOW)
    event = _change(ChangeContext(user_id="u"))

    await log.record("state_change", event, "the prompt", _PROPOSAL)

    records = await log.read(since=_EPOCH)
    assert records == [
        DecisionRecord(
            time=_NOW,
            origin="state_change",
            event=event.model_dump(mode="json"),
            cause="person",
            prompt="the prompt",
            proposal=_PROPOSAL,
        )
    ]
    assert records[0].event["entity_id"] == "light.bedroom_lamp"


async def test_trigger_decisions_have_no_cause() -> None:
    redis = fakeredis.FakeAsyncRedis()
    log = DecisionLog(redis, clock=lambda: _NOW)

    await log.record("trigger_fired", _trigger(), "the prompt", _PROPOSAL)

    (record,) = await log.read(since=_EPOCH)
    assert record.origin == "trigger_fired"
    assert record.cause is None
    assert record.event["trigger_name"] == "bedtime"


async def test_a_state_change_without_a_context_has_no_cause() -> None:
    redis = fakeredis.FakeAsyncRedis()
    log = DecisionLog(redis, clock=lambda: _NOW)

    await log.record("state_change", _change(None), "the prompt", _PROPOSAL)

    (record,) = await log.read(since=_EPOCH)
    assert record.cause is None


async def test_read_since_filters_on_the_records_own_time() -> None:
    redis = fakeredis.FakeAsyncRedis()
    older = DecisionLog(redis, clock=lambda: _NOW - timedelta(days=2))
    newer = DecisionLog(redis, clock=lambda: _NOW)
    await older.record("state_change", _change(None), "old prompt", _PROPOSAL)
    await newer.record("state_change", _change(None), "new prompt", _PROPOSAL)

    records = await newer.read(since=_NOW - timedelta(days=1))

    assert [r.prompt for r in records] == ["new prompt"]


async def test_read_skips_a_row_it_cannot_parse() -> None:
    redis = fakeredis.FakeAsyncRedis()
    log = DecisionLog(redis, clock=lambda: _NOW)
    await log.record("state_change", _change(None), "first", _PROPOSAL)
    await redis.xadd(REFLEX_DECISION_LOG_STREAM, {"decision": "{not json"})
    await redis.xadd(REFLEX_DECISION_LOG_STREAM, {"other": "field"})
    await log.record("state_change", _change(None), "last", _PROPOSAL)

    records = await log.read(since=_EPOCH)

    assert [r.prompt for r in records] == ["first", "last"]


async def test_record_keeps_the_stream_within_its_age_bound() -> None:
    redis = fakeredis.FakeAsyncRedis()
    # An entry from 40 days ago: its stream id is old enough to be trimmed by the next write.
    stale_ms = int((_NOW - timedelta(days=40)).timestamp() * 1000)
    await redis.xadd(REFLEX_DECISION_LOG_STREAM, {"decision": "{}"}, id=f"{stale_ms}-0")
    log = DecisionLog(redis, clock=lambda: _NOW)

    await log.record("state_change", _change(None), "fresh", _PROPOSAL)

    assert await redis.xlen(REFLEX_DECISION_LOG_STREAM) == 1


async def test_record_never_raises_when_redis_refuses() -> None:
    refusal = redis_exceptions.ResponseError(
        "OOM command not allowed when used memory > 'maxmemory'"
    )
    redis = MagicMock()
    redis.pipeline = MagicMock(side_effect=refusal)
    redis.xadd = AsyncMock(side_effect=refusal)
    log = DecisionLog(redis, clock=lambda: _NOW)

    await log.record("state_change", _change(ChangeContext(user_id="u")), "p", _PROPOSAL)
    await log.record("trigger_fired", _trigger(), "p", _PROPOSAL)


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


async def _add(
    redis: AioRedis, prompt: str, *, time: datetime, entry_at: datetime | None = None
) -> None:
    """One entry whose stream id leads with *entry_at* (default: its own time)."""
    record = DecisionRecord(
        time=time,
        origin="state_change",
        event=_change(None).model_dump(mode="json"),
        cause=None,
        prompt=prompt,
        proposal=_PROPOSAL,
    )
    entry_ms = _ms(entry_at or time)
    await redis.xadd(
        REFLEX_DECISION_LOG_STREAM, {"decision": record.model_dump_json()}, id=f"{entry_ms}-0"
    )


async def test_read_refuses_a_naive_since() -> None:
    log = DecisionLog(fakeredis.FakeAsyncRedis())

    with pytest.raises(ValueError, match="since"):
        await log.read(since=datetime(2026, 10, 24, 12, 0))


async def test_read_refuses_a_naive_until() -> None:
    log = DecisionLog(fakeredis.FakeAsyncRedis())

    with pytest.raises(ValueError, match="until"):
        await log.read(since=_EPOCH, until=datetime(2026, 10, 24, 12, 0))


async def test_read_starts_at_since() -> None:
    redis = fakeredis.FakeAsyncRedis()
    since = _NOW - timedelta(hours=1)
    await _add(redis, "before", time=since - timedelta(minutes=1))
    await _add(redis, "at", time=since)
    await _add(redis, "after", time=since + timedelta(minutes=1))

    records = await DecisionLog(redis).read(since=since)

    assert [r.prompt for r in records] == ["at", "after"]


async def test_read_asks_redis_only_for_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = fakeredis.FakeAsyncRedis()
    asked: list[dict[str, Any]] = []
    real = decision_log.forward_range

    async def spy(*args: Any, **kwargs: Any) -> Any:
        asked.append(kwargs)
        return await real(*args, **kwargs)

    monkeypatch.setattr(decision_log, "forward_range", spy)
    since = _NOW - timedelta(hours=1)
    log = DecisionLog(redis)

    await log.read(since=since)
    await log.read(since=since, until=_NOW)

    assert [(a["min_id"], a["max_id"]) for a in asked] == [
        (str(_ms(since)), "+"),
        (str(_ms(since)), str(_ms(_NOW) + decision_log.UNTIL_SLACK_MS)),
    ]


async def test_read_stops_before_until() -> None:
    redis = fakeredis.FakeAsyncRedis()
    until = _NOW
    await _add(redis, "inside", time=until - timedelta(minutes=1))
    await _add(redis, "at until", time=until)
    await _add(redis, "outside", time=until + timedelta(minutes=1))

    records = await DecisionLog(redis).read(since=_EPOCH, until=until)

    assert [r.prompt for r in records] == ["inside"]


async def test_read_keeps_a_record_made_before_until_but_written_after_it() -> None:
    """``record()`` stamps the time before the XADD, so the stream id can lead it past until."""
    redis = fakeredis.FakeAsyncRedis()
    until = _NOW
    slack = timedelta(milliseconds=decision_log.UNTIL_SLACK_MS)
    await _add(
        redis,
        "decided before, written after",
        time=until - timedelta(milliseconds=1),
        entry_at=until + timedelta(milliseconds=1),
    )
    await _add(redis, "decided at until", time=until, entry_at=until + timedelta(milliseconds=2))
    await _add(
        redis,
        "decided before, written at the slack",
        time=until - timedelta(milliseconds=1),
        entry_at=until + slack,
    )

    records = await DecisionLog(redis).read(since=_EPOCH, until=until)

    assert [r.prompt for r in records] == [
        "decided before, written after",
        "decided before, written at the slack",
    ]


async def test_read_still_filters_on_a_records_own_time_inside_the_window() -> None:
    redis = fakeredis.FakeAsyncRedis()
    since = _NOW
    # Decided a hair before `since`, written (and so id'd) a hair after it.
    await _add(
        redis,
        "decided before",
        time=since - timedelta(milliseconds=1),
        entry_at=since + timedelta(milliseconds=5),
    )
    await _add(redis, "decided after", time=since + timedelta(milliseconds=10))

    records = await DecisionLog(redis).read(since=since)

    assert [r.prompt for r in records] == ["decided after"]
