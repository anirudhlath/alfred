"""A Reflex proposal never changes what episodic memory records (#285)."""

from __future__ import annotations

from bus.schemas.events import ReflexObservation, ReflexProposal, StateChangedEvent
from core.memory.ingestor import _build_observation_semantic_key, _build_observation_summary


def test_a_proposal_does_not_change_the_memory_text() -> None:
    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="media_player.living_room_tv",
        old_state="paused",
        new_state="playing",
    )
    plain = ReflexObservation(
        source="reflex-engine", origin="state_change", trigger_event=event.model_dump()
    )
    proposed = plain.model_copy(
        update={"proposal": ReflexProposal(decision="ask", reason="Film at night")}
    )
    stamp = "Wed 2026-10-07 22:30"

    assert _build_observation_summary(proposed, stamp) == _build_observation_summary(plain, stamp)
    assert _build_observation_semantic_key(proposed) == _build_observation_semantic_key(plain)
