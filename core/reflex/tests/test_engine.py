"""Tests for the Reflex Engine — System 1 SLM inference loop."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from bus.schemas.events import StateChangedEvent
from core.memory.reader import MemoryReader
from core.reflex.tool_registry import ToolInfo

if TYPE_CHECKING:
    from pathlib import Path


def _make_tools() -> list[ToolInfo]:
    """Build a standard test tool list."""
    return [
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
        ToolInfo(
            name="lighting.turn_off_lights",
            description="Turn off all lights in a room.",
            parameters={
                "room": {"type": "str", "description": "The room to turn off."},
            },
            feature_name="lighting",
            feature_description="Smart home lighting controls.",
            target_service="home-service",
        ),
    ]


@pytest.fixture
def mock_registry() -> AsyncMock:
    registry = AsyncMock()
    registry.get_tools = AsyncMock(return_value=_make_tools())
    return registry


@pytest.fixture
def mock_preferences() -> str:
    return (
        "# Lighting Preferences\n\n"
        "- I prefer dim lighting when watching TV or movies\n"
        "- Default brightness during daytime: 80%\n"
    )


@pytest.fixture
def mock_memory_reader(tmp_path: Path, mock_preferences: str) -> MemoryReader:
    """Build a MemoryReader whose get_preferences() returns mock_preferences."""
    prefs_dir = tmp_path / "preferences"
    profile_dir = tmp_path / "profile"
    prefs_dir.mkdir()
    profile_dir.mkdir()
    (prefs_dir / "prefs.md").write_text(mock_preferences)
    return MemoryReader(preferences_dir=prefs_dir, profile_dir=profile_dir)


@pytest.mark.asyncio
async def test_reflex_engine_produces_action(
    tv_on_event: StateChangedEvent,
    mock_memory_reader: MemoryReader,
    mock_registry: AsyncMock,
) -> None:
    from core.reflex.engine import ReflexEngine

    mock_ollama_response = {
        "response": json.dumps(
            {
                "tool_name": "lighting.dim_lights",
                "target_service": "home-service",
                "parameters": {"room": "living_room", "level": 20},
            }
        ),
        "prompt_tokens": 200,
        "completion_tokens": 30,
        "total_tokens": 230,
    }

    with patch(
        "core.reflex.ollama_client.infer",
        new_callable=AsyncMock,
        return_value=mock_ollama_response,
    ):
        engine = ReflexEngine(
            preferences_dir="/fake/prefs",
            tool_registry=mock_registry,
            memory_reader=mock_memory_reader,
        )
        action = await engine.process_event(tv_on_event)

    assert action is not None
    assert action.tool_name == "lighting.dim_lights"
    assert action.parameters["level"] == 20
    assert action.target_service == "home-service"


@pytest.mark.asyncio
async def test_reflex_engine_returns_none_for_no_action(
    mock_memory_reader: MemoryReader,
    mock_registry: AsyncMock,
) -> None:
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

    with patch(
        "core.reflex.ollama_client.infer",
        new_callable=AsyncMock,
        return_value=mock_ollama_response,
    ):
        engine = ReflexEngine(
            preferences_dir="/fake/prefs",
            tool_registry=mock_registry,
            memory_reader=mock_memory_reader,
        )
        action = await engine.process_event(boring_event)

    assert action is None


@pytest.mark.asyncio
async def test_reflex_engine_rejects_unknown_service(
    tv_on_event: StateChangedEvent,
    mock_memory_reader: MemoryReader,
    mock_registry: AsyncMock,
) -> None:
    from core.reflex.engine import ReflexEngine

    mock_ollama_response = {
        "response": json.dumps(
            {
                "tool_name": "lighting.dim_lights",
                "target_service": "rogue-service",
                "parameters": {"room": "living_room", "level": 20},
            }
        ),
    }

    with patch(
        "core.reflex.ollama_client.infer",
        new_callable=AsyncMock,
        return_value=mock_ollama_response,
    ):
        engine = ReflexEngine(
            preferences_dir="/fake/prefs",
            tool_registry=mock_registry,
            memory_reader=mock_memory_reader,
        )
        action = await engine.process_event(tv_on_event)

    assert action is None


@pytest.mark.asyncio
async def test_reflex_engine_prompt_contains_tools(
    mock_registry: AsyncMock,
    mock_memory_reader: MemoryReader,
) -> None:
    from core.reflex.engine import ReflexEngine

    engine = ReflexEngine(
        preferences_dir="/fake/prefs",
        tool_registry=mock_registry,
        memory_reader=mock_memory_reader,
    )

    tools = _make_tools()
    prompt = engine._build_system_prompt(tools)

    assert "lighting.dim_lights" in prompt
    assert "lighting.turn_off_lights" in prompt
    assert "home-service" in prompt
    assert "Dim the lights in a room." in prompt
    # Parameter descriptions appear in the prompt (e.g. available entity values)
    assert "The room to dim." in prompt
    assert "Brightness level 0-100." in prompt


@pytest.mark.asyncio
async def test_reflex_engine_prompt_contains_context(
    mock_registry: AsyncMock,
    mock_memory_reader: MemoryReader,
) -> None:
    from core.reflex.engine import ReflexEngine

    mock_context_reader = AsyncMock()
    mock_context_reader.get_rendered_context = AsyncMock(
        return_value="### Lights\n- light.living_room: on (brightness: 255)"
    )

    engine = ReflexEngine(
        preferences_dir="/fake/prefs",
        tool_registry=mock_registry,
        context_reader=mock_context_reader,
        memory_reader=mock_memory_reader,
    )

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

    with patch(
        "core.reflex.ollama_client.infer",
        new_callable=AsyncMock,
        return_value=mock_ollama_response,
    ) as mock_infer:
        await engine.process_event(boring_event)

    # Verify the prompt sent to Ollama contains the rendered context
    called_prompt = mock_infer.call_args[0][0]
    assert "## Home State" in called_prompt
    assert "light.living_room: on (brightness: 255)" in called_prompt
    # Context should appear before preferences
    assert called_prompt.index("## Home State") < called_prompt.index("## User Preferences")


# --- TriggerFired processing tests ---

from bus.schemas.events import TriggerFired


def _make_trigger_fired(
    name: str = "take medicine",
    trigger_type: str = "time",
    urgency: str = "informational",
) -> TriggerFired:
    return TriggerFired(
        trigger_id="t-1",
        trigger_name=name,
        trigger_type=trigger_type,
        context={"trigger_type": trigger_type, "evaluated_at": "2026-03-23T21:00:00Z"},
        urgency=urgency,
    )


@pytest.mark.asyncio
async def test_process_trigger_fired_produces_action(
    mock_memory_reader: MemoryReader,
    mock_registry: AsyncMock,
) -> None:
    from core.reflex.engine import ReflexEngine

    mock_ollama_response = {
        "response": json.dumps(
            {
                "tool_name": "lighting.dim_lights",
                "target_service": "home-service",
                "parameters": {"room": "bedroom", "level": 10},
            }
        ),
        "prompt_tokens": 200,
        "completion_tokens": 30,
        "total_tokens": 230,
    }

    with patch(
        "core.reflex.ollama_client.infer",
        new_callable=AsyncMock,
        return_value=mock_ollama_response,
    ):
        engine = ReflexEngine(
            preferences_dir="/fake/prefs",
            tool_registry=mock_registry,
            memory_reader=mock_memory_reader,
        )
        action = await engine.process_trigger_fired(_make_trigger_fired())

    assert action is not None
    assert action.tool_name == "lighting.dim_lights"
    assert action.source == "reflex-engine"


@pytest.mark.asyncio
async def test_process_trigger_fired_returns_none_for_no_action(
    mock_memory_reader: MemoryReader,
    mock_registry: AsyncMock,
) -> None:
    from core.reflex.engine import ReflexEngine

    mock_ollama_response = {
        "response": json.dumps({"action": "none"}),
        "prompt_tokens": 150,
        "completion_tokens": 10,
        "total_tokens": 160,
    }

    with patch(
        "core.reflex.ollama_client.infer",
        new_callable=AsyncMock,
        return_value=mock_ollama_response,
    ):
        engine = ReflexEngine(
            preferences_dir="/fake/prefs",
            tool_registry=mock_registry,
            memory_reader=mock_memory_reader,
        )
        action = await engine.process_trigger_fired(_make_trigger_fired())

    assert action is None


@pytest.mark.asyncio
async def test_process_trigger_fired_prompt_contains_trigger_details(
    mock_registry: AsyncMock,
    mock_memory_reader: MemoryReader,
) -> None:
    from core.reflex.engine import ReflexEngine

    mock_ollama_response = {
        "response": json.dumps({"action": "none"}),
    }

    with patch(
        "core.reflex.ollama_client.infer",
        new_callable=AsyncMock,
        return_value=mock_ollama_response,
    ) as mock_infer:
        engine = ReflexEngine(
            preferences_dir="/fake/prefs",
            tool_registry=mock_registry,
            memory_reader=mock_memory_reader,
        )
        await engine.process_trigger_fired(_make_trigger_fired())

    called_prompt = mock_infer.call_args[0][0]
    assert "## Trigger Fired" in called_prompt
    assert "take medicine" in called_prompt
    assert "time" in called_prompt
    assert "ALREADY being notified" in called_prompt


@pytest.mark.asyncio
async def test_process_trigger_fired_handles_malformed_json(
    mock_memory_reader: MemoryReader,
    mock_registry: AsyncMock,
) -> None:
    from core.reflex.engine import ReflexEngine

    mock_ollama_response = {"response": "not valid json at all"}

    with patch(
        "core.reflex.ollama_client.infer",
        new_callable=AsyncMock,
        return_value=mock_ollama_response,
    ):
        engine = ReflexEngine(
            preferences_dir="/fake/prefs",
            tool_registry=mock_registry,
            memory_reader=mock_memory_reader,
        )
        action = await engine.process_trigger_fired(_make_trigger_fired())

    assert action is None
