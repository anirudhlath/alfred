"""Tests for evals.pipeline — inference orchestration with trace capture."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import pathlib

from bus.schemas.events import StateChangedEvent
from core.reflex.tool_registry import ToolInfo
from evals.models import Scenario
from evals.pipeline import EvalContext, run_scenario


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


def _mock_infer(response: dict[str, object]):
    """Create a mock infer function returning a fixed response."""

    async def _infer(prompt: str, model: str) -> dict[str, object]:
        return response

    return _infer


@pytest.mark.asyncio
async def test_run_scenario_captures_trace(tmp_path: pathlib.Path) -> None:
    """Pipeline captures full trace from prompt through response."""
    prefs_dir = str(tmp_path)

    mock_response = {
        "response": json.dumps({"action": "none"}),
        "prompt_tokens": 100,
        "completion_tokens": 8,
    }

    ctx = EvalContext(_make_tools(), prefs_dir, "test-model", infer=_mock_infer(mock_response))
    trace = await run_scenario(
        scenario=_make_scenario(),
        tools=_make_tools(),
        preferences_dir=prefs_dir,
        model="test-model",
        ctx=ctx,
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

    mock_response = {
        "response": json.dumps(
            {
                "tool_name": "lighting.dim_lights",
                "target_service": "home-service",
                "parameters": {"room": "living_room", "level": 20},
            }
        ),
        "prompt_tokens": 200,
        "completion_tokens": 30,
    }

    ctx = EvalContext(_make_tools(), prefs_dir, infer=_mock_infer(mock_response))
    trace = await run_scenario(
        scenario=_make_scenario(),
        tools=_make_tools(),
        preferences_dir=prefs_dir,
        ctx=ctx,
    )

    assert trace.parsed_action is not None
    assert trace.parsed_action.tool_name == "lighting.dim_lights"
