"""Tests for the Memory Ingestor — writes ReflexObservations to episodic memory."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bus.schemas.events import ActionRequest, ActionResult, ReflexObservation
from core.memory.schemas import EpisodicEntry


def _make_observation(
    tool_name: str = "smart_home.turn_on",
    entity_id: str = "light.hallway",
    status: str = "success",
    origin: str = "state_change",
    decision_context: str | None = None,
) -> ReflexObservation:
    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name=tool_name,
        parameters={"entity_id": entity_id},
    )
    result = ActionResult(
        source="home-service",
        request_id=action.request_id,
        tool_name=tool_name,
        status=status,
    )
    return ReflexObservation(
        source="reflex-engine",
        origin=origin,
        trigger_event={"entity_id": entity_id, "new_state": "on"},
        action=action,
        result=result,
        decision_context=decision_context,
    )


@pytest.mark.asyncio
async def test_ingest_observation_writes_to_episodic() -> None:
    """Memory Ingestor stores ReflexObservation as episodic entry."""
    from core.memory.ingestor import ingest_observation

    mock_episodic = AsyncMock()
    mock_scorer = AsyncMock()
    mock_scorer.score = AsyncMock(
        return_value=MagicMock(overall=0.3, safety=0.0, novelty=0.2, personal=0.1, emotional=0.0)
    )

    obs = _make_observation()

    await ingest_observation(obs, mock_episodic, mock_scorer)

    mock_episodic.write.assert_called_once()
    entry: EpisodicEntry = mock_episodic.write.call_args.args[0]
    assert entry.source == "reflex"
    assert "smart_home.turn_on" in entry.summary
    assert "light.hallway" in entry.entities
    assert entry.entities == ["light.hallway"]
    mock_scorer.score.assert_called_once()


@pytest.mark.asyncio
async def test_ingest_observation_includes_decision_context() -> None:
    """Decision context from SLM reasoning is included in summary."""
    from core.memory.ingestor import ingest_observation

    mock_episodic = AsyncMock()
    mock_scorer = AsyncMock()
    mock_scorer.score = AsyncMock(
        return_value=MagicMock(overall=0.5, safety=0.0, novelty=0.5, personal=0.0, emotional=0.0)
    )

    obs = _make_observation(decision_context="Motion detected at night, turning on hallway light")

    await ingest_observation(obs, mock_episodic, mock_scorer)

    entry: EpisodicEntry = mock_episodic.write.call_args.args[0]
    assert "Motion detected at night" in entry.summary


@pytest.mark.asyncio
async def test_ingest_observation_trigger_fired_origin() -> None:
    """TriggerFired origin is reflected in the episodic entry."""
    from core.memory.ingestor import ingest_observation

    mock_episodic = AsyncMock()
    mock_scorer = AsyncMock()
    mock_scorer.score = AsyncMock(
        return_value=MagicMock(overall=0.3, safety=0.0, novelty=0.2, personal=0.1, emotional=0.0)
    )

    obs = _make_observation(origin="trigger_fired")

    await ingest_observation(obs, mock_episodic, mock_scorer)

    entry: EpisodicEntry = mock_episodic.write.call_args.args[0]
    assert entry.source == "reflex"
    assert "trigger" in entry.semantic_key.lower() or "reflex" in entry.semantic_key.lower()


@pytest.mark.asyncio
async def test_ingest_observation_extracts_entities() -> None:
    """Entities are extracted from trigger_event and action parameters."""
    from core.memory.ingestor import ingest_observation

    mock_episodic = AsyncMock()
    mock_scorer = AsyncMock()
    mock_scorer.score = AsyncMock(
        return_value=MagicMock(overall=0.3, safety=0.0, novelty=0.2, personal=0.1, emotional=0.0)
    )

    obs = _make_observation(entity_id="light.kitchen")

    await ingest_observation(obs, mock_episodic, mock_scorer)

    entry: EpisodicEntry = mock_episodic.write.call_args.args[0]
    assert "light.kitchen" in entry.entities
