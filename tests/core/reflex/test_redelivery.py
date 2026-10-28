"""Reflex decides each event once, however often its entry is delivered.

An entry whose handling fails is left pending and replayed by the reclaim pass, and a crash
between the decision and the ACK replays it too. The model must not be asked twice about the
same event, so the runner marks an event decided once the decision is counted and published
(``already_decided`` / ``mark_decided``), and a failure before that leaves it unmarked.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from redis import exceptions as redis_exceptions

from bus.schemas.events import StateChangedEvent, TriggerFired
from core.reflex import runner
from core.reflex.__main__ import _handle_trigger_fired
from core.reflex.engine import ReflexEngine
from core.reflex.runner import (
    already_decided,
    decisions_key,
    mark_decided,
    process_stream_entry,
)
from core.reflex.tool_registry import ToolInfo
from shared.redis_streams import REPLAY_MARKER_TTL_SECONDS
from shared.streams import REFLEX_DECIDED_PREFIX, REFLEX_OBSERVATIONS_STREAM
from tests.core.reflex.fakes import reflex_agent

if TYPE_CHECKING:
    from datetime import date

    from shared.types import AioRedis
    from tests.llm_fakes import Reply, Script

NONE = '{"decision": "none"}'
RESULT_STREAM = "alfred:home:action_results"


def _lamp(new_state: str = "on") -> StateChangedEvent:
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="light.living_room_lamp",
        old_state="off" if new_state == "on" else "on",
        new_state=new_state,
    )


def _trigger() -> TriggerFired:
    return TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")


LAMP_ON = ToolInfo(
    name="home.light_turn_on",
    description="light.turn_on",
    feature_name="home",
    feature_description="",
    target_service="home-service",
    audience="reflex",
    input_schema={
        "type": "object",
        "properties": {"target": {"type": "string"}},
        "required": ["target"],
    },
)


def _engine(*replies: Reply) -> tuple[ReflexEngine, Script]:
    registry = AsyncMock()
    registry.get_tools = AsyncMock(return_value=[LAMP_ON])
    agent, script = reflex_agent(*replies)
    engine = ReflexEngine(preferences_dir="/unused", tool_registry=registry, agent=agent)
    engine._cached_preferences = ""
    return engine, script


async def _deliver(redis: AioRedis, engine: ReflexEngine, event: StateChangedEvent) -> bool:
    """One delivery of the event's stream entry (always the same entry id)."""
    return await process_stream_entry(
        entry_id=b"1760000000000-0",
        entry_data={b"event": event.model_dump_json().encode()},
        engine=engine,
        agent=AsyncMock(),
        redis=redis,
        result_stream=RESULT_STREAM,
        observation_stream=REFLEX_OBSERVATIONS_STREAM,
    )


async def _decisions_today(redis: AioRedis) -> dict[str, int]:
    from datetime import UTC, datetime

    today: date = datetime.now(UTC).date()
    counted = await redis.hgetall(decisions_key(today))
    return {k.decode(): int(v) for k, v in counted.items()}


# --- the marker ---------------------------------------------------------------------------


async def test_a_marked_event_is_decided_and_an_unmarked_one_is_not() -> None:
    redis = fakeredis.FakeAsyncRedis()

    assert await already_decided(redis, "e-1") is False

    await mark_decided(redis, "e-1")

    assert await already_decided(redis, "e-1") is True
    assert await already_decided(redis, "e-2") is False
    assert 0 < await redis.ttl(f"{REFLEX_DECIDED_PREFIX}e-1") <= REPLAY_MARKER_TTL_SECONDS


async def test_marking_never_raises_when_redis_refuses() -> None:
    redis = MagicMock()
    redis.set = AsyncMock(
        side_effect=redis_exceptions.ResponseError("OOM command not allowed when used memory")
    )

    await mark_decided(redis, "e-1")


# --- state changes ------------------------------------------------------------------------


async def test_a_redelivered_event_is_decided_once() -> None:
    redis = fakeredis.FakeAsyncRedis()
    engine, script = _engine(NONE)
    event = _lamp()

    first = await _deliver(redis, engine, event)
    second = await _deliver(redis, engine, event)

    assert (first, second) == (False, False)
    assert script.call_count == 1
    assert await _decisions_today(redis) == {"none": 1}


async def test_a_redelivery_is_logged_at_info(caplog: pytest.LogCaptureFixture) -> None:
    redis = fakeredis.FakeAsyncRedis()
    engine, _ = _engine(NONE)
    event = _lamp()
    await _deliver(redis, engine, event)

    with caplog.at_level(logging.INFO, logger=runner.logger.name):
        await _deliver(redis, engine, event)

    assert [r.levelno for r in caplog.records if event.event_id in r.getMessage()] == [logging.INFO]


async def test_distinct_events_are_each_decided() -> None:
    redis = fakeredis.FakeAsyncRedis()
    engine, script = _engine(NONE, NONE)

    await _deliver(redis, engine, _lamp("on"))
    await _deliver(redis, engine, _lamp("on"))  # a new event: its own event_id

    assert script.call_count == 2


