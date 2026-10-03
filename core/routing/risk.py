"""Tool risk lookup — reads risk tags from the Redis tool registry.

Risk tiers ("benign" | "elevated" | "critical") are declared per tool in the
service manifests written to ``alfred:tool_registry`` by the SDK.

A *declared* tool with no ``risk`` field reads "benign" — legacy manifests
predate risk tagging. An *undeclared* tool reads "unknown" and the caller
must fail closed: the registry is the only evidence a tool exists at all, so
treating a name it has never heard of as benign hands the tiered-autonomy
gate to whatever the SLM happened to hallucinate (see ``docs/autonomy.md``).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from loguru import logger

from shared.streams import TOOL_REGISTRY_KEY, decode_stream_value

if TYPE_CHECKING:
    from shared.types import AioRedis

DEFAULT_RISK = "benign"
# Not in the registry: no evidence about this tool, so no autonomy for it.
UNKNOWN_RISK = "unknown"


async def tool_risk(redis: AioRedis, target_service: str, tool_name: str) -> str:
    """Return the declared risk for a tool, or "unknown" if the registry lacks it."""
    raw: bytes | str | None = await redis.hget(TOOL_REGISTRY_KEY, target_service)
    if raw is None:
        return UNKNOWN_RISK
    try:
        manifest: Any = json.loads(decode_stream_value(raw))
    except json.JSONDecodeError:
        logger.warning("Invalid manifest JSON for service '{}' — risk unknown", target_service)
        return UNKNOWN_RISK
    if not isinstance(manifest, dict):
        logger.warning("Non-object manifest JSON for service '{}' — risk unknown", target_service)
        return UNKNOWN_RISK
    # Wrong-shape entries are skipped, never trusted: a well-formed declaration still
    # answers (stray garbage must not hide a "critical" tag), anything else is unknown.
    for feature in _dicts(manifest.get("features")):
        for tool in _dicts(feature.get("tools")):
            if tool.get("name") == tool_name:
                return str(tool.get("risk", DEFAULT_RISK))
    return UNKNOWN_RISK


def _dicts(value: object) -> list[dict[str, Any]]:
    """The dict items of ``value`` when it is a list, else none — the manifest shape guard."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
