"""Integration tests for the TriggerFired consumer in the Reflex Runner."""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock

import pytest

from bus.schemas.events import ReflexProposal, TriggerFired
from core.notifications.schema import Urgency
from core.triggers.models import TRIGGER_ENGINE_SOURCE


@pytest.fixture
def mock_publisher() -> AsyncMock:
    publisher = AsyncMock()
    publisher.publish = AsyncMock()
    return publisher


@pytest.fixture
def mock_engine() -> AsyncMock:
    engine = AsyncMock()
    engine.process_trigger_fired = AsyncMock(return_value=ReflexProposal(decision="none"))
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
    assert call_kwargs.kwargs["source"] == TRIGGER_ENGINE_SOURCE
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
async def test_a_trigger_proposal_is_recorded_not_executed(
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    from bus.schemas.events import ActionRequest, ReflexObservation, ReflexProposal
    from core.reflex.__main__ import _handle_trigger_fired

    engine = AsyncMock()
    engine.process_trigger_fired = AsyncMock(
        return_value=ReflexProposal(
            decision="act",
            reason="Bedtime",
            action=ActionRequest(
                source="reflex-engine",
                target_service="home-service",
                tool_name="home.light_turn_off",
                parameters={"target": "Living Room"},
                reason="Bedtime",
            ),
        )
    )
    event = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")
    redis = AsyncMock()

    await _handle_trigger_fired(_make_entry_data(event), engine, mock_agent, redis, mock_publisher)

    mock_publisher.publish.assert_called_once()  # Path A is unchanged
    mock_agent.execute_action.assert_not_called()
    (call,) = redis.xadd.await_args_list
    stream, fields = call.args
    assert stream == "alfred:reflex:observations"
    obs = ReflexObservation.model_validate_json(fields["event"])
    assert obs.origin == "trigger_fired"
    assert obs.proposal is not None
    assert obs.proposal.decision == "act"
    assert obs.action is None
    assert obs.result is None
    assert redis.hincrby.await_args_list[0].args[1:] == ("act", 1)


@pytest.mark.asyncio
async def test_a_trigger_none_records_only_the_count(
    mock_engine: AsyncMock,
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
) -> None:
    from core.reflex.__main__ import _handle_trigger_fired

    event = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")
    redis = AsyncMock()

    await _handle_trigger_fired(
        _make_entry_data(event), mock_engine, mock_agent, redis, mock_publisher
    )

    redis.xadd.assert_not_awaited()
    assert redis.hincrby.await_args_list[0].args[1:] == ("none", 1)


@pytest.mark.asyncio
async def test_an_invalid_trigger_proposal_is_logged_as_a_warning(
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from bus.schemas.events import ReflexProposal
    from core.reflex.__main__ import _handle_trigger_fired

    engine = AsyncMock()
    engine.process_trigger_fired = AsyncMock(
        return_value=ReflexProposal(decision="invalid", raw="Sure!", problem="not JSON")
    )
    event = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")

    with caplog.at_level(logging.WARNING, logger="core.reflex.__main__"):
        await _handle_trigger_fired(
            _make_entry_data(event), engine, mock_agent, AsyncMock(), mock_publisher
        )

    assert any(
        r.levelno == logging.WARNING and "not JSON" in r.getMessage() for r in caplog.records
    )


@pytest.mark.asyncio
async def test_a_failed_proposal_write_is_not_blamed_on_the_model(
    mock_agent: AsyncMock,
    mock_publisher: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from bus.schemas.events import ReflexProposal
    from core.reflex.__main__ import _handle_trigger_fired

    engine = AsyncMock()
    engine.process_trigger_fired = AsyncMock(
        return_value=ReflexProposal(decision="ask", reason="Lights off for the night?")
    )
    event = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")
    redis = AsyncMock()
    redis.xadd = AsyncMock(side_effect=Exception("OOM command not allowed"))

    with caplog.at_level(logging.WARNING, logger="core.reflex.__main__"):
        await _handle_trigger_fired(
            _make_entry_data(event), engine, mock_agent, redis, mock_publisher
        )

    messages = [r.getMessage() for r in caplog.records]
    assert not any("SLM reasoning failed" in m for m in messages)
    assert any("Proposal observation failed" in m and "OOM" in m for m in messages)
