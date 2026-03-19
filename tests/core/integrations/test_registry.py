"""Tests for IntegrationRegistry."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.integrations.base import (
    Integration,
    IntegrationCapability,
    IntegrationRequest,
    IntegrationResult,
)
from core.integrations.registry import IntegrationRegistry


class _WeatherIntegration(Integration):
    """Test-only integration class (not decorated — registered per-test)."""

    name = "weather"
    category = "weather"

    async def get_capabilities(self) -> list[IntegrationCapability]:
        return [IntegrationCapability(name="get_forecast", description="Get weather", params_schema={})]

    async def execute(self, request: IntegrationRequest) -> IntegrationResult:
        return IntegrationResult(data={"temp": 72}, freshness=datetime.now(UTC), confidence=0.9)

    async def health_check(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _reset_and_register() -> None:
    """Reset registry and re-register test integration before each test."""
    IntegrationRegistry._registry.clear()
    IntegrationRegistry._instances.clear()
    IntegrationRegistry._registry["weather"] = _WeatherIntegration


def test_register_and_get() -> None:
    assert "weather" in IntegrationRegistry.available()
    instance = IntegrationRegistry.get("weather")
    assert instance.name == "weather"


def test_get_unknown_raises() -> None:
    with pytest.raises(KeyError):
        IntegrationRegistry.get("nonexistent")


@pytest.mark.asyncio
async def test_get_all_capabilities() -> None:
    caps = await IntegrationRegistry.get_all_capabilities()
    assert len(caps) >= 1
    assert caps[0].name == "get_forecast"


@pytest.mark.asyncio
async def test_health_check_all() -> None:
    results = await IntegrationRegistry.health_check_all()
    assert results["weather"] is True
