"""Attention gating inside the Reflex runner's state-event processing."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import fakeredis
import pytest
from redis import exceptions as redis_exceptions

from bus.schemas.events import ChangeContext, ReflexProposal
from core.reflex.tests.fakes import decided
from shared.streams import ATTRIBUTION_CAUSES_PREFIX, ATTRIBUTION_CHANGES_STREAM

if TYPE_CHECKING:
    from bus.schemas.events import StateChangedEvent


def _entry(event: StateChangedEvent) -> dict[bytes, bytes]:
    return {b"event": event.model_dump_json().encode()}


@pytest.mark.asyncio
async def test_gated_event_skips_slm_and_returns_false(
    tv_on_event: StateChangedEvent,
) -> None:
    from core.reflex.runner import process_stream_entry

    engine = AsyncMock()
    agent = AsyncMock()
    redis = AsyncMock()
    attention = AsyncMock()
    attention.should_fire = AsyncMock(return_value=False)

    took_action = await process_stream_entry(
        entry_id=b"1-0",
        entry_data=_entry(tv_on_event),
        engine=engine,
        agent=agent,
        redis=redis,
        result_stream="alfred:home:action_results",
        observation_stream="alfred:reflex:observations",
        attention=attention,
    )

    assert took_action is False
    engine.process_event.assert_not_awaited()
    agent.execute_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_attended_event_reaches_engine(tv_on_event: StateChangedEvent) -> None:
    from core.reflex.runner import process_stream_entry

    engine = AsyncMock()
    engine.process_event = AsyncMock(
        return_value=decided(ReflexProposal(decision="none"))
    )  # SLM decides "no action"
    attention = AsyncMock()
    attention.should_fire = AsyncMock(return_value=True)

    took_action = await process_stream_entry(
        entry_id=b"1-0",
        entry_data=_entry(tv_on_event),
        engine=engine,
        agent=AsyncMock(),
        redis=AsyncMock(),
        result_stream="alfred:home:action_results",
        observation_stream="alfred:reflex:observations",
        attention=attention,
    )

    assert took_action is False
    engine.process_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_attention_set_means_no_gating(tv_on_event: StateChangedEvent) -> None:
    """attention=None (default) preserves pre-existing behavior."""
    from core.reflex.runner import process_stream_entry

    engine = AsyncMock()
    engine.process_event = AsyncMock(return_value=decided(ReflexProposal(decision="none")))

    await process_stream_entry(
        entry_id=b"1-0",
        entry_data=_entry(tv_on_event),
        engine=engine,
        agent=AsyncMock(),
        redis=AsyncMock(),
        result_stream="alfred:home:action_results",
        observation_stream="alfred:reflex:observations",
    )
    engine.process_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_gated_events_are_still_counted(tv_on_event: StateChangedEvent) -> None:
    """The attention gate decides what the model sees, not what the house did."""
    from core.reflex.runner import process_stream_entry

    redis = fakeredis.FakeAsyncRedis()
    engine = AsyncMock()
    attention = AsyncMock()
    attention.should_fire = AsyncMock(return_value=False)

    took_action = await process_stream_entry(
        entry_id=b"1-0",
        entry_data=_entry(tv_on_event),
        engine=engine,
        agent=AsyncMock(),
        redis=redis,
        result_stream="alfred:home:action_results",
        observation_stream="alfred:reflex:observations",
        attention=attention,
    )

    assert took_action is False
    engine.process_event.assert_not_awaited()
    (key,) = await redis.keys(f"{ATTRIBUTION_CAUSES_PREFIX}*")  # today's UTC date
    assert await redis.hgetall(key) == {b"unknown": b"1"}


@pytest.mark.asyncio
async def test_a_redelivered_entry_is_counted_once(tv_on_event: StateChangedEvent) -> None:
    """A failed inference leaves the entry un-ACKed; its replay must not count the change again."""
    from core.reflex.runner import process_stream_entry

    redis = fakeredis.FakeAsyncRedis()
    event = tv_on_event.model_copy(update={"context": ChangeContext(id="c1", user_id="u1")})
    engine = AsyncMock()
    engine.process_event = AsyncMock(
        side_effect=[ConnectionError("model down"), decided(ReflexProposal(decision="none"))]
    )

    async def deliver() -> bool:
        return await process_stream_entry(
            entry_id=b"1-0",
            entry_data=_entry(event),
            engine=engine,
            agent=AsyncMock(),
            redis=redis,
            result_stream="alfred:home:action_results",
            observation_stream="alfred:reflex:observations",
        )

    with pytest.raises(ConnectionError):
        await deliver()
    assert await deliver() is False

    assert engine.process_event.await_count == 2
    (key,) = await redis.keys(f"{ATTRIBUTION_CAUSES_PREFIX}*")
    assert await redis.hgetall(key) == {b"person": b"1"}
    assert await redis.xlen(ATTRIBUTION_CHANGES_STREAM) == 1


@pytest.mark.asyncio
async def test_a_failed_counter_write_changes_nothing_downstream(
    tv_on_event: StateChangedEvent, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.reflex.runner import process_stream_entry

    redis = fakeredis.FakeAsyncRedis()
    refusal = redis_exceptions.ResponseError("OOM command not allowed when used memory")
    monkeypatch.setattr(redis, "hincrby", AsyncMock(side_effect=refusal))
    engine = AsyncMock()
    engine.process_event = AsyncMock(return_value=decided(ReflexProposal(decision="none")))

    took_action = await process_stream_entry(
        entry_id=b"1-0",
        entry_data=_entry(tv_on_event),
        engine=engine,
        agent=AsyncMock(),
        redis=redis,
        result_stream="alfred:home:action_results",
        observation_stream="alfred:reflex:observations",
    )

    assert took_action is False
    engine.process_event.assert_awaited_once()
