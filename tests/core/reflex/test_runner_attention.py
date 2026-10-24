"""Attention gating inside the Reflex runner's state-event processing."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import fakeredis
import pytest

from bus.schemas.events import ReflexProposal
from shared.streams import ATTRIBUTION_CAUSES_PREFIX

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
        return_value=ReflexProposal(decision="none")
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
    engine.process_event = AsyncMock(return_value=ReflexProposal(decision="none"))

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
