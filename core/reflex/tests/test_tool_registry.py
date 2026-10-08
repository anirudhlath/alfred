"""Tests for ToolRegistry — reads tool manifests from Redis."""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.reflex.tool_registry import ToolRegistry, legacy_input_schema


def _make_manifest(service_name: str, features: list[dict[str, Any]]) -> str:
    """Build a JSON manifest string for testing."""
    return json.dumps(
        {
            "service_name": service_name,
            "service_endpoint": "http://localhost:8000/mcp",
            "features": features,
        }
    )


LIGHTING_FEATURE = {
    "name": "lighting",
    "description": "Smart home lighting controls.",
    "tools": [
        {
            "name": "lighting.dim_lights",
            "description": "Dim the lights in a room.",
            "parameters": {
                "room": {"type": "str", "description": "The room to dim."},
                "level": {"type": "int", "description": "Brightness level 0-100."},
            },
        },
        {
            "name": "lighting.turn_off_lights",
            "description": "Turn off all lights in a room.",
            "parameters": {
                "room": {"type": "str", "description": "The room to turn off."},
            },
        },
    ],
}


@pytest.mark.asyncio
async def test_get_tools_parses_manifest() -> None:
    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {
        b"home-service": _make_manifest("home-service", [LIGHTING_FEATURE]).encode(),
    }

    registry = ToolRegistry(mock_redis)
    tools = await registry.get_tools()

    assert len(tools) == 2
    assert tools[0].name == "lighting.dim_lights"
    assert tools[0].target_service == "home-service"
    assert tools[0].feature_name == "lighting"
    assert tools[0].feature_description == "Smart home lighting controls."
    assert "room" in tools[0].parameters
    assert tools[0].parameters["room"]["type"] == "str"


@pytest.mark.asyncio
async def test_get_tools_empty_registry() -> None:
    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {}

    registry = ToolRegistry(mock_redis)
    tools = await registry.get_tools()

    assert tools == []


@pytest.mark.asyncio
async def test_get_tools_multiple_services() -> None:
    scenes_feature = {
        "name": "scenes",
        "description": "Scene management.",
        "tools": [
            {
                "name": "scenes.set_scene",
                "description": "Activate a scene.",
                "parameters": {"scene_name": {"type": "str", "description": "Scene name."}},
            }
        ],
    }
    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {
        b"home-service": _make_manifest("home-service", [LIGHTING_FEATURE]).encode(),
        b"other-service": _make_manifest("other-service", [scenes_feature]).encode(),
    }

    registry = ToolRegistry(mock_redis)
    tools = await registry.get_tools()

    assert len(tools) == 3
    services = {t.target_service for t in tools}
    assert services == {"home-service", "other-service"}


@pytest.mark.asyncio
async def test_get_tools_malformed_json_skipped() -> None:
    """Malformed JSON in a registry entry is skipped, not fatal."""
    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {
        b"good-service": _make_manifest("good-service", [LIGHTING_FEATURE]).encode(),
        b"bad-service": b"not valid json {{{",
    }

    registry = ToolRegistry(mock_redis)
    tools = await registry.get_tools()

    # Only tools from the good service are returned
    assert len(tools) == 2
    assert all(t.target_service == "good-service" for t in tools)