async def test_a_failed_decision_is_not_marked() -> None:
    redis = fakeredis.FakeAsyncRedis()
    engine, script = _engine(RuntimeError("model down"), NONE)
    event = _lamp()

    with pytest.raises(RuntimeError, match="model down"):
        await _deliver(redis, engine, event)
    assert await already_decided(redis, event.event_id) is False

    await _deliver(redis, engine, event)

    assert script.call_count == 2
    assert await already_decided(redis, event.event_id) is True


async def test_a_crash_before_marking_decides_again(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = fakeredis.FakeAsyncRedis()
    engine, script = _engine(NONE, NONE)
    event = _lamp()
    # The process dies after the decision is counted, while its outcome is being recorded.
    with monkeypatch.context() as crash:
        crash.setattr(runner, "observe_passively", AsyncMock(side_effect=asyncio.CancelledError))
        with pytest.raises(asyncio.CancelledError):
            await _deliver(redis, engine, event)
    assert await already_decided(redis, event.event_id) is False

    await _deliver(redis, engine, event)  # the reclaim pass replays the entry

    assert script.call_count == 2
    assert await already_decided(redis, event.event_id) is True


async def test_a_proposal_is_marked_after_it_is_published() -> None:
    redis = fakeredis.FakeAsyncRedis()
    reply = (
        '{"decision": "ask", "tool_name": "home.light_turn_on", '
        '"parameters": {"target": "Lamp"}, "reason": "dark"}'
    )
    engine, script = _engine(reply, reply)
    event = _lamp()

    await _deliver(redis, engine, event)
    await _deliver(redis, engine, event)

    assert script.call_count == 1
    assert await _decisions_today(redis) == {"ask": 1}
    assert await redis.xlen(REFLEX_OBSERVATIONS_STREAM) == 1


def _refuse_exists(redis: AioRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    """The decided-check's read fails: Redis cannot say whether the event was decided."""
    monkeypatch.setattr(
        redis, "exists", AsyncMock(side_effect=redis_exceptions.ConnectionError("redis gone"))
    )


async def test_a_failing_decided_check_leaves_the_entry_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raised, so the loop does not ACK; guessing "not decided" could decide the event twice."""
    redis = fakeredis.FakeAsyncRedis()
    engine, script = _engine(NONE)
    _refuse_exists(redis, monkeypatch)

    with pytest.raises(redis_exceptions.ConnectionError, match="redis gone"):
        await _deliver(redis, engine, _lamp())

    assert script.call_count == 0
    assert await _decisions_today(redis) == {}


async def test_a_refused_marker_write_does_not_fail_the_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = fakeredis.FakeAsyncRedis()
    engine, _ = _engine(NONE)
    real_set = redis.set

    async def refuse_the_marker(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith(REFLEX_DECIDED_PREFIX):
            raise redis_exceptions.ResponseError("OOM command not allowed")
        return await real_set(name, *args, **kwargs)

    monkeypatch.setattr(redis, "set", refuse_the_marker)

    assert await _deliver(redis, engine, _lamp()) is False


# --- fired triggers -----------------------------------------------------------------------


def _publisher() -> AsyncMock:
    publisher = AsyncMock()
    publisher.publish = AsyncMock()
    return publisher


async def _fire(
    redis: AioRedis, engine: ReflexEngine, publisher: AsyncMock, event: TriggerFired
) -> None:
    await _handle_trigger_fired(
        {"event": event.model_dump_json()}, engine, AsyncMock(), redis, publisher
    )


async def test_trigger_fired_is_decided_once() -> None:
    redis = fakeredis.FakeAsyncRedis()
    engine, script = _engine(NONE)
    publisher = _publisher()
    event = _trigger()

    await _fire(redis, engine, publisher, event)
    await _fire(redis, engine, publisher, event)

    assert script.call_count == 1
    assert await _decisions_today(redis) == {"none": 1}
    # The notification went out before the decision, so a replay does not repeat it.
    assert publisher.publish.await_count == 1


async def test_a_trigger_decision_that_failed_is_not_marked() -> None:
    redis = fakeredis.FakeAsyncRedis()
    engine, script = _engine(RuntimeError("model down"), NONE)
    publisher = _publisher()
    event = _trigger()

    await _fire(redis, engine, publisher, event)  # a failed inference is logged, not raised
    assert await already_decided(redis, event.event_id) is False

    await _fire(redis, engine, publisher, event)

    assert script.call_count == 2
    assert await already_decided(redis, event.event_id) is True


async def test_a_failing_decided_check_leaves_the_trigger_entry_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = fakeredis.FakeAsyncRedis()
    engine, script = _engine(NONE)
    publisher = _publisher()
    _refuse_exists(redis, monkeypatch)

    with pytest.raises(redis_exceptions.ConnectionError, match="redis gone"):
        await _fire(redis, engine, publisher, _trigger())

    assert script.call_count == 0
    publisher.publish.assert_not_awaited()  # nothing reaches the owner before the check
