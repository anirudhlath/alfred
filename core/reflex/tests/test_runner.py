"""Tests for the Reflex Runner orchestration loop."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bus.schemas.events import ActionRequest, StateChangedEvent


@pytest.mark.asyncio
async def test_process_stream_entry_produces_action() -> None:
    """A valid state change event should be processed and produce an action."""
    from core.reflex.runner import process_stream_entry

    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="media_player.living_room_tv",
        old_state="off",
        new_state="on",
        attributes={"friendly_name": "Living Room TV"},
    )
    event_json = event.model_dump_json()

    mock_engine = AsyncMock()
    mock_engine.process_event.return_value = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="smart_home.dim_lights",
        parameters={"room": "living_room", "level": 20},
    )

    mock_agent = AsyncMock()
    mock_agent.execute_action.return_value = MagicMock(
        model_dump_json=MagicMock(return_value='{"status":"success"}')
    )

    mock_redis = AsyncMock()

    result = await process_stream_entry(
        entry_data={"event": event_json},
        engine=mock_engine,
        agent=mock_agent,
        redis=mock_redis,
        result_stream="alfred:home:action_results",
        scratchpad_queue="alfred:scratchpad:queue",
    )

    assert result is True
    mock_engine.process_event.assert_called_once()
    mock_agent.execute_action.assert_called_once()
    mock_redis.xadd.assert_called_once()
    mock_redis.lpush.assert_called_once()


@pytest.mark.asyncio
async def test_process_stream_entry_no_action() -> None:
    """An irrelevant event should not produce an action."""
    from core.reflex.runner import process_stream_entry

    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="sensor.temperature",
        new_state="22.5",
    )

    mock_engine = AsyncMock()
    mock_engine.process_event.return_value = None

    mock_agent = AsyncMock()
    mock_redis = AsyncMock()

    result = await process_stream_entry(
        entry_data={"event": event.model_dump_json()},
        engine=mock_engine,
        agent=mock_agent,
        redis=mock_redis,
        result_stream="alfred:home:action_results",
        scratchpad_queue="alfred:scratchpad:queue",
    )

    assert result is False
    mock_engine.process_event.assert_called_once()
    mock_agent.execute_action.assert_not_called()
    mock_redis.xadd.assert_not_called()


@pytest.mark.asyncio
async def test_process_stream_entry_malformed_event() -> None:
    """A malformed event should be logged and skipped, not crash."""
    from core.reflex.runner import process_stream_entry

    mock_engine = AsyncMock()
    mock_agent = AsyncMock()
    mock_redis = AsyncMock()

    result = await process_stream_entry(
        entry_data={"event": "not valid json {{{"},
        engine=mock_engine,
        agent=mock_agent,
        redis=mock_redis,
        result_stream="alfred:home:action_results",
        scratchpad_queue="alfred:scratchpad:queue",
    )

    assert result is False
    mock_engine.process_event.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_consumer_group_creates_if_missing() -> None:
    """Consumer group creation should be idempotent."""
    from core.reflex.runner import ensure_consumer_group

    mock_redis = AsyncMock()
    mock_redis.xgroup_create = AsyncMock()

    await ensure_consumer_group(mock_redis, "alfred:home:state_changed", "reflex-engine")

    mock_redis.xgroup_create.assert_called_once_with(
        "alfred:home:state_changed", "reflex-engine", id="0", mkstream=True
    )


@pytest.mark.asyncio
async def test_ensure_consumer_group_ignores_exists_error() -> None:
    """If consumer group already exists, should not raise."""
    import redis.asyncio as aioredis

    from core.reflex.runner import ensure_consumer_group

    mock_redis = AsyncMock()
    mock_redis.xgroup_create = AsyncMock(
        side_effect=aioredis.ResponseError("BUSYGROUP Consumer Group name already exists")
    )

    # Should not raise
    await ensure_consumer_group(mock_redis, "alfred:home:state_changed", "reflex-engine")


@pytest.mark.asyncio
async def test_process_stream_entry_handles_bytes_keys() -> None:
    """Redis returns bytes keys — verify they're handled."""
    from core.reflex.runner import process_stream_entry

    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="sensor.temperature",
        new_state="22.5",
    )

    mock_engine = AsyncMock()
    mock_engine.process_event.return_value = None
    mock_agent = AsyncMock()
    mock_redis = AsyncMock()

    result = await process_stream_entry(
        entry_data={b"event": event.model_dump_json().encode()},
        engine=mock_engine,
        agent=mock_agent,
        redis=mock_redis,
        result_stream="alfred:home:action_results",
        scratchpad_queue="alfred:scratchpad:queue",
    )

    assert result is False
    mock_engine.process_event.assert_called_once()
