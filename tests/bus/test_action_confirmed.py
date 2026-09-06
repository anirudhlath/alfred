"""ActionRequest confirmation marker (contract C3) — bus schema + SDK mirror."""

from __future__ import annotations


def test_action_request_confirmed_defaults_false() -> None:
    from bus.schemas.events import ActionRequest

    action = ActionRequest(
        source="conscious-engine",
        target_service="home-service",
        tool_name="home.unlock_door",
    )
    assert action.confirmed is False


def test_confirmed_roundtrip_bus_to_sdk() -> None:
    """A confirmed bus ActionRequest deserializes as a confirmed SDK ActionRequest."""
    from bus.schemas.events import ActionRequest as BusAction
    from sdk.alfred_sdk.events import ActionRequest as SdkAction

    bus_action = BusAction(
        source="domain-router",
        target_service="home-service",
        tool_name="home.unlock_door",
        parameters={"entity_id": "lock.front_door"},
        confirmed=True,
    )
    sdk_action = SdkAction.model_validate_json(bus_action.model_dump_json())
    assert sdk_action.confirmed is True
    assert sdk_action.request_id == bus_action.request_id


def test_confirmed_roundtrip_sdk_to_bus() -> None:
    from bus.schemas.events import ActionRequest as BusAction
    from sdk.alfred_sdk.events import ActionRequest as SdkAction

    sdk_action = SdkAction(
        source="reflex-engine",
        target_service="home-service",
        tool_name="lighting.dim_lights",
    )
    bus_action = BusAction.model_validate_json(sdk_action.model_dump_json())
    assert bus_action.confirmed is False


def test_pending_and_attention_prefixes_exist() -> None:
    from shared.streams import ATTENTION_PREFIX, PENDING_ACTIONS_PREFIX

    assert ATTENTION_PREFIX == "alfred:attention:"
    assert PENDING_ACTIONS_PREFIX == "alfred:pending_actions:"


def test_reason_defaults_to_none_and_roundtrips_bus_to_sdk() -> None:
    """`reason` is optional, and survives the bus → SDK → bus JSON roundtrip."""
    from bus.schemas.events import ActionRequest as BusAction
    from sdk.alfred_sdk.events import ActionRequest as SdkAction

    bare = BusAction(
        source="conscious-engine", target_service="home-service", tool_name="home.unlock_door"
    )
    assert bare.reason is None

    bus_action = BusAction(
        source="conscious-engine",
        target_service="home-service",
        tool_name="home.unlock_door",
        parameters={"entity_id": "lock.front_door"},
        reason="You asked me to let the dog walker in at 3pm.",
    )
    sdk_action = SdkAction.model_validate_json(bus_action.model_dump_json())
    assert sdk_action.reason == "You asked me to let the dog walker in at 3pm."
    back = BusAction.model_validate_json(sdk_action.model_dump_json())
    assert back.reason == bus_action.reason
