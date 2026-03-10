"""Integration test: full event → reflex → action pipeline.

Uses mocked Ollama and Redis to test the complete flow without external services.
This is the eval-ability contract test — structured in, structured out.
"""

import json
import pathlib
from unittest.mock import AsyncMock, patch

import pytest

from bus.schemas.events import ActionRequest, StateChangedEvent
from core.reflex.engine import ReflexEngine
from core.reflex.tool_registry import ToolInfo


def _make_mock_registry() -> AsyncMock:
    """Build a mock ToolRegistry with standard home-service tools."""
    tools = [
        ToolInfo(
            name="lighting.dim_lights",
            description="Dim the lights in a room.",
            parameters={
                "room": {"type": "str", "description": "The room to dim."},
                "level": {"type": "int", "description": "Brightness level 0-100."},
            },
            feature_name="lighting",
            feature_description="Smart home lighting controls.",
            target_service="home-service",
        ),
    ]
    registry = AsyncMock()
    registry.get_tools = AsyncMock(return_value=tools)
    return registry


@pytest.fixture
def preferences_dir(tmp_path: pathlib.Path) -> str:
    prefs = tmp_path / "preferences"
    prefs.mkdir()

    lighting = prefs / "lighting.md"
    lighting.write_text(
        "---\ndomain: home\n---\n"
        "# Lighting Preferences\n\n"
        "- I prefer dim lighting when watching TV or movies\n"
        "- Default brightness during daytime: 80%\n"
        "- Default brightness in the evening: 40%\n"
    )
    return str(prefs)


# tv_on_event fixture is inherited from conftest.py


@pytest.mark.asyncio
async def test_full_reflex_pipeline(preferences_dir: str, tv_on_event: StateChangedEvent) -> None:
    """The canonical test: TV turns on → Reflex reads preferences → dims lights."""

    ollama_response = {
        "response": json.dumps(
            {
                "tool_name": "lighting.dim_lights",
                "target_service": "home-service",
                "parameters": {"room": "living_room", "level": 20},
            }
        ),
        "prompt_tokens": 200,
        "completion_tokens": 25,
        "total_tokens": 225,
    }

    mock_registry = _make_mock_registry()

    with patch(
        "core.reflex.ollama_client.infer",
        new_callable=AsyncMock,
        return_value=ollama_response,
    ):
        engine = ReflexEngine(
            preferences_dir=preferences_dir,
            tool_registry=mock_registry,
        )
        action = await engine.process_event(tv_on_event)

    # Structured output verification (eval contract)
    assert action is not None
    assert isinstance(action, ActionRequest)
    assert action.tool_name == "lighting.dim_lights"
    assert action.target_service == "home-service"
    assert action.parameters["room"] == "living_room"
    assert 0 <= action.parameters["level"] <= 100


@pytest.mark.asyncio
async def test_reflex_no_action_for_irrelevant_event(preferences_dir: str) -> None:
    """Temperature sensor change should not trigger any action."""

    temp_event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="sensor.outside_temperature",
        new_state="22.5",
    )

    ollama_response = {
        "response": json.dumps({"action": "none"}),
        "prompt_tokens": 180,
        "completion_tokens": 8,
        "total_tokens": 188,
    }

    mock_registry = _make_mock_registry()

    with patch(
        "core.reflex.ollama_client.infer",
        new_callable=AsyncMock,
        return_value=ollama_response,
    ):
        engine = ReflexEngine(
            preferences_dir=preferences_dir,
            tool_registry=mock_registry,
        )
        action = await engine.process_event(temp_event)

    assert action is None
