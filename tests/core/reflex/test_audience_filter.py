"""Audience tagging: registry parsing + Reflex prompt filtering."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from core.reflex.tool_registry import ToolInfo, ToolRegistry

if TYPE_CHECKING:
    from bus.schemas.events import StateChangedEvent

_MANIFEST = json.dumps(
    {
        "service_name": "home-service",
        "service_endpoint": "http://localhost:8000",
        "features": [
            {
                "name": "home",
                "description": "Home control",
                "tools": [
                    {
                        "name": "home.turn_on_lights",
                        "description": "Turn on lights",
                        "parameters": {},
                        "audience": "reflex",
                        "risk": "benign",
                    },
                    {
                        "name": "home.unlock_door",
                        "description": "Unlock a door",
                        "parameters": {},
                        "audience": "conscious",
                        "risk": "critical",
                    },
                    {
                        "name": "home.legacy_tool",
                        "description": "Registered before audience tagging",
                        "parameters": {},
                    },
                ],
            }
        ],
    }
)


@pytest.mark.asyncio
async def test_registry_parses_audience_and_risk_with_defaults() -> None:
    redis = AsyncMock()
    redis.hgetall = AsyncMock(return_value={b"home-service": _MANIFEST.encode()})
    tools = await ToolRegistry(redis).get_tools()

    by_name = {t.name: t for t in tools}
    assert len(tools) == 3  # get_tools returns ALL tools — Conscious sees everything
    assert by_name["home.turn_on_lights"].audience == "reflex"
    assert by_name["home.turn_on_lights"].risk == "benign"
    assert by_name["home.unlock_door"].audience == "conscious"
    assert by_name["home.unlock_door"].risk == "critical"
    assert by_name["home.legacy_tool"].audience == "conscious"  # default
    assert by_name["home.legacy_tool"].risk == "benign"  # default


@pytest.mark.asyncio
async def test_reflex_prompt_contains_only_reflex_audience_tools(
    tv_on_event: StateChangedEvent,
) -> None:
    from core.reflex.engine import ReflexEngine

    redis = AsyncMock()
    redis.hgetall = AsyncMock(return_value={b"home-service": _MANIFEST.encode()})

    captured: dict[str, str] = {}

    async def _capture(prompt: str) -> dict[str, object]:
        captured["prompt"] = prompt
        return {"response": json.dumps({"action": "none"})}

    with patch("core.reflex.ollama_client.infer", new=AsyncMock(side_effect=_capture)):
        engine = ReflexEngine(preferences_dir="/fake", tool_registry=ToolRegistry(redis))
        engine._cached_preferences = "- no preferences"
        await engine.process_event(tv_on_event)

    assert "home.turn_on_lights" in captured["prompt"]
    assert "home.unlock_door" not in captured["prompt"]
    assert "home.legacy_tool" not in captured["prompt"]


def test_toolinfo_defaults() -> None:
    tool = ToolInfo(
        name="x.y",
        description="",
        parameters={},
        feature_name="x",
        feature_description="",
        target_service="svc",
    )
    assert tool.audience == "conscious"
    assert tool.risk == "benign"
