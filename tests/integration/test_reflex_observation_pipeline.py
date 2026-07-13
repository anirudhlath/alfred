"""Integration test: Reflex action → observation stream → Memory Ingestor → episodic recall."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bus.schemas.events import ActionRequest, ActionResult, ReflexObservation, StateChangedEvent
from core.memory.ingestor import ingest_observation
from core.memory.schemas import EpisodicEntry, SignificanceScore


@pytest.mark.asyncio
async def test_reflex_observation_reaches_episodic_memory() -> None:
    """Full pipeline: build observation → ingest → verify episodic entry."""
    # Simulate what process_stream_entry builds
    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="binary_sensor.hallway_motion",
        old_state="off",
        new_state="on",
    )
    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="smart_home.turn_on",
        parameters={"entity_id": "light.hallway"},
    )
    result = ActionResult(
        source="home-service",
        request_id=action.request_id,
        tool_name="smart_home.turn_on",
        status="success",
    )
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event=event.model_dump(),
        action=action,
        result=result,
        decision_context="Motion in hallway at night, turning on light",
    )

    # Mock episodic memory to capture the write
    mock_episodic = AsyncMock()
    mock_scorer = AsyncMock()
    mock_scorer.score = AsyncMock(
        return_value=SignificanceScore(
            overall=0.4, safety=0.1, novelty=0.3, personal=0.0, emotional=0.0
        )
    )

    await ingest_observation(obs, mock_episodic, mock_scorer)

    # Verify episodic write
    mock_episodic.write.assert_called_once()
    entry: EpisodicEntry = mock_episodic.write.call_args.args[0]
    significance: SignificanceScore = mock_episodic.write.call_args.args[1]

    assert entry.source == "reflex"
    assert "smart_home.turn_on" in entry.summary
    assert "light.hallway" in entry.entities
    assert "Motion in hallway" in entry.summary
    assert significance.overall == 0.4


@pytest.mark.asyncio
async def test_observation_roundtrip_serialization() -> None:
    """ReflexObservation survives JSON roundtrip (as it would through Redis stream)."""
    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="smart_home.dim_lights",
        parameters={"room": "living_room", "level": 20},
    )
    result = ActionResult(
        source="home-service",
        request_id=action.request_id,
        tool_name="smart_home.dim_lights",
        status="success",
    )
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={"entity_id": "media_player.tv", "new_state": "on"},
        action=action,
        result=result,
    )

    # Simulate Redis stream roundtrip
    json_str = obs.model_dump_json()
    restored = ReflexObservation.model_validate_json(json_str)

    assert restored.observation_id == obs.observation_id
    assert restored.action.tool_name == obs.action.tool_name
    assert restored.result.status == obs.result.status
    assert restored.origin == obs.origin