@pytest.mark.asyncio
async def test_get_services_returns_registered_services() -> None:
    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {
        b"home-service": _make_manifest("home-service", [LIGHTING_FEATURE]).encode(),
    }

    registry = ToolRegistry(mock_redis)
    tools = await registry.get_tools()
    services = registry.get_registered_services(tools)

    assert services == {"home-service"}


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_manifest", [b"123", b"[]", b'["ok"]', b'"text"', b"true", b"null"])
async def test_get_tools_non_object_manifest_skipped(
    bad_manifest: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    """Valid JSON that isn't an object is skipped with a WARNING naming the entry.

    ``json.loads`` accepts it, so the ``JSONDecodeError`` guard never fires, and
    ``.get`` on an int/list used to raise — taking down discovery for every service.
    """
    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {
        b"bad-service": bad_manifest,
        b"good-service": _make_manifest("good-service", [LIGHTING_FEATURE]).encode(),
    }

    with caplog.at_level(logging.WARNING, logger="core.reflex.tool_registry"):
        tools = await ToolRegistry(mock_redis).get_tools()

    assert [t.name for t in tools] == ["lighting.dim_lights", "lighting.turn_off_lights"]
    assert all(t.target_service == "good-service" for t in tools)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "bad-service" in warnings[0].getMessage()


def _registry_with(*features: dict[str, Any]) -> ToolRegistry:
    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {
        b"svc": _make_manifest("svc", list(features)).encode(),
    }
    return ToolRegistry(mock_redis)


@pytest.mark.asyncio
async def test_manifest_input_schema_is_used_verbatim() -> None:
    schema = {
        "type": "object",
        "properties": {"days": {"type": "array", "items": {"type": "string"}}},
        "required": ["days"],
    }
    feature = {
        "name": "f",
        "tools": [
            {
                "name": "f.go",
                "parameters": {"days": {"type": "list", "default": None, "required": True}},
                "input_schema": schema,
            }
        ],
    }
    tools = await _registry_with(feature).get_tools()
    assert tools[0].input_schema == schema


@pytest.mark.asyncio
async def test_old_sdk_manifest_keeps_todays_schema() -> None:
    # An older SDK wrote "default": null for every parameter and no input_schema.
    old = {
        "name": "lighting",
        "tools": [
            {
                "name": "lighting.dim_lights",
                "description": "Dim the lights in a room.",
                "parameters": {
                    "room": {"type": "str", "description": "The room to dim.", "default": None},
                    "level": {"type": "int", "description": "0-100.", "default": None},
                },
            }
        ],
    }
    tools = await _registry_with(old).get_tools()
    assert tools[0].input_schema == {
        "type": "object",
        "properties": {
            "room": {"type": "string", "description": "The room to dim."},
            "level": {"type": "integer", "description": "0-100."},
        },
        "required": [],
    }


@pytest.mark.asyncio
async def test_parameters_without_a_default_key_stay_required() -> None:
    tools = await _registry_with(LIGHTING_FEATURE).get_tools()
    assert tools[0].input_schema["required"] == ["room", "level"]


@pytest.mark.asyncio
async def test_malformed_tool_is_skipped_and_the_rest_still_load(
    caplog: pytest.LogCaptureFixture,
) -> None:
    feature = {
        "name": "f",
        "tools": [
            {"description": "no name"},
            {"name": "f.bad_params", "parameters": {"p": "not-a-dict"}},
            {"name": "f.bad_type", "parameters": {"p": {"type": 5}}},
            {"name": "f.good", "parameters": {}},
        ],
    }
    with caplog.at_level(logging.WARNING):
        tools = await _registry_with(feature, LIGHTING_FEATURE).get_tools()
    assert [t.name for t in tools] == [
        "f.good",
        "lighting.dim_lights",
        "lighting.turn_off_lights",
    ]
    assert caplog.text.count("Skipping malformed tool") == 3


@pytest.mark.parametrize(
    ("spec", "required"),
    [
        # An explicit required wins over a default being present...
        ({"type": "str", "default": "x", "required": True}, ["p"]),
        # ...and over a default being absent.
        ({"type": "str", "required": False}, []),
    ],
)
def test_legacy_input_schema_honours_an_explicit_required(
    spec: dict[str, Any], required: list[str]
) -> None:
    assert legacy_input_schema({"p": spec})["required"] == required


_VALID_SCHEMA = {"type": "object", "properties": {}, "required": []}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_tool",
    [
        pytest.param({"name": "f.bad", "input_schema": "oops"}, id="string-schema"),
        pytest.param({"name": "f.bad", "input_schema": {"foo": 1}}, id="non-object-schema"),
        pytest.param(
            {"name": "f.bad", "input_schema": {"type": "object", "properties": ["p"]}},
            id="non-dict-properties",
        ),
        pytest.param("oops", id="non-dict-tool"),
        pytest.param(
            {"name": "f.bad", "input_schema": _VALID_SCHEMA, "parameters": {"p": "x"}},
            id="schema-with-malformed-parameters",
        ),
    ],
)
async def test_tool_with_malformed_schema_or_parameters_is_skipped(
    bad_tool: Any, caplog: pytest.LogCaptureFixture
) -> None:
    feature = {"name": "f", "tools": [bad_tool, {"name": "f.good", "parameters": {}}]}
    with caplog.at_level(logging.WARNING, logger="core.reflex.tool_registry"):
        tools = await _registry_with(feature).get_tools()
    assert [t.name for t in tools] == ["f.good"]
    assert caplog.text.count("Skipping malformed tool") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("shipped", [None, {}], ids=["null", "empty"])
async def test_unset_input_schema_is_derived_from_parameters(shipped: Any) -> None:
    feature = {
        "name": "f",
        "tools": [
            {
                "name": "f.go",
                "parameters": {"room": {"type": "str"}},
                "input_schema": shipped,
            }
        ],
    }
    tools = await _registry_with(feature).get_tools()
    assert tools[0].input_schema == {
        "type": "object",
        "properties": {"room": {"type": "string"}},
        "required": ["room"],
    }
