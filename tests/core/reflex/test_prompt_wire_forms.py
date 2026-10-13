"""Reflex's prompt is the same whichever wire form a manifest takes (issue #325).

Reflex is in its shadow week (issue #285), so a service repinning its SDK must not change
a byte of what the model sees. The expected prompt is a literal captured from the renderer
before issue #325 (which joined ``tool.parameters``): comparing the forms only to each
other would let both drift together.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any
from unittest.mock import AsyncMock

import pytest
from pydantic.json_schema import SkipJsonSchema

from bus.schemas.events import StateChangedEvent
from core.reflex.prompt import build_state_change_prompt, render_tools
from core.reflex.tool_registry import REFLEX_AUDIENCE, ToolInfo, ToolRegistry
from sdk.alfred_sdk.feature import BaseFeature, ToolMeta, ToolParameter, tool

# What an SDK before issue #300 wrote: `parameters` only, every one with "default": null.
OLD_SDK_TOOLS: list[dict[str, Any]] = [
    {
        "name": "home.scene_all_off",
        "description": "Turn everything off.",
        "audience": "reflex",
        "parameters": {},
    },
    {
        "name": "home.light_turn_on",
        "description": "Turn on a light.",
        "audience": "reflex",
        # Not alphabetical, on purpose: the prompt keeps the tool's own order.
        "parameters": {
            "target": {"type": "str", "description": "A room or a light.", "default": None},
            "brightness_pct": {"type": "int", "description": "0-100.", "default": None},
            "color_name": {"type": "str", "default": None},
        },
    },
    {
        "name": "home.fan_turn_off",
        "description": "",
        "audience": "reflex",
        "parameters": {"target": {"type": "str", "description": "A fan.", "default": None}},
    },
]

# The same tools as the SDK writes them since issue #325: `input_schema` only.
NEW_SDK_TOOLS: list[dict[str, Any]] = [
    {
        "name": "home.scene_all_off",
        "description": "Turn everything off.",
        "audience": "reflex",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "home.light_turn_on",
        "description": "Turn on a light.",
        "audience": "reflex",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "A room or a light."},
                "brightness_pct": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "default": None,
                    "description": "0-100.",
                },
                "color_name": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
            },
            "required": ["target"],
        },
    },
    {
        "name": "home.fan_turn_off",
        "description": "",
        "audience": "reflex",
        "input_schema": {
            "type": "object",
            "properties": {"target": {"type": "string", "description": "A fan."}},
            "required": ["target"],
        },
    },
]

# What the SDKs of issues #300/#301 wrote: both, describing the same arguments.
BOTH_FORMS_TOOLS: list[dict[str, Any]] = [
    {**old, "input_schema": new["input_schema"]}
    for old, new in zip(OLD_SDK_TOOLS, NEW_SDK_TOOLS, strict=True)
]

# Captured for OLD_SDK_TOOLS from the renderer before issue #325 (which joined
# `tool.parameters`).
EXPECTED_PROMPT = (
    "You are Alfred's Reflex Engine, the quiet steward of a home. One thing in the house "
    "just changed. Decide whether to do something about it.\n"
    "\n"
    "- act: the right move is obvious. Common sense or a stated preference makes it plainly\n"
    "  what the household wants, and doing it would surprise no one at home.\n"
    "- ask: a move is plausible, but you are not sure it is wanted.\n"
    "- none: nothing needs doing. This is the usual answer.\n"
    "\n"
    "Use only the tools below. Target a room by its name in the House section, or a device by\n"
    "its name.\n"
    "\n"
    'Respond with JSON only. Either {"decision": "none"} or\n'
    '{"decision": "act" | "ask", "reason": "<one short sentence>", "tool_name": "...",\n'
    ' "parameters": {...}}\n'
    "\n"
    "Tools:\n"
    "- home.fan_turn_off(target) [home-service]\n"
    "- home.light_turn_on(target, brightness_pct, color_name) [home-service]: Turn on a light.\n"
    "- home.scene_all_off() [home-service]: Turn everything off.\n"
    "\n"
    "## Preferences\n"
    "None recorded yet.\n"
    "\n"
    "## Now\n"
    "Thu 8 Oct, 03:30 (night)\n"
    "\n"
    "## House\n"
    "Live home state unavailable.\n"
    "\n"
    "## What changed\n"
    "light.tv_lamp: off → on\n"
    "\n"
    "## Decision (JSON only):"
)


async def _register(tools: list[dict[str, Any]]) -> list[ToolInfo]:
    """Register ``tools`` as one service's manifest and read them back."""
    manifest = {
        "service_name": "home-service",
        "service_endpoint": "http://home-service/mcp",
        "features": [{"name": "home", "description": "Home control.", "tools": tools}],
    }
    redis = AsyncMock()
    redis.hgetall.return_value = {b"home-service": json.dumps(manifest).encode()}
    registered = await ToolRegistry(redis).get_tools()
    assert len(registered) == len(tools), "a tool was skipped as malformed"
    return registered


