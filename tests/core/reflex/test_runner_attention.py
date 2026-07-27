"""Attention gating inside the Reflex runner's state-event processing."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

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
    engine.process_event = AsyncMock(return_value=None)  # SLM decides "no action"
    attention = AsyncMock()
    attention.should_fire = AsyncMock(return_value=True)

    took_action = await process_stream_entry(
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
    engine.process_event = AsyncMock(return_value=None)

    await process_stream_entry(
        entry_data=_entry(tv_on_event),
        engine=engine,
        agent=AsyncMock(),
        redis=AsyncMock(),
        result_stream="alfred:home:action_results",
        observation_stream="alfred:reflex:observations",
    )
    engine.process_event.assert_awaited_once()
