"""Tests for canonical event schemas."""

import pytest


def test_state_changed_event_creation() -> None:
    from bus.schemas.events import StateChangedEvent

    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="light.living_room",
        old_state="on",
        new_state="off",
        attributes={"brightness": 0},
    )
    assert event.source == "home-service"
    assert event.domain == "home"
    assert event.entity_id == "light.living_room"
    assert event.event_type == "state_changed"
    assert event.timestamp is not None


def test_state_changed_event_rejects_missing_fields() -> None:
    from pydantic import ValidationError

    from bus.schemas.events import StateChangedEvent

    with pytest.raises(ValidationError):
        StateChangedEvent(source="home-service")  # type: ignore[call-arg]


def test_action_request_creation() -> None:
    from bus.schemas.events import ActionRequest

    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="smart_home.dim_lights",
        parameters={"room": "living_room", "level": 20},
    )
    assert action.event_type == "action_request"
    assert action.tool_name == "smart_home.dim_lights"
    assert action.parameters["level"] == 20


def test_action_result_success() -> None:
    from bus.schemas.events import ActionResult

    result = ActionResult(
        source="home-service",
        request_id="req-123",
        tool_name="smart_home.dim_lights",
        status="success",
        result={"brightness": 20},
    )
    assert result.status == "success"
    assert result.error is None


def test_action_result_failure() -> None:
    from bus.schemas.events import ActionResult

    result = ActionResult(
        source="home-service",
        request_id="req-123",
        tool_name="smart_home.dim_lights",
        status="error",
        error="Device unreachable",
    )
    assert result.status == "error"
    assert result.error == "Device unreachable"


def test_telemetry_event_creation() -> None:
    from bus.schemas.events import TelemetryEvent

    event = TelemetryEvent(
        source="reflex-engine",
        metric_type="latency",
        category="reflex",
        value=142.5,
        unit="ms",
        metadata={"function": "process_event", "model": "llama3:8b"},
    )
    assert event.event_type == "telemetry"
    assert event.value == 142.5


def test_tool_registration_event() -> None:
    from bus.schemas.events import ToolRegistration

    reg = ToolRegistration(
        source="home-service",
        service_name="home-service",
        service_endpoint="http://home-service:8000/mcp",
        tools=[
            {
                "name": "smart_home.dim_lights",
                "description": "Dim lights to a level",
                "parameters": {
                    "room": {"type": "string"},
                    "level": {"type": "integer", "minimum": 0, "maximum": 100},
                },
            }
        ],
    )
    assert reg.event_type == "tool_registration"
    assert len(reg.tools) == 1
    assert reg.tools[0]["name"] == "smart_home.dim_lights"


def test_event_serialization_roundtrip() -> None:
    from bus.schemas.events import StateChangedEvent

    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="light.living_room",
        old_state="on",
        new_state="off",
    )
    json_str = event.model_dump_json()
    restored = StateChangedEvent.model_validate_json(json_str)
    assert restored.entity_id == event.entity_id
    assert restored.timestamp == event.timestamp


def test_base_event_id_uniqueness() -> None:
    from bus.schemas.events import StateChangedEvent

    e1 = StateChangedEvent(
        source="a", domain="home", entity_id="x", old_state="on", new_state="off"
    )
    e2 = StateChangedEvent(
        source="a", domain="home", entity_id="x", old_state="on", new_state="off"
    )
    assert e1.event_id != e2.event_id


def test_trigger_fired_defaults() -> None:
    from bus.schemas.events import TriggerFired

    evt = TriggerFired(
        trigger_id="t-1",
        trigger_name="test trigger",
        trigger_type="time",
        context={"reason": "cron matched"},
    )
    assert evt.event_type == "trigger_fired"
    assert evt.source == "trigger-engine"
    assert evt.trigger_id == "t-1"


def test_trigger_fired_urgency_default() -> None:
    from bus.schemas.events import TriggerFired

    evt = TriggerFired(
        trigger_id="t-1",
        trigger_name="test trigger",
        trigger_type="time",
    )
    assert evt.urgency == "informational"


def test_trigger_fired_urgency_custom() -> None:
    from bus.schemas.events import TriggerFired

    evt = TriggerFired(
        trigger_id="t-1",
        trigger_name="test trigger",
        trigger_type="time",
        urgency="urgent",
    )
    assert evt.urgency == "urgent"


def test_trigger_fired_urgency_roundtrip() -> None:
    from bus.schemas.events import TriggerFired

    evt = TriggerFired(
        trigger_id="t-1",
        trigger_name="test trigger",
        trigger_type="time",
        urgency="important",
    )
    json_str = evt.model_dump_json()
    restored = TriggerFired.model_validate_json(json_str)
    assert restored.urgency == "important"


def test_trigger_created_updated_schema() -> None:
    from bus.schemas.events import TriggerCreated

    evt = TriggerCreated(
        trigger_id="t-1",
        trigger_type="sensor",
        name="dim on TV",
        created_by="reflex-engine",
        conditions={"entity_id": "media_player.tv", "state_match": "on"},
    )
    assert evt.event_type == "trigger_created"
    assert evt.source == "trigger-engine"
    assert evt.action is None
    assert evt.one_shot is False


def test_reflex_observation_schema() -> None:
    """ReflexObservation carries full context of a Reflex action."""
    from bus.schemas.events import ActionRequest, ActionResult, ReflexObservation

    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="lighting.dim_lights",
        parameters={"room": "living_room", "level": 20},
    )
    result = ActionResult(
        source="home-service",
        request_id=action.request_id,
        tool_name="lighting.dim_lights",
        status="success",
    )
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={"entity_id": "media_player.living_room_tv", "new_state": "on"},
        action=action,
        result=result,
        decision_context="TV turned on in living room, dimming lights per preference",
    )

    assert obs.observation_id  # auto-generated
    assert obs.timestamp  # auto-generated
    assert obs.origin == "state_change"
    assert obs.action.tool_name == "lighting.dim_lights"
    assert obs.result.status == "success"
    assert obs.decision_context is not None

    # Roundtrip serialization
    json_str = obs.model_dump_json()
    restored = ReflexObservation.model_validate_json(json_str)
    assert restored.observation_id == obs.observation_id
    assert restored.action.tool_name == "lighting.dim_lights"


def test_reflex_observation_defaults() -> None:
    """ReflexObservation works with minimal fields."""
    from bus.schemas.events import ActionRequest, ActionResult, ReflexObservation

    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="smart_home.turn_on",
        parameters={"entity_id": "light.hallway"},
    )
    result = ActionResult(
        source="home-service",
        request_id=action.request_id,
        tool_name="smart_home.turn_on",
        status="success",
    )
    obs = ReflexObservation(
        source="reflex-engine",
        origin="trigger_fired",
        trigger_event={},
        action=action,
        result=result,
    )

    assert obs.decision_context is None
    assert obs.event_type == "reflex_observation"
