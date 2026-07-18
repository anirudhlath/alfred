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


def test_service_registered_roundtrip_sdk_to_bus() -> None:
    """Serialize an SDK ServiceRegistered, deserialize as bus ServiceRegistered."""
    from bus.schemas.events import ServiceRegistered as BusReg
    from sdk.alfred_sdk.events import ServiceRegistered as SdkReg

    sdk_event = SdkReg(
        source="home-service",
        service_name="home-service",
        credentials_endpoint="http://localhost:8000/credentials",
        has_credentials_schema=True,
    )
    bus_event = BusReg.model_validate_json(sdk_event.model_dump_json())

    assert bus_event.event_type == "service_registered"
    assert bus_event.service_name == "home-service"
    assert bus_event.credentials_endpoint == "http://localhost:8000/credentials"
    assert bus_event.has_credentials_schema is True
    assert bus_event.event_id == sdk_event.event_id


def test_service_registered_defaults() -> None:
    """A service without credential support publishes a minimal event."""
    from sdk.alfred_sdk.events import ServiceRegistered

    event = ServiceRegistered(source="plain-service", service_name="plain-service")
    assert event.credentials_endpoint is None
    assert event.has_credentials_schema is False


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
        (bus.ServiceRegistered, sdk.ServiceRegistered),
    ]:
        bus_fields = _get_field_names(bus_cls)
        sdk_fields = _get_field_names(sdk_cls)
        assert bus_fields == sdk_fields, (
            f"{bus_cls.__name__} field mismatch: "
            f"bus-only={bus_fields - sdk_fields}, sdk-only={sdk_fields - bus_fields}"
        )


def test_credential_models_match_core_field_shape() -> None:
    """SDK CredentialField/CredentialSchema must stay JSON-identical to core's.

    The JSON contract is the coupling (Pillar 3) — the SDK never imports core,
    so this test is the only guard against drift with core/integrations/base.py.
    """
    from core.integrations.base import CredentialField as CoreField
    from core.integrations.base import CredentialSchema as CoreSchema
    from sdk.alfred_sdk.feature import CredentialField as SdkField
    from sdk.alfred_sdk.feature import CredentialSchema as SdkSchema

    assert _get_field_names(CoreField) == _get_field_names(SdkField)
    assert _get_field_names(CoreSchema) == _get_field_names(SdkSchema)

    # Round-trip: SDK-serialized schema parses as the core model with values intact.
    sdk_schema = SdkSchema(
        fields={"token": SdkField(label="Token", field_type="password", transient=False)}
    )
    core_schema = CoreSchema.model_validate_json(sdk_schema.model_dump_json())
    assert core_schema.fields["token"].label == "Token"
    assert core_schema.fields["token"].field_type == "password"

    # Defaults must match too — core fills defaults for fields the SDK omitted.
    assert CoreField(label="x").model_dump() == SdkField(label="x").model_dump()