async def _render(tools: list[dict[str, Any]]) -> str:
    """Register ``tools``, read them back, render the prompt."""
    registered = await _register(tools)
    return build_state_change_prompt(
        event=StateChangedEvent(
            source="home-service",
            domain="home",
            entity_id="light.tv_lamp",
            old_state="off",
            new_state="on",
        ),
        preferences="",
        tools=[t for t in registered if t.audience == REFLEX_AUDIENCE],
        entities=None,
        now=datetime(2026, 10, 8, 3, 30, tzinfo=UTC),
        tz_name="UTC",
    )


@pytest.mark.asyncio
async def test_old_and_new_sdk_manifests_render_the_same_prompt() -> None:
    old, new = await _render(OLD_SDK_TOOLS), await _render(NEW_SDK_TOOLS)
    assert old == new
    assert old == EXPECTED_PROMPT


@pytest.mark.asyncio
async def test_a_manifest_carrying_both_forms_renders_the_same_prompt() -> None:
    assert await _render(BOTH_FORMS_TOOLS) == EXPECTED_PROMPT


class _HandBuiltHome(BaseFeature):
    """The same tools built by hand from parameters, as home-service builds its own."""

    feature_name = "home"

    def get_tools(self) -> list[ToolMeta]:
        return [
            ToolMeta(name="home.scene_all_off", description="Turn everything off.", parameters={}),
            ToolMeta(
                name="home.light_turn_on",
                description="Turn on a light.",
                parameters={
                    "target": ToolParameter(
                        type="str", description="A room or a light.", required=True
                    ),
                    "brightness_pct": ToolParameter(type="int", description="0-100."),
                    "color_name": ToolParameter(type="str"),
                },
            ),
            ToolMeta(
                name="home.fan_turn_off",
                description="",
                parameters={"target": ToolParameter(type="str", required=True)},
            ),
        ]


class _DecoratedHome(BaseFeature):
    """The same tools written as ``@tool`` methods."""

    feature_name = "home"

    @tool(description="Turn everything off.")
    def scene_all_off(self) -> None: ...

    @tool(description="Turn on a light.")
    def light_turn_on(
        self, target: str, brightness_pct: int | None = None, color_name: str | None = None
    ) -> None: ...

    @tool(description="")
    def fan_turn_off(self, target: str) -> None: ...


@pytest.mark.asyncio
@pytest.mark.parametrize("feature", [_HandBuiltHome, _DecoratedHome])
async def test_what_the_sdk_writes_renders_the_same_prompt(feature: type[BaseFeature]) -> None:
    written = json.loads(json.dumps(feature().to_manifest().model_dump()))["tools"]
    assert all("parameters" not in t for t in written)
    for t in written:
        t["audience"] = REFLEX_AUDIENCE
    assert await _render(written) == EXPECTED_PROMPT


class _HiddenArgumentHome(BaseFeature):
    """A tool with a parameter its schema skips: it has a default, so the model never fills it."""

    feature_name = "home"

    @tool(audience="reflex", description="Turn on a light.")
    def light_turn_on(self, target: str, hidden: Annotated[int, SkipJsonSchema()] = 3) -> None: ...


@pytest.mark.asyncio
async def test_a_parameter_the_schema_skips_stays_out_of_the_prompt() -> None:
    # The legacy map listed `hidden` too, so the renderer before issue #325 named an argument
    # the model was never offered. The schema leaves it out, and Reflex now reads the schema.
    written = json.loads(json.dumps(_HiddenArgumentHome().to_manifest().model_dump()))["tools"]
    [registered] = await _register(written)
    assert registered.parameter_names == ("target",)
    assert render_tools([registered]) == (
        "Tools:\n- home.light_turn_on(target) [home-service]: Turn on a light."
    )
