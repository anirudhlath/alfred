"""Integration tests for the TriggerFired consumer in the Reflex Runner."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from bus.schemas.events import TriggerFired
from core.notifications.schema import Urgency


@pytest.fixture
def mock_publisher() -> AsyncMock:
    publisher = AsyncMock()
    publisher.publish = AsyncMock()
    return publisher


@pytest.fixture
def mock_engine() -> AsyncMock:
    engine = AsyncMock()
    engine.process_trigger_fired = AsyncMock(return_value=None)
    return engine


@pytest.fixture
def mock_agent() -> AsyncMock:
    return AsyncMock()


def _make_entry_data(event: TriggerFired) -> dict[str | bytes, str | bytes]:
    return {"event": event.model_dump_json()}


@pytest.mark.asyncio
async def test_handle_trigger_fired_publishes_notification(
    mock_engine: AsyncMock,
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    from core.reflex.__main__ import _handle_trigger_fired

    event = TriggerFired(
        trigger_id="t-1",
        trigger_name="take medicine",
        trigger_type="time",
        urgency="important",
    )
    redis = AsyncMock()

    await _handle_trigger_fired(
        _make_entry_data(event),
        mock_engine,
        mock_agent,
        redis,
        mock_publisher,
    )

    mock_publisher.publish.assert_called_once()
    call_kwargs = mock_publisher.publish.call_args
    assert call_kwargs.kwargs["urgency"] == Urgency.IMPORTANT
    assert "Trigger:" in call_kwargs.kwargs["title"]
    assert "take medicine" in call_kwargs.kwargs["title"]


@pytest.mark.asyncio
async def test_handle_trigger_fired_sensor_uses_alert_title(
    mock_engine: AsyncMock,
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    from core.reflex.__main__ import _handle_trigger_fired

    event = TriggerFired(
        trigger_id="t-1",
        trigger_name="humidity high",
        trigger_type="sensor",
    )
    redis = AsyncMock()

    await _handle_trigger_fired(
        _make_entry_data(event),
        mock_engine,
        mock_agent,
        redis,
        mock_publisher,
    )

    call_kwargs = mock_publisher.publish.call_args
    assert "Trigger:" in call_kwargs.kwargs["title"]
    assert "humidity high" in call_kwargs.kwargs["title"]


@pytest.mark.asyncio
async def test_handle_trigger_fired_calls_slm(
    mock_engine: AsyncMock,
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    from core.reflex.__main__ import _handle_trigger_fired

    event = TriggerFired(
        trigger_id="t-1",
        trigger_name="test",
        trigger_type="time",
    )
    redis = AsyncMock()

    await _handle_trigger_fired(
        _make_entry_data(event),
        mock_engine,
        mock_agent,
        redis,
        mock_publisher,
    )

    mock_engine.process_trigger_fired.assert_called_once()


@pytest.mark.asyncio
async def test_handle_trigger_fired_slm_failure_does_not_block_notification(
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    from core.reflex.__main__ import _handle_trigger_fired

    engine = AsyncMock()
    engine.process_trigger_fired = AsyncMock(side_effect=RuntimeError("Ollama down"))

    event = TriggerFired(
        trigger_id="t-1",
        trigger_name="test",
        trigger_type="time",
    )
    redis = AsyncMock()

    # Should NOT raise — SLM error is caught, notification already sent
    await _handle_trigger_fired(
        _make_entry_data(event),
        engine,
        mock_agent,
        redis,
        mock_publisher,
    )

    mock_publisher.publish.assert_called_once()


@pytest.mark.asyncio
async def test_handle_trigger_fired_skips_non_trigger_events(
    mock_engine: AsyncMock,
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    from core.reflex.__main__ import _handle_trigger_fired

    entry_data: dict[str | bytes, str | bytes] = {
        "event": json.dumps({"event_type": "state_changed", "source": "test"})
    }
    redis = AsyncMock()

    await _handle_trigger_fired(
        entry_data,
        mock_engine,
        mock_agent,
        redis,
        mock_publisher,
    )

    mock_publisher.publish.assert_not_called()
    mock_engine.process_trigger_fired.assert_not_called()


@pytest.mark.asyncio
async def test_handle_trigger_fired_skips_missing_event_field(
    mock_engine: AsyncMock,
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    from core.reflex.__main__ import _handle_trigger_fired

    redis = AsyncMock()

    bad_data: dict[str | bytes, str | bytes] = {"not_event": "data"}
    await _handle_trigger_fired(
        bad_data,
        mock_engine,
        mock_agent,
        redis,
        mock_publisher,
    )

    mock_publisher.publish.assert_not_called()


@pytest.mark.asyncio
async def test_handle_trigger_fired_dnd_defers_informational(
    mock_engine: AsyncMock,
    mock_agent: AsyncMock,
) -> None:
    """DND active + INFORMATIONAL urgency -> notification deferred."""
    from core.notifications.dispatcher import NotificationDispatcher
    from core.notifications.dnd import DNDChecker
    from core.notifications.publisher import NotificationPublisher
    from core.notifications.schema import DNDStatus
    from core.reflex.__main__ import _handle_trigger_fired

    redis = AsyncMock()
    redis.rpush = AsyncMock()
    redis.xadd = AsyncMock()

    dnd_checker = AsyncMock(spec=DNDChecker)
    dnd_checker.is_active = AsyncMock(
        return_value=DNDStatus(active=True, reason="manual", source="manual")
    )
    dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_checker)
    publisher = NotificationPublisher(dispatcher)

    event = TriggerFired(
        trigger_id="t-1",
        trigger_name="low priority",
        trigger_type="time",
        urgency="informational",
    )

    await _handle_trigger_fired(
        _make_entry_data(event),
        mock_engine,
        mock_agent,
        redis,
        publisher,
    )

    # Should defer (rpush to deferred queue), NOT deliver (no xadd to dispatch stream)
    redis.rpush.assert_called_once()
    redis.xadd.assert_not_called()


@pytest.mark.asyncio
async def test_handle_trigger_fired_dnd_delivers_urgent(
    mock_engine: AsyncMock,
    mock_agent: AsyncMock,
) -> None:
    """DND active + URGENT urgency -> notification delivered immediately."""
    from core.notifications.dispatcher import NotificationDispatcher
    from core.notifications.dnd import DNDChecker
    from core.notifications.publisher import NotificationPublisher
    from core.notifications.schema import DNDStatus
    from core.reflex.__main__ import _handle_trigger_fired

    redis = AsyncMock()
    redis.rpush = AsyncMock()
    redis.xadd = AsyncMock()

    dnd_checker = AsyncMock(spec=DNDChecker)
    dnd_checker.is_active = AsyncMock(
        return_value=DNDStatus(active=True, reason="manual", source="manual")
    )
    dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_checker)
    publisher = NotificationPublisher(dispatcher)

    event = TriggerFired(
        trigger_id="t-1",
        trigger_name="critical alert",
        trigger_type="sensor",
        urgency="urgent",
    )

    await _handle_trigger_fired(
        _make_entry_data(event),
        mock_engine,
        mock_agent,
        redis,
        publisher,
    )

    # Should deliver (xadd to dispatch stream), NOT defer
    redis.xadd.assert_called_once()
    redis.rpush.assert_not_called()


@pytest.mark.asyncio
async def test_handle_trigger_fired_with_slm_action(
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    from bus.schemas.events import ActionRequest, ActionResult
    from core.reflex.__main__ import _handle_trigger_fired

    action_result = ActionResult(
        source="home-service",
        request_id="r-1",
        tool_name="lighting.dim_lights",
        status="success",
    )
    mock_agent.execute_action = AsyncMock(return_value=action_result)

    engine = AsyncMock()
    engine.process_trigger_fired = AsyncMock(
        return_value=ActionRequest(
            source="reflex-engine",
            target_service="home-service",
            tool_name="lighting.dim_lights",
            parameters={"room": "bedroom", "level": 10},
        )
    )

    event = TriggerFired(
        trigger_id="t-1",
        trigger_name="bedtime",
        trigger_type="time",
    )
    redis = AsyncMock()
    redis.xadd = AsyncMock()

    await _handle_trigger_fired(
        _make_entry_data(event),
        engine,
        mock_agent,
        redis,
        mock_publisher,
    )

    mock_publisher.publish.assert_called_once()
    mock_agent.execute_action.assert_called_once()
    # Two xadd calls: result stream + observation stream
    assert redis.xadd.call_count == 2


@pytest.mark.asyncio
async def test_handle_trigger_fired_publishes_observation(
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    """TriggerFired path publishes structured ReflexObservation."""
    from bus.schemas.events import ActionRequest, ActionResult, ReflexObservation
    from core.reflex.__main__ import _handle_trigger_fired

    action_result = ActionResult(
        source="home-service",
        request_id="r-1",
        tool_name="lighting.dim_lights",
        status="success",
    )
    mock_agent.execute_action = AsyncMock(return_value=action_result)

    engine = AsyncMock()
    engine.process_trigger_fired = AsyncMock(
        return_value=ActionRequest(
            source="reflex-engine",
            target_service="home-service",
            tool_name="lighting.dim_lights",
            parameters={"room": "bedroom", "level": 10},
        )
    )

    event = TriggerFired(
        trigger_id="t-1",
        trigger_name="bedtime dim",
        trigger_type="time",
    )
    redis = AsyncMock()
    redis.xadd = AsyncMock()

    await _handle_trigger_fired(
        _make_entry_data(event),
        engine,
        mock_agent,
        redis,
        mock_publisher,
    )

    # Second xadd is the observation
    obs_call = redis.xadd.call_args_list[1]
    obs_json = obs_call.args[1]["event"]
    obs = ReflexObservation.model_validate_json(obs_json)
    assert obs.origin == "trigger_fired"
    assert obs.action.tool_name == "lighting.dim_lights"
    assert obs.result.status == "success"
