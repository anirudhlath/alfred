"""Context reader — fetches and renders service context from Redis."""

from __future__ import annotations

import logging
import time
from typing import Any

from sdk.alfred_sdk.context import ContextSnapshot
from shared.streams import CONTEXT_KEY_PREFIX

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
    """Reads and caches service context from Redis."""

    CACHE_TTL = 300.0  # 5 minutes

    def __init__(self, redis_url: str, service_name: str = "home-service") -> None:
        self._redis_url = redis_url
        self._service_name = service_name
        self._cached_rendered: str = ""
        self._cache_time: float = 0.0
        self._cache_valid: bool = False

    async def get_rendered_context(self) -> str:
        """Return rendered Markdown context, re-fetching after TTL."""
        now = time.monotonic()
        if not self._cache_valid or (now - self._cache_time) > self.CACHE_TTL:
            import redis.asyncio as aioredis

            r = aioredis.from_url(self._redis_url)
            try:
                key = f"{CONTEXT_KEY_PREFIX}{self._service_name}"
                raw: bytes | None = await r.get(key)
                if raw:
                    snapshot = ContextSnapshot.model_validate_json(raw)
                    self._cached_rendered = render_snapshot(snapshot)
                else:
                    self._cached_rendered = ""
            finally:
                await r.aclose()
            self._cache_time = now
            self._cache_valid = True

        return self._cached_rendered
