"""Integration test: full event → reflex → action pipeline.

Uses a scripted model and a mocked registry to test the complete flow without external
services.
This is the eval-ability contract test — structured in, structured out.
"""

import json
import pathlib
from unittest.mock import AsyncMock

import pytest

from bus.schemas.events import ActionRequest, StateChangedEvent
from core.reflex.engine import ReflexEngine
from core.reflex.tool_registry import ToolInfo
from tests.core.reflex.fakes import reflex_agent


def _make_mock_registry() -> AsyncMock:
    """Build a mock ToolRegistry with standard home-service tools."""
    tools = [
        ToolInfo(
            name="lighting.dim_lights",
            description="Dim the lights in a room.",
            feature_name="lighting",
            feature_description="Smart home lighting controls.",
            target_service="home-service",
            audience="reflex",
            input_schema={
                "type": "object",
                "properties": {
                    "room": {"type": "string", "description": "The room to dim."},
                    "level": {"type": "integer", "description": "Brightness level 0-100."},
                },
                "required": ["room", "level"],
            },
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

    reply = json.dumps(
        {
            "decision": "act",
            "reason": "TV on, dim for viewing",
            "tool_name": "lighting.dim_lights",
            "target_service": "home-service",
            "parameters": {"room": "living_room", "level": 20},
        }
    )
    agent, _ = reflex_agent(reply)
    engine = ReflexEngine(
        preferences_dir=preferences_dir, tool_registry=_make_mock_registry(), agent=agent
    )

    proposal = await engine.process_event(tv_on_event)

    # Structured output verification (eval contract)
    assert proposal.decision == "act"
    assert proposal.reason == "TV on, dim for viewing"
    action = proposal.action
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

    agent, _ = reflex_agent(json.dumps({"action": "none"}))
    engine = ReflexEngine(
        preferences_dir=preferences_dir, tool_registry=_make_mock_registry(), agent=agent
    )

    proposal = await engine.process_event(temp_event)

    assert proposal.decision == "none"
    assert proposal.action is None
