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
