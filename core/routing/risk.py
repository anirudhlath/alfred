"""Tool risk lookup — reads risk tags from the Redis tool registry.

Risk tiers ("benign" | "elevated" | "critical") are declared per tool in the
service manifests written to ``alfred:tool_registry`` by the SDK. Absent
service, tool, or field defaults to "benign" (legacy manifests predate
risk tagging).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from loguru import logger

from shared.streams import TOOL_REGISTRY_KEY, decode_stream_value

if TYPE_CHECKING:
    from shared.types import AioRedis

DEFAULT_RISK = "benign"


async def tool_risk(redis: AioRedis, target_service: str, tool_name: str) -> str:
    """Return the declared risk for a tool, defaulting to "benign"."""
    raw: bytes | str | None = await redis.hget(TOOL_REGISTRY_KEY, target_service)
    if raw is None:
        return DEFAULT_RISK
    try:
        manifest: dict[str, Any] = json.loads(decode_stream_value(raw))
    except json.JSONDecodeError:
        logger.warning("Invalid manifest JSON for service '{}' — assuming benign", target_service)
        return DEFAULT_RISK
    for feature in manifest.get("features", []):
        for tool in feature.get("tools", []):
            if tool.get("name") == tool_name:
                return str(tool.get("risk", DEFAULT_RISK))
    return DEFAULT_RISK
