"""ReflexProposal — Reflex's shadow decision, carried on ReflexObservation (#285)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bus.schemas.events import ActionRequest, ReflexObservation, ReflexProposal


def _act() -> ReflexProposal:
    return ReflexProposal(
        decision="act",
        reason="Film at night",
        action=ActionRequest(
            source="reflex-engine",
            target_service="home-service",
            tool_name="home.light_turn_on",
            parameters={"target": "Living Room", "brightness_pct": 30},
            reason="Film at night",
        ),
    )


def test_a_proposal_round_trips_inside_an_observation() -> None:
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={"entity_id": "media_player.living_room_tv"},
        proposal=_act(),
    )

    back = ReflexObservation.model_validate_json(obs.model_dump_json())

    assert back.proposal == obs.proposal
    # A proposal is not an action: the "this happened" fields stay empty.
    assert back.action is None
    assert back.result is None


def test_an_observation_written_before_proposals_still_parses() -> None:
    legacy = (
        '{"event_type": "reflex_observation", "source": "reflex-engine",'
        ' "origin": "state_change", "trigger_event": {}}'
    )

    assert ReflexObservation.model_validate_json(legacy).proposal is None


def test_an_unknown_decision_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ReflexProposal.model_validate({"decision": "maybe"})
