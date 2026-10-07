# core/reflex/context_reader.py
"""Context reader — renders every service's live state for the prompts.

Reads through the SDK's ``read_live_state()`` on every call. There is no cache: a
change a service has written shows on the very next read.
"""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, Any

from sdk.alfred_sdk.live_state import read_live_state

if TYPE_CHECKING:
    from sdk.alfred_sdk.context import ContextSnapshot
    from shared.types import AioRedis

# Said in place of the state section when no service has live state (HA disconnected),
# so a model never reads an empty section as "nothing is on".
LIVE_STATE_UNAVAILABLE = "Live home state unavailable."


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
    """Reads every registered service's live state, fresh on every call."""

    def __init__(self, redis: AioRedis) -> None:
        self._redis = redis

    async def get_rendered_context(self) -> str:
        """Markdown of the live state, or LIVE_STATE_UNAVAILABLE when there is none."""
        snapshot = await read_live_state(self._redis)
        if snapshot is None:
            return LIVE_STATE_UNAVAILABLE
        return render_snapshot(snapshot)

    async def get_entity_states(
        self,
        patterns: list[str] | None = None,
    ) -> list[dict[str, Any]] | None:
        """Entity states, optionally filtered by glob patterns; None when there is no live state."""
        snapshot = await read_live_state(self._redis)
        if snapshot is None:
            return None

        all_entities: list[dict[str, Any]] = []
        # Walk both buckets rather than merging their dicts: a domain can be in both, and a
        # merge would drop its controllable entries.
        for entries in (*snapshot.controllable.values(), *snapshot.sensors.values()):
            for e in entries:
                entity_dict: dict[str, Any] = {"entity_id": e.entity_id, "state": e.state}
                if e.attributes:
                    entity_dict["attributes"] = e.attributes
                all_entities.append(entity_dict)

        if patterns:
            return [
                entity
                for entity in all_entities
                if any(fnmatch.fnmatch(entity["entity_id"], p) for p in patterns)
            ]
        return all_entities
