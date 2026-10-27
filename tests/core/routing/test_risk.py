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
async def test_unknown_when_the_tool_is_not_in_the_registry() -> None:
    """Fail closed: an unregistered tool has NO known risk, so it must not read benign.

    Defaulting to benign let a hallucinated tool name from the Reflex SLM through the
    tiered-autonomy gate and execute unconfirmed.
    """
    from core.routing.risk import tool_risk

    assert await tool_risk(_redis(None), "ghost-service", "x.y") == "unknown"
    assert await tool_risk(_redis(_MANIFEST.encode()), "home-service", "home.ghost") == "unknown"
    assert await tool_risk(_redis(b"{not json"), "home-service", "home.unlock_door") == "unknown"


@pytest.mark.asyncio
async def test_registered_tool_without_a_risk_field_is_benign() -> None:
    """Legacy manifests predate risk tagging — a *declared* tool still defaults benign."""
    from core.routing.risk import tool_risk

    assert (
        await tool_risk(_redis(_MANIFEST.encode()), "home-service", "home.legacy_tool") == "benign"
    )


_CRITICAL_DOOR = {"name": "home.unlock_door", "risk": "critical"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        (123, "unknown"),
        (["home.unlock_door"], "unknown"),
        ("home.unlock_door", "unknown"),
        ({"features": 123}, "unknown"),
        ({"features": {"name": "home"}}, "unknown"),
        ({"features": [123, ["x"]]}, "unknown"),
        ({"features": [{"tools": 123}]}, "unknown"),
        ({"features": [{"tools": [123, ["home.unlock_door"], "home.unlock_door"]}]}, "unknown"),
        # Garbage beside a well-formed declaration is skipped, never allowed to mask
        # it: reading "unknown" here would let System 2 run a critical tool unconfirmed.
        ({"features": [123, {"tools": 5}, {"tools": [["x"], 7, _CRITICAL_DOOR]}]}, "critical"),
    ],
)
async def test_wrong_shape_manifest_does_not_raise(manifest: object, expected: str) -> None:
    """Valid JSON of the wrong shape fails closed instead of raising.

    ``.get``/iteration on an int or list used to raise inside ``DomainRouter.route()``'s
    enforcement path — a 500 rather than a routed or rejected action.
    """
    from core.routing.risk import tool_risk

    redis = _redis(json.dumps(manifest).encode())
    assert await tool_risk(redis, "home-service", "home.unlock_door") == expected
