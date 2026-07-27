"""tool_risk() — registry-backed risk lookup with benign default."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

_MANIFEST = json.dumps(
    {
        "service_name": "home-service",
        "features": [
            {
                "name": "home",
                "tools": [
                    {"name": "home.turn_on_lights", "risk": "benign"},
                    {"name": "home.set_climate", "risk": "elevated"},
                    {"name": "home.unlock_door", "risk": "critical"},
                    {"name": "home.legacy_tool"},
                ],
            }
        ],
    }
)


def _redis(manifest: bytes | None) -> AsyncMock:
    redis = AsyncMock()
    redis.hget = AsyncMock(return_value=manifest)
    return redis


@pytest.mark.asyncio
async def test_returns_declared_risk() -> None:
    from core.routing.risk import tool_risk

    redis = _redis(_MANIFEST.encode())
    assert await tool_risk(redis, "home-service", "home.unlock_door") == "critical"
    assert await tool_risk(redis, "home-service", "home.set_climate") == "elevated"
    assert await tool_risk(redis, "home-service", "home.turn_on_lights") == "benign"
    redis.hget.assert_called_with("alfred:tool_registry", "home-service")


@pytest.mark.asyncio
async def test_defaults_to_benign_when_absent() -> None:
    from core.routing.risk import tool_risk

    assert await tool_risk(_redis(None), "ghost-service", "x.y") == "benign"
    assert await tool_risk(_redis(_MANIFEST.encode()), "home-service", "home.ghost") == "benign"
    assert (
        await tool_risk(_redis(_MANIFEST.encode()), "home-service", "home.legacy_tool") == "benign"
    )
    assert await tool_risk(_redis(b"{not json"), "home-service", "home.unlock_door") == "benign"
