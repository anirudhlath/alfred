"""Reflex Engine — System 1 fast-path SLM inference.

One event in, a ReflexProposal out (#285): act, ask, none or invalid. Reads
preferences, Reflex's tools and live state, builds the prompt with
``core.reflex.prompt`` and parses the reply with ``core.reflex.decision``.

Design for eval-ability: structured (event, preferences, live state) in → structured
proposal out. No side effects — in shadow mode nothing a proposal names is executed.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.memory.reader import MemoryReader
from core.reflex import inference
from core.reflex.decision import parse_decision
from core.reflex.prompt import build_state_change_prompt, build_trigger_prompt, index_snapshot
from core.reflex.tool_registry import REFLEX_AUDIENCE
from sdk.alfred_sdk.telemetry import track_latency
from shared.traced import traced

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from bus.schemas.events import ReflexProposal, StateChangedEvent, TriggerFired
    from core.reflex.context_reader import ContextReader
    from core.reflex.prompt import LiveEntity
    from core.reflex.tool_registry import ToolInfo, ToolRegistry

logger = logging.getLogger(__name__)


def build_notification_body(event: TriggerFired) -> str:
    """Build a human-readable notification body from TriggerFired context."""
    parts: list[str] = []
    if event.context.get("event_entity"):
        entity = event.context["event_entity"]
        state = event.context.get("event_state")
        parts.append(f"{entity}: {state}" if state else str(entity))
    if event.context.get("evaluated_at"):
        parts.append(f"Fired at {event.context['evaluated_at']}")
    return " | ".join(parts) if parts else f"Trigger '{event.trigger_name}' fired"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ReflexEngine:
    """The System 1 fast-path inference engine."""

    TOOL_CACHE_TTL = 300.0  # Re-read tool registry from Redis every 5 minutes

    def __init__(
        self,
        preferences_dir: str,
        tool_registry: ToolRegistry,
        context_reader: ContextReader | None = None,
        memory_reader: MemoryReader | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.preferences_dir = preferences_dir
        self._registry = tool_registry
        self._context_reader = context_reader
        self._memory_reader = memory_reader
        self._clock = clock
        self._cached_preferences: str | None = None
        self._cached_tools: list[ToolInfo] | None = None
        self._cache_time: float = 0.0

    def _get_preferences(self) -> str:
        """Return cached preferences, loading from disk on first call."""
        if self._cached_preferences is None:
            if self._memory_reader is not None:
                self._cached_preferences = self._memory_reader.get_preferences()
            else:
                from pathlib import Path

                reader = MemoryReader(
                    preferences_dir=Path(self.preferences_dir),
                    profile_dir=Path(self.preferences_dir).parent / "profile",
                )
                self._cached_preferences = reader.get_preferences()
        return self._cached_preferences

    async def _get_tools(self) -> list[ToolInfo]:
        """Reflex-audience tools, TTL-cached.

        Only tools tagged ``REFLEX_AUDIENCE`` reach the prompt — the first layer
        of tiered autonomy (contract C9). Untagged tools default to "conscious".
        """
        now = time.monotonic()
        if self._cached_tools is None or (now - self._cache_time) > self.TOOL_CACHE_TTL:
            all_tools = await self._registry.get_tools()
            self._cached_tools = [t for t in all_tools if t.audience == REFLEX_AUDIENCE]
            self._cache_time = now
        return self._cached_tools

    async def reload_tools(self) -> None:
        """Invalidate cached tools, forcing re-fetch on next event."""
        self._cached_tools = None

    async def _live_context(self) -> tuple[Mapping[str, LiveEntity] | None, str]:
        """Live state, indexed, and the user's timezone. Offline (no reader): none, UTC."""
        if self._context_reader is None:
            return None, "UTC"
        snapshot = await self._context_reader.get_snapshot()
        tz_name = await self._context_reader.get_user_timezone()
        return (None if snapshot is None else index_snapshot(snapshot)), tz_name

    @traced(name="reflex.process_event")
    @track_latency(category="reflex")
    async def process_event(self, event: StateChangedEvent) -> ReflexProposal:
        """Decide about one state change. Raises if the model call fails."""
        tools = await self._get_tools()
        entities, tz_name = await self._live_context()
        prompt = build_state_change_prompt(
            event=event,
            preferences=self._get_preferences(),
            tools=tools,
            entities=entities,
            now=self._clock(),
            tz_name=tz_name,
        )
        response = await inference.infer(prompt)
        return parse_decision(str(response.get("response", "")), tools)

    @traced(name="reflex.process_trigger_fired")
    @track_latency(category="reflex")
    async def process_trigger_fired(self, event: TriggerFired) -> ReflexProposal:
        """Decide whether a fired trigger calls for more than its notification."""
        tools = await self._get_tools()
        entities, tz_name = await self._live_context()
        prompt = build_trigger_prompt(
            event=event,
            preferences=self._get_preferences(),
            tools=tools,
            entities=entities,
            now=self._clock(),
            tz_name=tz_name,
        )
        response = await inference.infer(prompt)
        return parse_decision(str(response.get("response", "")), tools)
