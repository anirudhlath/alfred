"""ToolRegistry — reads tool manifests from Redis at runtime.

Thin read layer over the alfred:tool_registry Redis hash.
No caching — Redis HGETALL is sub-millisecond.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, get_args

from sdk.alfred_sdk.feature import (
    ToolAudience,
    ToolParameter,
    ToolRisk,
    check_object_schema,
    input_schema_from_parameters,
)
from shared.streams import TOOL_REGISTRY_KEY

if TYPE_CHECKING:
    from shared.types import AioRedis

logger = logging.getLogger(__name__)

# The audience tag that puts a tool in Reflex's prompt; untagged tools are "conscious".
REFLEX_AUDIENCE = "reflex"
# The values the SDK allows, read from its Literal types, never restated here.
_AUDIENCES: frozenset[str] = frozenset(get_args(ToolAudience))
_RISKS: frozenset[str] = frozenset(get_args(ToolRisk))
# ToolInfo fields that must be strings, possibly empty (`name` must also be non-empty).
_STRING_FIELDS = ("description", "feature_name", "feature_description", "target_service")


def legacy_input_schema(parameters: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build a tool's input schema from a manifest written before ``input_schema``.

    The one reader of a manifest's legacy ``parameters`` map (issue #325): the SDK no
    longer writes it, but services repin their SDK on their own schedule. Such a manifest
    gives every parameter ``"default": null`` (issue #300), so this reproduces what the
    model was offered before: a parameter is required when it says so, or, lacking a
    ``required`` key, when it has no ``default`` key. One difference is deliberate: a
    parameter without a description gets no ``description`` keyword, where System 2
    used to send ``"description": ""``, which tells a model nothing.

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


def _no_arguments() -> dict[str, Any]:
    """The schema of a tool that takes no arguments."""
    return {"type": "object", "properties": {}, "required": []}


@dataclass(frozen=True)
class ToolInfo:
    """A single tool discovered from the registry.

    ``input_schema`` is the one description of the tool's arguments: the schema its
    manifest ships, or, for a manifest written before ``input_schema`` existed, one the
    registry derives from that manifest's ``parameters`` (``legacy_input_schema``). The
    legacy map itself is not kept.
    """

    name: str
    description: str
    feature_name: str
    feature_description: str
    target_service: str
    audience: str = "conscious"
    risk: str = "benign"
    # The tool's arguments as one JSON Schema object, checked by `check_object_schema`.
    input_schema: dict[str, Any] = field(default_factory=_no_arguments)

    def __post_init__(self) -> None:
        # These reach prompts, tool-call payloads and dispatch, so a bad one fails here.
        if not isinstance(self.name, str) or not self.name:
            raise ValueError(f"tool name must be a non-empty string, not {self.name!r}")
        for field_name in _STRING_FIELDS:
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise ValueError(f"tool {field_name} must be a string, not {value!r}")
        if not isinstance(self.audience, str) or self.audience not in _AUDIENCES:
            raise ValueError(
                f"tool audience must be one of {sorted(_AUDIENCES)}, not {self.audience!r}"
            )
        if not isinstance(self.risk, str) or self.risk not in _RISKS:
            raise ValueError(f"tool risk must be one of {sorted(_RISKS)}, not {self.risk!r}")
        check_object_schema(self.input_schema)

    @property
    def parameter_names(self) -> tuple[str, ...]:
        """The tool's parameter names, in its schema's order (Reflex renders them)."""
        return tuple(self.input_schema.get("properties", {}))


def _input_schema(tool: Any) -> Any:
    """The schema a manifest's tool ships, else one derived from its legacy ``parameters``.

    An absent, null or empty ``input_schema`` means a manifest written before it existed.
    A shipped one is returned as is, for ``ToolInfo`` to check, and ``parameters`` is then
    never read.

    Args:
        tool: One entry of a feature's ``tools``, as read from the manifest.

    Returns:
        The tool's input schema.
    """
    shipped = tool.get("input_schema")
    if shipped is None or shipped == {}:
        return legacy_input_schema(tool.get("parameters", {}))
    return shipped


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

            features = manifest.get("features", [])
            if not isinstance(features, list):
                logger.warning(
                    "Skipping malformed manifest from service '%s': its features are not a list",
                    service_name,
                )
                continue

            # Parse features. A malformed feature is skipped whole; the rest still load.
            for feature in features:
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
                                feature_name=feature_name,
                                feature_description=feature_desc,
                                target_service=service_name,
                                audience=t.get("audience", "conscious"),
                                risk=t.get("risk", "benign"),
                                input_schema=_input_schema(t),
                            )
                        )
                    except (AttributeError, KeyError, TypeError, ValueError) as exc:
                        # One bad tool (an entry that is not an object, a missing or empty name, a
                        # description or feature name/description that is not a string, an audience
                        # or risk the SDK does not allow, a schema that is not an object schema, or,
                        # shipping none, malformed legacy parameters) must not take down every other
                        # tool. pydantic's ValidationError is a ValueError.
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
