"""ToolRegistry — reads tool manifests from Redis at runtime.

Thin read layer over the alfred:tool_registry Redis hash.
No caching — Redis HGETALL is sub-millisecond.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sdk.alfred_sdk.feature import ToolParameter, input_schema_from_parameters
from shared.streams import TOOL_REGISTRY_KEY

if TYPE_CHECKING:
    from shared.types import AioRedis

logger = logging.getLogger(__name__)

# The audience tag that puts a tool in Reflex's prompt; untagged tools are "conscious".
REFLEX_AUDIENCE = "reflex"


def legacy_input_schema(parameters: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build a tool's input schema from a manifest written before ``input_schema``.

    Such a manifest gives every parameter ``"default": null`` (#300), so this
    reproduces exactly what the model was offered before: a parameter is required
    when it says so, or, lacking a ``required`` key, when it has no ``default`` key.
    """
    return input_schema_from_parameters(
        {
            name: ToolParameter.model_validate(
                {"type": "str", **spec, "required": spec.get("required", "default" not in spec)}
            )
            for name, spec in parameters.items()
        }
    )


@dataclass(frozen=True)
class ToolInfo:
    """A single tool discovered from the registry."""

    name: str
    description: str
    parameters: dict[str, dict[str, Any]]
    feature_name: str
    feature_description: str
    target_service: str
    audience: str = "conscious"
    risk: str = "benign"
    # The tool's arguments as one JSON Schema object. Empty → derived from `parameters`.
    input_schema: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.input_schema:
            object.__setattr__(self, "input_schema", legacy_input_schema(self.parameters))


class ToolRegistry:
    """Reads tool manifests from Redis ``alfred:tool_registry``."""

    REGISTRY_KEY = TOOL_REGISTRY_KEY

    def __init__(self, redis: AioRedis | None = None) -> None:
        # ``redis`` may be None in offline contexts (e.g. evals) that only use the
        # static helpers; ``get_tools`` requires a live connection and guards for it.
        self._redis = redis

    async def get_tools(self) -> list[ToolInfo]:
        """Read all service manifests and return a flat list of tools."""
        if self._redis is None:
            raise RuntimeError("ToolRegistry.get_tools() requires a Redis connection")
        raw: dict[bytes | str, bytes | str] = await self._redis.hgetall(self.REGISTRY_KEY)

        tools: list[ToolInfo] = []
        for service_key, manifest_json in raw.items():
            service_name = service_key.decode() if isinstance(service_key, bytes) else service_key
            manifest_str = (
                manifest_json.decode() if isinstance(manifest_json, bytes) else manifest_json
            )

            try:
                manifest: Any = json.loads(manifest_str)
            except json.JSONDecodeError:
                logger.error("Invalid JSON in registry for service '%s'", service_name)
                continue
            # Same guard as service_credentials._parse_manifest: valid JSON need not be
            # an object, and one bad entry must not take down every service's tools.
            if not isinstance(manifest, dict):
                logger.warning(
                    "Non-object JSON in registry for service '%s' — skipped", service_name
                )
                continue

            # Parse features
            for feature in manifest.get("features", []):
                feature_name = feature.get("name", "")
                feature_desc = feature.get("description", "")
                for t in feature.get("tools", []):
                    try:
                        schema = t.get("input_schema")
                        tools.append(
                            ToolInfo(
                                name=t["name"],
                                description=t.get("description", ""),
                                parameters=t.get("parameters", {}),
                                feature_name=feature_name,
                                feature_description=feature_desc,
                                target_service=service_name,
                                audience=t.get("audience", "conscious"),
                                risk=t.get("risk", "benign"),
                                input_schema=schema if isinstance(schema, dict) else {},
                            )
                        )
                    except (AttributeError, KeyError, TypeError, ValueError) as exc:
                        # One bad tool must not take down every other tool (pydantic's
                        # ValidationError is a ValueError).
                        logger.warning(
                            "Skipping malformed tool %r from service '%s': %s",
                            t.get("name") if isinstance(t, dict) else t,
                            service_name,
                            exc,
                        )

        return tools

    @staticmethod
    def get_registered_services(tools: list[ToolInfo]) -> set[str]:
        """Extract the set of service names from a tool list."""
        return {t.target_service for t in tools}
