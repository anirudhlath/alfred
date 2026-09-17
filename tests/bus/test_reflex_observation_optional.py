"""ReflexObservation must be able to represent 'saw it, did nothing'."""

from __future__ import annotations

from bus.schemas.events import ActionRequest, ActionResult, ReflexObservation


def test_observation_without_action_round_trips() -> None:
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={"entity_id": "light.hallway", "new_state": "on"},
    )

    assert obs.action is None
    assert obs.result is None

    restored = ReflexObservation.model_validate_json(obs.model_dump_json())
    assert restored.action is None
    assert restored.result is None
    assert restored.trigger_event["entity_id"] == "light.hallway"


def test_observation_with_action_still_round_trips() -> None:
    """Regression: the existing action path is unchanged."""
    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="home.light_turn_on",
        parameters={"entity_id": "light.hallway"},
    )
    result = ActionResult(
        source="home-service",
        request_id=action.request_id,
        tool_name="home.light_turn_on",
        status="success",
    )
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={"entity_id": "light.hallway", "new_state": "on"},
        action=action,
        result=result,
    )

    restored = ReflexObservation.model_validate_json(obs.model_dump_json())
    assert restored.action is not None
    assert restored.action.tool_name == "home.light_turn_on"
    assert restored.result is not None
    assert restored.result.status == "success"
