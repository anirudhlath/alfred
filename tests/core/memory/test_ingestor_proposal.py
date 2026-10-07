"""A Reflex proposal never changes what episodic memory records (#285)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from bus.schemas.events import ReflexObservation, ReflexProposal, StateChangedEvent, TriggerFired
from core.memory.ingestor import (
    _build_observation_semantic_key,
    _build_observation_summary,
    ingest_observation,
)


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


async def test_a_trigger_proposal_writes_no_memory() -> None:
    """A trigger dump has no entity or states: as a passive memory it reads "unknown → unknown"."""
    trigger = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")
    obs = ReflexObservation(
        source="reflex-engine",
        origin="trigger_fired",
        trigger_event=trigger.model_dump(),
        proposal=ReflexProposal(decision="ask", reason="Lights off for the night?"),
    )
    episodic = AsyncMock()

    await ingest_observation(obs, episodic, AsyncMock(), AsyncMock(), tz=ZoneInfo("UTC"))

    episodic.write.assert_not_awaited()


async def test_a_state_change_proposal_is_still_remembered_as_seen() -> None:
    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="media_player.living_room_tv",
        old_state="paused",
        new_state="playing",
    )
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event=event.model_dump(),
        proposal=ReflexProposal(decision="ask", reason="Dim for the film?"),
    )
    episodic = AsyncMock()

    await ingest_observation(obs, episodic, AsyncMock(), AsyncMock(), tz=ZoneInfo("UTC"))

    episodic.write.assert_awaited_once()
    assert episodic.write.await_args.args[0].source == "observation"
