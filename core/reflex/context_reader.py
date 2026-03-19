# core/reflex/context_reader.py
"""Context reader — fetches and renders service context from Redis."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from sdk.alfred_sdk.context import ContextSnapshot
from shared.streams import CONTEXT_KEY_PREFIX

if TYPE_CHECKING:
    from core.reflex.runner import AioRedis

logger = logging.getLogger(__name__)


def render_snapshot(snapshot: ContextSnapshot) -> str:
    """Render a ContextSnapshot into Markdown for the LLM prompt."""
    lines: list[str] = []

    for domain, entries in sorted(snapshot.controllable.items()):
        title = domain.replace("_", " ").title() + "s"
        lines.append(f"### {title}")
        for e in entries:
            attrs = ""
            if e.attributes:
                attr_parts = [f"{k}: {v}" for k, v in e.attributes.items()]
                attrs = f" ({', '.join(attr_parts)})"
            lines.append(f"- {e.entity_id}: {e.state}{attrs}")
        lines.append("")

    for domain, entries in sorted(snapshot.sensors.items()):
        title = domain.replace("_", " ").title() + "s"
        lines.append(f"### {title}")
        for e in entries:
            lines.append(f"- {e.entity_id}: {e.state}")
        lines.append("")

    return "\n".join(lines).rstrip()


class ContextReader:
    """Reads and caches service context from Redis.

    Scans all alfred:context:* keys to aggregate context from all
    registered services (not just home-service).
    """

    CACHE_TTL = 300.0  # 5 minutes

    def __init__(self, redis: AioRedis) -> None:
        self._redis = redis
        self._cached_rendered: str = ""
        self._cache_time: float = 0.0
        self._cache_valid: bool = False

    async def get_rendered_context(self) -> str:
        """Return rendered Markdown context from all services, re-fetching after TTL."""
        now = time.monotonic()
        if not self._cache_valid or (now - self._cache_time) > self.CACHE_TTL:
            merged = ContextSnapshot()

            async for key in self._redis.scan_iter(match=f"{CONTEXT_KEY_PREFIX}*", count=100):
                raw: bytes | None = await self._redis.get(key)
                if raw is None:
                    continue
                try:
                    snap = ContextSnapshot.model_validate_json(raw)
                except Exception as exc:
                    k = key.decode() if isinstance(key, bytes) else key
                    logger.warning("Failed to parse context from %s: %s", k, exc)
                    continue

                for domain, entries in snap.controllable.items():
                    merged.controllable.setdefault(domain, []).extend(entries)
                for domain, entries in snap.sensors.items():
                    merged.sensors.setdefault(domain, []).extend(entries)

            self._cached_rendered = render_snapshot(merged)
            self._cache_time = now
            self._cache_valid = True

        return self._cached_rendered
