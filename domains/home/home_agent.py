"""Home domain sub-agent.

Routes actions from the Reflex Engine to the home-service microservice
via MCP tool calls over HTTP. Discovers service endpoints from the
Redis tool registry.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from bus.schemas.events import ActionRequest, ActionResult

logger = logging.getLogger(__name__)


class HomeAgent:
    """Sub-agent for the home domain."""

    def __init__(self, redis: Any) -> None:
        self.redis: Any = redis

    async def _get_service_endpoint(self, service_name: str) -> str | None:
        """Look up a service endpoint from the tool registry."""
        manifest_json: str | None = await self.redis.hget("alfred:tool_registry", service_name)
        if manifest_json is None:
            return None
        manifest: dict[str, Any] = json.loads(manifest_json)
        endpoint = manifest.get("service_endpoint")
        return str(endpoint) if endpoint is not None else None

    async def execute_action(self, action: ActionRequest) -> ActionResult:
        """Execute an action by calling the target microservice's MCP endpoint."""
        endpoint = await self._get_service_endpoint(action.target_service)

        if endpoint is None:
            return ActionResult(
                source="home-agent",
                request_id=action.request_id,
                tool_name=action.tool_name,
                status="error",
                error=f"Service '{action.target_service}' not found in tool registry",
            )

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    endpoint,
                    json={
                        "method": action.tool_name,
                        "params": action.parameters,
                        "id": action.request_id,
                    },
                )
                resp.raise_for_status()
                result_data: Any = resp.json()

            return ActionResult(
                source="home-agent",
                request_id=action.request_id,
                tool_name=action.tool_name,
                status="success",
                result=result_data if isinstance(result_data, dict) else {"data": result_data},
            )
        except Exception as e:
            logger.error("Action execution failed: %s", e)
            return ActionResult(
                source="home-agent",
                request_id=action.request_id,
                tool_name=action.tool_name,
                status="error",
                error=str(e),
            )
