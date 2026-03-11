"""Tests for ReflexEngine public API: build_prompt, parse_response."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from bus.schemas.events import ActionRequest, StateChangedEvent
from core.reflex.engine import ReflexEngine
from core.reflex.tool_registry import ToolInfo


def _make_tools() -> list[ToolInfo]:
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
    ]


def _make_event() -> StateChangedEvent:
    return StateChangedEvent(
        source="eval",
        domain="home",
        entity_id="media_player.living_room_tv",
        old_state="off",
        new_state="on",
        attributes={"friendly_name": "Living Room TV"},
    )


class TestBuildPrompt:
    def test_returns_string_with_event_details(self) -> None:
        """build_prompt includes event entity_id and state change."""
        registry = AsyncMock()
        engine = ReflexEngine(preferences_dir="/tmp/fake", tool_registry=registry)
        tools = _make_tools()

        prompt = engine.build_prompt(
            event=_make_event(),
            preferences_text="- dim lights when TV on",
            tools=tools,
        )

        assert isinstance(prompt, str)
        assert "media_player.living_room_tv" in prompt
        assert "off" in prompt
        assert "on" in prompt
        assert "dim lights when TV on" in prompt

    def test_includes_tool_names(self) -> None:
        """build_prompt includes discovered tool names."""
        registry = AsyncMock()
        engine = ReflexEngine(preferences_dir="/tmp/fake", tool_registry=registry)
        tools = _make_tools()

        prompt = engine.build_prompt(
            event=_make_event(),
            preferences_text="prefs",
            tools=tools,
        )

        assert "lighting.dim_lights" in prompt
        assert "home-service" in prompt

    def test_empty_tools(self) -> None:
        """build_prompt works with no tools."""
        registry = AsyncMock()
        engine = ReflexEngine(preferences_dir="/tmp/fake", tool_registry=registry)

        prompt = engine.build_prompt(
            event=_make_event(),
            preferences_text="prefs",
            tools=[],
        )

        assert "No tools available" in prompt


class TestParseResponse:
    def test_valid_action(self) -> None:
        """parse_response returns ActionRequest for valid tool response."""
        registry = AsyncMock()
        engine = ReflexEngine(preferences_dir="/tmp/fake", tool_registry=registry)
        event = _make_event()

        response: dict[str, object] = {
            "response": json.dumps({
                "tool_name": "lighting.dim_lights",
                "target_service": "home-service",
                "parameters": {"room": "living_room", "level": 20},
            }),
        }

        result = engine.parse_response(response, event, {"home-service"})

        assert result is not None
        assert isinstance(result, ActionRequest)
        assert result.tool_name == "lighting.dim_lights"
        assert result.target_service == "home-service"

    def test_no_action(self) -> None:
        """parse_response returns None for action=none."""
        registry = AsyncMock()
        engine = ReflexEngine(preferences_dir="/tmp/fake", tool_registry=registry)
        event = _make_event()

        response: dict[str, object] = {
            "response": json.dumps({"action": "none"}),
        }

        result = engine.parse_response(response, event, {"home-service"})
        assert result is None

    def test_invalid_service_returns_none(self) -> None:
        """parse_response rejects responses with unregistered target_service."""
        registry = AsyncMock()
        engine = ReflexEngine(preferences_dir="/tmp/fake", tool_registry=registry)
        event = _make_event()

        response: dict[str, object] = {
            "response": json.dumps({
                "tool_name": "lighting.dim_lights",
                "target_service": "fake-service",
                "parameters": {},
            }),
        }

        result = engine.parse_response(response, event, {"home-service"})
        assert result is None

    def test_malformed_json_returns_none(self) -> None:
        """parse_response returns None for unparseable response."""
        registry = AsyncMock()
        engine = ReflexEngine(preferences_dir="/tmp/fake", tool_registry=registry)
        event = _make_event()

        response: dict[str, object] = {"response": "not json at all"}

        result = engine.parse_response(response, event, {"home-service"})
        assert result is None
