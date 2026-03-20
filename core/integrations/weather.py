"""Weather integration adapter — Open-Meteo (free, no API key)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from core.integrations.base import (
    Integration,
    IntegrationCapability,
    IntegrationRequest,
    IntegrationResult,
)
from core.integrations.registry import IntegrationRegistry
from core.integrations.sanitizer import sanitize_response

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.open-meteo.com/v1/forecast"


@IntegrationRegistry.register()
class WeatherAdapter(Integration):
    """Fetches weather data from Open-Meteo (free API, no key needed)."""

    name = "weather"
    category = "weather"

    def __init__(self, latitude: float = 0.0, longitude: float = 0.0) -> None:
        self._lat = latitude
        self._lon = longitude
        self._client = httpx.AsyncClient(timeout=10.0)

    async def get_capabilities(self) -> list[IntegrationCapability]:
        return [
            IntegrationCapability(
                name="get_current",
                description="Get current weather conditions",
                params_schema={"type": "object", "properties": {}},
            ),
            IntegrationCapability(
                name="get_forecast",
                description="Get weather forecast for next 7 days",
                params_schema={"type": "object", "properties": {}},
            ),
        ]

    async def execute(self, request: IntegrationRequest) -> IntegrationResult:
        client = self._client

        if request.action == "get_current":
            params: dict[str, str | float | int] = {
                "latitude": self._lat,
                "longitude": self._lon,
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                "temperature_unit": "fahrenheit",
            }
        elif request.action == "get_forecast":
            params = {
                "latitude": self._lat,
                "longitude": self._lon,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code",
                "temperature_unit": "fahrenheit",
                "forecast_days": 7,
            }
        else:
            return IntegrationResult(
                data={"error": f"Unknown action: {request.action}"},
                freshness=datetime.now(UTC),
                confidence=0.0,
            )

        resp = await client.get(_BASE_URL, params=params)
        resp.raise_for_status()
        raw_data = resp.json()
        clean_data = sanitize_response(raw_data)

        return IntegrationResult(
            data=clean_data if isinstance(clean_data, dict) else {"data": clean_data},
            freshness=datetime.now(UTC),
            confidence=0.95,
        )

    async def health_check(self) -> bool:
        try:
            client = self._client
            resp = await client.get(
                _BASE_URL,
                params={
                    "latitude": self._lat,
                    "longitude": self._lon,
                    "current": "temperature_2m",
                },
            )
            resp.raise_for_status()
            return True
        except Exception:
            return False
