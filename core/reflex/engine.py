"""Reflex Engine — System 1 fast-path SLM inference.

One event in, a ``Decided`` out (#285): the model's proposal (act, ask, none or invalid) and
the outcome it comes to. Reads preferences, Reflex's tools, live state and what people just
changed, builds the prompt with ``core.reflex.prompt`` and decides through the Reflex agent
(``core.reflex.agent``), which parses the reply with ``core.reflex.decision``. Never reverse a
person (``core.reflex.reversal``): a proposal that would undo what a person just changed is
checked after the model answers and ends as ``would_reverse``.

Design for eval-ability: structured (event, preferences, live state) in → structured
decision out. The engine has no effect on the house — in shadow mode nothing a proposal names
is executed — and writes nothing: the runner records, counts and publishes the decision.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from core.memory.reader import MemoryReader
from core.reflex.agent import decide
from core.reflex.outcome import Decided, first_outcome
from core.reflex.prompt import build_state_change_prompt, build_trigger_prompt, index_snapshot
from core.reflex.prose import fetch_prose, repo_prose
from core.reflex.reversal import reversed_entities
from core.reflex.settings import ReflexSettings, load_settings
from core.reflex.tool_registry import REFLEX_AUDIENCE
from sdk.alfred_sdk.telemetry import track_latency
from shared.traced import traced

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pydantic_ai import Agent

    from bus.schemas.events import ReflexProposal, StateChangedEvent, TriggerFired
    from core.reflex.context_reader import ContextReader
    from core.reflex.prompt import LiveEntity, ReflexProse
    from core.reflex.reversal import PersonChange, PersonChanges
    from core.reflex.tool_registry import ToolInfo, ToolRegistry
    from shared.prompts.store import PromptStore
    from shared.types import AioRedis

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


def _decided(
    proposal: ReflexProposal,
    prompt: str,
    recent: dict[str, PersonChange],
    entities: Mapping[str, LiveEntity] | None,
) -> Decided:
    """The proposal, its outcome and what it was decided from.

    With no live state there is nothing to resolve a target against, so nothing is reversed:
    Reflex sees no House then either, and executes nothing.
    """
    reversed_ = (
        []
        if proposal.action is None
        else reversed_entities(proposal.action, recent, entities or {})
    )
    return Decided(
        proposal=proposal, outcome=first_outcome(proposal, reversed_), prompt=prompt, recent=recent
    )


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
        agent: Agent[list[ToolInfo], ReflexProposal],
        clock: Callable[[], datetime] = _utc_now,
        prompts: PromptStore | None = None,
        redis: AioRedis | None = None,
        person_changes: PersonChanges | None = None,
    ) -> None:
        self.preferences_dir = preferences_dir
        self._registry = tool_registry
        self._context_reader = context_reader
        self._memory_reader = memory_reader
        self._agent = agent
        self._clock = clock
        self._prompts = prompts
        self._redis = redis
        self._person_changes = person_changes
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

    async def _prose(self, origin: Literal["state_change", "trigger_fired"]) -> ReflexProse:
        """The prompt's intro and rules: the store's (a cache hit once warm), else the repo's."""
        if self._prompts is None:
            return repo_prose(origin)
        return await fetch_prose(self._prompts, origin)

    async def _recent_changes(self) -> dict[str, PersonChange]:
        """What people changed within the window, read afresh for each decision.

        The window is the owner's setting (the defaults without Redis). With no record of
        changes there are none. A Redis error propagates: Reflex does not decide blind to a
        person's change, and the event is retried.
        """
        if self._person_changes is None:
            return {}
        settings = ReflexSettings() if self._redis is None else await load_settings(self._redis)
        window = timedelta(minutes=settings.reverse_window_minutes)
        return await self._person_changes.recent(window)

    @traced(name="reflex.process_event")
    @track_latency(category="reflex")
    async def process_event(self, event: StateChangedEvent) -> Decided:
        """Decide about one state change. Raises if the model call or a Redis read fails."""
        tools = await self._get_tools()
        entities, tz_name = await self._live_context()
        recent = await self._recent_changes()
        prompt = build_state_change_prompt(
            event=event,
            preferences=self._get_preferences(),
            tools=tools,
            entities=entities,
            now=self._clock(),
            tz_name=tz_name,
            prose=await self._prose("state_change"),
            leave_alone=list(recent),
        )
        proposal = await decide(self._agent, prompt, tools)
        return _decided(proposal, prompt, recent, entities)

    @traced(name="reflex.process_trigger_fired")
    @track_latency(category="reflex")
    async def process_trigger_fired(self, event: TriggerFired) -> Decided:
        """Decide whether a fired trigger calls for more than its notification."""
        tools = await self._get_tools()
        entities, tz_name = await self._live_context()
        recent = await self._recent_changes()
        prompt = build_trigger_prompt(
            event=event,
            preferences=self._get_preferences(),
            tools=tools,
            entities=entities,
            now=self._clock(),
            tz_name=tz_name,
            prose=await self._prose("trigger_fired"),
            leave_alone=list(recent),
        )
        proposal = await decide(self._agent, prompt, tools)
        return _decided(proposal, prompt, recent, entities)
