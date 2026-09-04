"""Ingesting observations that record a non-action."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from bus.schemas.events import ReflexObservation

if TYPE_CHECKING:
    from core.memory.schemas import EpisodicEntry


def _passive(
    entity_id: str = "media_player.living_room_apple_tv",
    old_state: str = "paused",
    new_state: str = "playing",
    attributes: dict[str, object] | None = None,
) -> ReflexObservation:
    return ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={
            "entity_id": entity_id,
            "old_state": old_state,
            "new_state": new_state,
            "attributes": attributes if attributes is not None else {},
        },
    )


@pytest.fixture
def scorer() -> AsyncMock:
    mock = AsyncMock()
    mock.score = AsyncMock(
        return_value=MagicMock(overall=0.2, safety=0.0, novelty=0.3, personal=0.3, emotional=0.2)
    )
    return mock


@pytest.mark.asyncio
async def test_passive_observation_uses_the_observation_source(scorer: AsyncMock) -> None:
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(_passive(), episodic, scorer)

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.source == "observation"


@pytest.mark.asyncio
async def test_summary_describes_the_transition(scorer: AsyncMock) -> None:
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(_passive(attributes={"media_title": "Harry Potter"}), episodic, scorer)

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.summary == (
        "[observation] media_player.living_room_apple_tv: paused → playing (Harry Potter)"
    )


@pytest.mark.asyncio
async def test_summary_without_salient_attributes(scorer: AsyncMock) -> None:
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(_passive("binary_sensor.front_door", "off", "on"), episodic, scorer)

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.summary == "[observation] binary_sensor.front_door: off → on"


@pytest.mark.asyncio
async def test_salient_attributes_are_folded_in_declared_order(scorer: AsyncMock) -> None:
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(
        _passive(
            "light.kitchen",
            "off",
            "on",
            attributes={
                "friendly_name": "Kitchen Light",
                "brightness": 178,
                "unrelated": "ignored",
            },
        ),
        episodic,
        scorer,
    )

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.summary == "[observation] light.kitchen: off → on (178, Kitchen Light)"
    assert "ignored" not in entry.summary


@pytest.mark.asyncio
async def test_missing_old_state_is_rendered_as_unknown(scorer: AsyncMock) -> None:
    """StateChangedEvent.old_state is Optional — a first sighting has none."""
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={"entity_id": "sensor.new_device", "new_state": "22.5"},
    )
    await ingest_observation(obs, episodic, scorer)

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.summary == "[observation] sensor.new_device: unknown → 22.5"


@pytest.mark.asyncio
async def test_entities_come_from_the_trigger_event(scorer: AsyncMock) -> None:
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(_passive("light.hallway"), episodic, scorer)

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.entities == ["light.hallway"]


@pytest.mark.asyncio
async def test_semantic_key_is_searchable(scorer: AsyncMock) -> None:
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    await ingest_observation(_passive("light.hallway", "off", "on"), episodic, scorer)

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.semantic_key == "Observed light.hallway change from off to on"


@pytest.mark.asyncio
async def test_passive_scorer_is_used_when_supplied(scorer: AsyncMock) -> None:
    """The passive population must be scored by the passive scorer."""
    from core.memory.ingestor import ingest_observation

    episodic = AsyncMock()
    passive_scorer = AsyncMock()
    passive_scorer.score = AsyncMock(
        return_value=MagicMock(overall=0.1, safety=0.0, novelty=0.1, personal=0.3, emotional=0.2)
    )

    await ingest_observation(_passive(), episodic, scorer, passive_scorer=passive_scorer)

    passive_scorer.score.assert_awaited_once()
    scorer.score.assert_not_awaited()


@pytest.mark.asyncio
async def test_action_observations_still_use_the_default_scorer(scorer: AsyncMock) -> None:
    """Regression: the action path is untouched by the passive scorer."""
    from bus.schemas.events import ActionRequest, ActionResult
    from core.memory.ingestor import ingest_observation

    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="home.light_turn_on",
        parameters={"entity_id": "light.hallway"},
    )
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={"entity_id": "light.hallway", "new_state": "on"},
        action=action,
        result=ActionResult(
            source="home-service",
            request_id=action.request_id,
            tool_name="home.light_turn_on",
            status="success",
        ),
    )

    episodic = AsyncMock()
    passive_scorer = AsyncMock()

    await ingest_observation(obs, episodic, scorer, passive_scorer=passive_scorer)

    entry: EpisodicEntry = episodic.write.call_args.args[0]
    assert entry.source == "reflex"
    assert "home.light_turn_on" in entry.summary
    scorer.score.assert_awaited_once()
    passive_scorer.score.assert_not_awaited()
