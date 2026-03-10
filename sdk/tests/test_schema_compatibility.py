"""Tests that SDK event schemas remain wire-compatible with bus/schemas/events.py."""

from __future__ import annotations

from typing import Any


def test_state_changed_roundtrip_bus_to_sdk() -> None:
    """Serialize a bus StateChangedEvent, deserialize as SDK StateChangedEvent."""
    from bus.schemas.events import StateChangedEvent as BusEvent
    from sdk.alfred_sdk.events import StateChangedEvent as SdkEvent

    bus_event = BusEvent(
        source="home-service",
        domain="home",
        entity_id="light.living_room",
        old_state="on",
        new_state="off",
        attributes={"brightness": 0},
    )
    json_str = bus_event.model_dump_json()
    sdk_event = SdkEvent.model_validate_json(json_str)

    assert sdk_event.event_id == bus_event.event_id
    assert sdk_event.entity_id == bus_event.entity_id
    assert sdk_event.timestamp == bus_event.timestamp
    assert sdk_event.attributes == bus_event.attributes


def test_action_request_roundtrip_sdk_to_bus() -> None:
    """Serialize an SDK ActionRequest, deserialize as bus ActionRequest."""
    from bus.schemas.events import ActionRequest as BusAction
    from sdk.alfred_sdk.events import ActionRequest as SdkAction

    sdk_action = SdkAction(
        source="reflex-engine",
        target_service="home-service",
        tool_name="smart_home.dim_lights",
        parameters={"room": "living_room", "level": 20},
    )
    json_str = sdk_action.model_dump_json()
    bus_action = BusAction.model_validate_json(json_str)

    assert bus_action.tool_name == sdk_action.tool_name
    assert bus_action.parameters == sdk_action.parameters
    assert bus_action.request_id == sdk_action.request_id


def test_action_result_roundtrip_bus_to_sdk() -> None:
    """Serialize a bus ActionResult, deserialize as SDK ActionResult."""
    from bus.schemas.events import ActionResult as BusResult
    from sdk.alfred_sdk.events import ActionResult as SdkResult

    bus_result = BusResult(
        source="home-agent",
        request_id="req-123",
        tool_name="smart_home.dim_lights",
        status="success",
        result={"brightness": 20},
    )
    json_str = bus_result.model_dump_json()
    sdk_result = SdkResult.model_validate_json(json_str)

    assert sdk_result.status == bus_result.status
    assert sdk_result.result == bus_result.result


def _get_field_names(model: type[Any]) -> set[str]:
    """Get all field names from a Pydantic model."""
    return set(model.model_fields.keys())


def test_shared_schemas_have_same_fields() -> None:
    """Verify SDK and bus schemas define the same fields."""
    from bus.schemas import events as bus
    from sdk.alfred_sdk import events as sdk

    for bus_cls, sdk_cls in [
        (bus.BaseEvent, sdk.BaseEvent),
        (bus.StateChangedEvent, sdk.StateChangedEvent),
        (bus.ActionRequest, sdk.ActionRequest),
        (bus.ActionResult, sdk.ActionResult),
    ]:
        bus_fields = _get_field_names(bus_cls)
        sdk_fields = _get_field_names(sdk_cls)
        assert bus_fields == sdk_fields, (
            f"{bus_cls.__name__} field mismatch: "
            f"bus-only={bus_fields - sdk_fields}, sdk-only={sdk_fields - bus_fields}"
        )
