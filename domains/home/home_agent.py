"""Home domain sub-agent.

Routes actions from the Reflex Engine to the home-service microservice
via MCP tool calls over HTTP. Discovers service endpoints from the
Redis tool registry.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable

import httpx

from bus.schemas.events import ActionRequest, ActionResult
from core.reflex.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


class RedisLike(Protocol):
    """Protocol for async Redis operations used by HomeAgent."""

    def hget(self, name: str, key: str) -> Awaitable[str | bytes | None] | str | bytes | None: ...


class HomeAgent:
    """Sub-agent for the home domain."""

    def __init__(self, redis: RedisLike) -> None:
        self.redis = redis
        self._endpoint_cache: dict[str, str] = {}
        self._http_client: httpx.AsyncClient | None = None

    async def _get_service_endpoint(self, service_name: str) -> str | None:
        """Look up a service endpoint from the tool registry (cached)."""
        cached = self._endpoint_cache.get(service_name)
        if cached is not None:
            return cached

        manifest_json = await self.redis.hget(ToolRegistry.REGISTRY_KEY, service_name)  # type: ignore[misc]
        if manifest_json is None:
            return None
        raw = manifest_json.decode() if isinstance(manifest_json, bytes) else manifest_json
        manifest: dict[str, Any] = json.loads(raw)
        endpoint = manifest.get("service_endpoint")
        if endpoint is not None:
            self._endpoint_cache[service_name] = str(endpoint)
            return str(endpoint)
        return None

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
            if self._http_client is None or self._http_client.is_closed:
                self._http_client = httpx.AsyncClient(timeout=30.0)
            resp = await self._http_client.post(
                endpoint,
                json={
                    "method": action.tool_name,
                    "params": action.parameters,
                    "id": action.request_id,
                },
            )
            resp.raise_for_status()
            result_data: Any = resp.json()

            # MCP uses JSON-RPC: errors are in the response body, not HTTP status
            if isinstance(result_data, dict) and result_data.get("error"):
                return ActionResult(
                    source="home-agent",
                    request_id=action.request_id,
                    tool_name=action.tool_name,
                    status="error",
                    error=str(result_data["error"]),
                )

            return ActionResult(
                source="home-agent",
                request_id=action.request_id,
                tool_name=action.tool_name,
                status="success",
                result=result_data if isinstance(result_data, dict) else {"data": result_data},
            )
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            logger.error("Action execution failed: %s", error_msg)
            return ActionResult(
                source="home-agent",
                request_id=action.request_id,
                tool_name=action.tool_name,
                status="error",
                error=error_msg,
            )
