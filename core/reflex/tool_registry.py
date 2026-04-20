"""ToolRegistry — reads tool manifests from Redis at runtime.

Thin read layer over the alfred:tool_registry Redis hash.
No caching — Redis HGETALL is sub-millisecond.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from shared.streams import TOOL_REGISTRY_KEY

if TYPE_CHECKING:
    from shared.types import AioRedis

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolInfo:
    """A single tool discovered from the registry."""

    name: str
    description: str
    parameters: dict[str, dict[str, Any]]
    feature_name: str
    feature_description: str
    target_service: str


class ToolRegistry:
    """Reads tool manifests from Redis ``alfred:tool_registry``."""

    REGISTRY_KEY = TOOL_REGISTRY_KEY

    def __init__(self, redis: AioRedis) -> None:
        self._redis = redis

    async def get_tools(self) -> list[ToolInfo]:
        """Read all service manifests and return a flat list of tools."""
        raw: dict[bytes | str, bytes | str] = await self._redis.hgetall(  # type: ignore[misc]
            self.REGISTRY_KEY
        )

        tools: list[ToolInfo] = []
        for service_key, manifest_json in raw.items():
            service_name = service_key.decode() if isinstance(service_key, bytes) else service_key
            manifest_str = (
                manifest_json.decode() if isinstance(manifest_json, bytes) else manifest_json
            )

            try:
                manifest: dict[str, Any] = json.loads(manifest_str)
            except json.JSONDecodeError:
                logger.error("Invalid JSON in registry for service '%s'", service_name)
                continue

            # Parse features
            for feature in manifest.get("features", []):
                feature_name = feature.get("name", "")
                feature_desc = feature.get("description", "")
                for t in feature.get("tools", []):
                    tools.append(
                        ToolInfo(
                            name=t["name"],
                            description=t.get("description", ""),
                            parameters=t.get("parameters", {}),
                            feature_name=feature_name,
                            feature_description=feature_desc,
                            target_service=service_name,
                        )
                    )

        return tools

    @staticmethod
    def get_registered_services(tools: list[ToolInfo]) -> set[str]:
        """Extract the set of service names from a tool list."""
        return {t.target_service for t in tools}
