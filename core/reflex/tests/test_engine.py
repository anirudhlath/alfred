"""Tests for the Reflex Engine — System 1 SLM inference loop."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from bus.schemas.events import StateChangedEvent

# tv_on_event fixture is inherited from conftest.py


@pytest.fixture
def mock_preferences() -> str:
    return (
        "# Lighting Preferences\n\n"
        "- I prefer dim lighting when watching TV or movies\n"
        "- Default brightness during daytime: 80%\n"
    )


@pytest.mark.asyncio
async def test_reflex_engine_produces_action(
    tv_on_event: StateChangedEvent, mock_preferences: str
) -> None:
    from core.reflex.engine import ReflexEngine

    mock_ollama_response = {
        "response": json.dumps(
            {
                "tool_name": "smart_home.dim_lights",
                "target_service": "home-service",
                "parameters": {"room": "living_room", "level": 20},
            }
        ),
        "prompt_tokens": 200,
        "completion_tokens": 30,
        "total_tokens": 230,
    }

    with (
        patch("core.reflex.memory_reader.read_preferences", return_value=mock_preferences),
        patch(
            "core.reflex.ollama_client.infer",
            new_callable=AsyncMock,
            return_value=mock_ollama_response,
        ),
    ):
        engine = ReflexEngine(preferences_dir="/fake/prefs")
        action = await engine.process_event(tv_on_event)

    assert action is not None
    assert action.tool_name == "smart_home.dim_lights"
    assert action.parameters["level"] == 20
    assert action.target_service == "home-service"


@pytest.mark.asyncio
async def test_reflex_engine_returns_none_for_no_action(mock_preferences: str) -> None:
    from core.reflex.engine import ReflexEngine

    boring_event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="sensor.temperature",
        new_state="22.5",
    )

    mock_ollama_response = {
        "response": json.dumps({"action": "none"}),
        "prompt_tokens": 150,
        "completion_tokens": 10,
        "total_tokens": 160,
    }

    with (
        patch("core.reflex.memory_reader.read_preferences", return_value=mock_preferences),
        patch(
            "core.reflex.ollama_client.infer",
            new_callable=AsyncMock,
            return_value=mock_ollama_response,
        ),
    ):
        engine = ReflexEngine(preferences_dir="/fake/prefs")
        action = await engine.process_event(boring_event)

    assert action is None
