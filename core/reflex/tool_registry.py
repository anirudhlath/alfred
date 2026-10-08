"""ToolRegistry — reads tool manifests from Redis at runtime.

Thin read layer over the alfred:tool_registry Redis hash.
No caching — Redis HGETALL is sub-millisecond.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sdk.alfred_sdk.feature import (
    ToolParameter,
    check_object_schema,
    input_schema_from_parameters,
)
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

    Args:
        parameters: The manifest's ``parameters``: name → that parameter's metadata.

    Returns:
        An object schema with ``properties`` and ``required``.
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
        # Both reach every prompt and tool-call payload, so a bad one fails here instead.
        if not isinstance(self.name, str) or not self.name:
            raise ValueError(f"tool name must be a non-empty string, not {self.name!r}")
        if not isinstance(self.description, str):
            raise ValueError(f"tool description must be a string, not {self.description!r}")
        # Derived even when a schema ships: that validates `parameters`, which Reflex
        # renders, so a malformed one fails here rather than in a later prompt.
        derived = legacy_input_schema(self.parameters)
        if not self.input_schema:
            object.__setattr__(self, "input_schema", derived)


def _shipped_input_schema(schema: object) -> dict[str, Any]:
    """Check the ``input_schema`` a manifest ships; ``{}`` when it ships none.

    Args:
        schema: The tool's ``input_schema`` value, as read from the manifest.

    Returns:
        The schema, or ``{}`` when it is absent, null or empty (derive it instead).

    Raises:
        ValueError: The schema is present but not an object schema, by the SDK's
            ``check_object_schema``.
    """
    if schema is None or schema == {}:
        return {}
    return check_object_schema(schema)


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

            # Parse features. A malformed feature is skipped whole; the rest still load.
            for feature in manifest.get("features", []):
                if not isinstance(feature, dict):
                    logger.warning(
                        "Skipping malformed feature %r from service '%s': not an object",
                        feature,
                        service_name,
                    )
                    continue
                feature_name = feature.get("name", "")
                feature_desc = feature.get("description", "")
                feature_tools = feature.get("tools", [])
                if not isinstance(feature_tools, list):
                    logger.warning(
                        "Skipping malformed feature %r from service '%s': its tools are not a list",
                        feature_name,
                        service_name,
                    )
                    continue
                for t in feature_tools:
                    try:
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
                                input_schema=_shipped_input_schema(t.get("input_schema")),
                            )
                        )
                    except (AttributeError, KeyError, TypeError, ValueError) as exc:
                        # One bad tool (an entry that is not an object, a missing or empty
                        # name, a description that is not a string, malformed parameters,
                        # or a schema that is not an object schema) must not take down
                        # every other tool. pydantic's ValidationError is a ValueError.
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
