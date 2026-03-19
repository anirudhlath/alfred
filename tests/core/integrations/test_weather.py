"""Tests for weather integration adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.integrations.base import IntegrationRequest
from core.integrations.registry import IntegrationRegistry
from core.integrations.weather import WeatherAdapter


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """Clear cached instances so each test gets a fresh adapter."""
    IntegrationRegistry._instances.pop("weather", None)


@pytest.fixture
def adapter() -> WeatherAdapter:
    return WeatherAdapter(latitude=40.7128, longitude=-74.0060)


@pytest.mark.asyncio
async def test_get_capabilities(adapter: WeatherAdapter) -> None:
    caps = await adapter.get_capabilities()
    names = [c.name for c in caps]
    assert "get_current" in names
    assert "get_forecast" in names


@pytest.mark.asyncio
async def test_execute_current_weather(adapter: WeatherAdapter) -> None:
    mock_response = {
        "current": {
            "temperature_2m": 72.0,
            "apparent_temperature": 70.0,
            "weather_code": 0,
            "wind_speed_10m": 5.0,
        }
    }
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response
        mock_resp.raise_for_status = lambda: None
        mock_get.return_value = mock_resp

        result = await adapter.execute(IntegrationRequest(action="get_current", params={}))

    assert "current" in result.data
    assert result.confidence > 0


@pytest.mark.asyncio
async def test_health_check_success(adapter: WeatherAdapter) -> None:
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json.return_value = {"current": {}}
        mock_get.return_value = mock_resp
        assert await adapter.health_check() is True
