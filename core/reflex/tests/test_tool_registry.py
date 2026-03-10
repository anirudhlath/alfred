"""Tests for ToolRegistry — reads tool manifests from Redis."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from core.reflex.tool_registry import ToolInfo, ToolRegistry


def _make_manifest(service_name: str, features: list[dict]) -> str:
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
