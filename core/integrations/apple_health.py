"""Apple Health integration adapter.

Requires an iOS bridge: Health Auto Export app or Shortcuts automation
pushing data to a local HTTP endpoint. This adapter reads from that endpoint.
"""

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


@IntegrationRegistry.register()
class AppleHealthAdapter(Integration):
    """Fetches health data from a local bridge endpoint."""

    name = "apple_health"
    category = "health"

    def __init__(self, endpoint: str = "") -> None:
        self._endpoint = endpoint
        self._client = httpx.AsyncClient(timeout=10.0)

    async def get_capabilities(self) -> list[IntegrationCapability]:
        return [
            IntegrationCapability(
                name="get_sleep",
                description="Get last night's sleep data",
                params_schema={"type": "object", "properties": {}},
            ),
            IntegrationCapability(
                name="get_activity",
                description="Get today's activity data",
                params_schema={"type": "object", "properties": {}},
            ),
        ]

    async def execute(self, request: IntegrationRequest) -> IntegrationResult:
        if not self._endpoint:
            return IntegrationResult(
                data={"error": "Health bridge not configured"},
                freshness=datetime.now(UTC),
                confidence=0.0,
            )

        try:
            resp = await self._client.get(f"{self._endpoint}/{request.action}")
            resp.raise_for_status()
            raw = resp.json()
            clean = sanitize_response(raw)
            return IntegrationResult(
                data=clean if isinstance(clean, dict) else {"data": clean},
                freshness=datetime.now(UTC),
                confidence=0.8,
            )
        except Exception as e:
            return IntegrationResult(
                data={"error": str(e)},
                freshness=datetime.now(UTC),
                confidence=0.0,
            )

    async def health_check(self) -> bool:
        if not self._endpoint:
            return False
        try:
            resp = await self._client.get(f"{self._endpoint}/health")
            return resp.status_code == 200
        except Exception:
            return False
