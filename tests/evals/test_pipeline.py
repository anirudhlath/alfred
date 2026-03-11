"""Tests for evals.pipeline — inference orchestration with trace capture."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

if TYPE_CHECKING:
    import pathlib

from bus.schemas.events import StateChangedEvent
from core.reflex.tool_registry import ToolInfo
from evals.models import Scenario
from evals.pipeline import run_scenario


def _make_tools() -> list[ToolInfo]:
    return [
        ToolInfo(
            name="lighting.dim_lights",
            description="Dim the lights.",
            parameters={"room": {"type": "str"}, "level": {"type": "int"}},
            feature_name="lighting",
            feature_description="Lighting controls.",
            target_service="home-service",
        ),
    ]


def _make_scenario() -> Scenario:
    return Scenario(
        name="test_pipeline",
        event=StateChangedEvent(
            source="eval",
            domain="home",
            entity_id="media_player.tv",
            old_state="off",
            new_state="on",
            attributes={"friendly_name": "TV"},
        ),
        expected=None,
    )


@pytest.mark.asyncio
async def test_run_scenario_captures_trace(tmp_path: pathlib.Path) -> None:
    """Pipeline captures full trace from prompt through response."""
    prefs_dir = str(tmp_path)
    # No preferences files — empty prefs is fine for this test

    ollama_response = {
        "response": json.dumps({"action": "none"}),
        "prompt_tokens": 100,
        "completion_tokens": 8,
        "total_tokens": 108,
    }

    with patch(
        "core.reflex.ollama_client.infer",
        new_callable=AsyncMock,
        return_value=ollama_response,
    ):
        trace = await run_scenario(
            scenario=_make_scenario(),
            tools=_make_tools(),
            preferences_dir=prefs_dir,
            model="test-model",
        )

    assert trace.model == "test-model"
    assert "media_player.tv" in trace.prompt
    assert trace.raw_response == json.dumps({"action": "none"})
    assert trace.parsed_action is None
    assert trace.prompt_tokens == 100
    assert trace.completion_tokens == 8
    assert trace.latency_ms >= 0


@pytest.mark.asyncio
async def test_run_scenario_with_action(tmp_path: pathlib.Path) -> None:
    """Pipeline captures parsed ActionRequest."""
    prefs_dir = str(tmp_path)

    ollama_response = {
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
        return_value=ollama_response,
    ):
        trace = await run_scenario(
            scenario=_make_scenario(),
            tools=_make_tools(),
            preferences_dir=prefs_dir,
        )

    assert trace.parsed_action is not None
    assert trace.parsed_action.tool_name == "lighting.dim_lights"
