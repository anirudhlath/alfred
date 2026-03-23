"""Reflex Engine — System 1 fast-path SLM inference.

Consumes events from Redis Streams, reads Markdown preferences,
prompts the local SLM, and produces structured ActionRequests.

Design for eval-ability: structured (event, preferences) in → structured action out.
No side effects during inference.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

from bus.schemas.events import ActionRequest, StateChangedEvent, TriggerFired
from core.memory.reader import MemoryReader
from core.reflex import ollama_client
from core.reflex.tool_registry import ToolInfo, ToolRegistry
from sdk.alfred_sdk.telemetry import track_latency
from shared.traced import traced

if TYPE_CHECKING:
    from core.reflex.context_reader import ContextReader

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_TEMPLATE = """\
You are Alfred's Reflex Engine — a fast-acting steward for a smart home.

Given an event from the smart home and the user's preferences, decide if an action is needed.

Rules:
- Only act if the event clearly matches a user preference
- If no action is needed, respond with: {{"action": "none"}}
- If an action IS needed, respond with:
  {{"tool_name": "<tool name>", "target_service": "<service>", "parameters": {{<params>}}}}

{tool_section}

Respond ONLY with valid JSON. No explanation."""

_TRIGGER_FIRED_PROMPT_TEMPLATE = """\
You are Alfred's Reflex Engine — a fast-acting steward for a smart home.

A trigger has fired. The user set up this trigger for a reason. Given the trigger details,
current home state, and user preferences, decide if any additional action is needed beyond
the notification already being sent to the user.

Rules:
- The user is ALREADY being notified about this trigger. You do NOT need to send a notification.
- Only act if an additional home automation action would be helpful given the context.
- If no additional action is needed, respond with: {{"action": "none"}}
- If an action IS needed, respond with:
  {{"tool_name": "<tool name>", "target_service": "<service>", "parameters": {{<params>}}}}

{tool_section}

Respond ONLY with valid JSON. No explanation."""


def _build_notification_body(event: TriggerFired) -> str:
    """Build a human-readable notification body from TriggerFired context."""
    parts: list[str] = []
    if event.context.get("event_entity"):
        entity = event.context["event_entity"]
        state = event.context.get("event_state")
        parts.append(f"{entity}: {state}" if state else str(entity))
    if event.context.get("evaluated_at"):
        parts.append(f"Fired at {event.context['evaluated_at']}")
    return " | ".join(parts) if parts else f"Trigger '{event.trigger_name}' fired"


def _build_tool_section(tools: list[ToolInfo]) -> str:
    """Build the 'Available tools' section of the system prompt."""
    if not tools:
        return "No tools available."

    lines: list[str] = ["Available tools:"]
    for t in tools:
        params_str = ", ".join(
            f"{p}: {info.get('type', 'Any')}" for p, info in t.parameters.items()
        )
        line = f"- {t.name}({params_str}) [service: {t.target_service}]"
        if t.description:
            line += f" — {t.description}"
        lines.append(line)

        # Include parameter descriptions (e.g. available entity values)
        for p, info in t.parameters.items():
            desc = info.get("description", "")
            if desc:
                lines.append(f"    {p}: {desc}")

    return "\n".join(lines)


class ReflexEngine:
    """The System 1 fast-path inference engine."""

    TOOL_CACHE_TTL = 300.0  # Re-read tool registry from Redis every 5 minutes

    def __init__(
        self,
        preferences_dir: str,
        tool_registry: ToolRegistry,
        context_reader: ContextReader | None = None,
        memory_reader: MemoryReader | None = None,
    ) -> None:
        self.preferences_dir = preferences_dir
        self._registry = tool_registry
        self._context_reader = context_reader
        self._memory_reader = memory_reader
        self._cached_preferences: str | None = None
        self._cached_tools: list[ToolInfo] | None = None
        self._cached_system_prompt: str | None = None
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

    def _build_system_prompt(self, tools: list[ToolInfo]) -> str:
        """Build the system prompt with dynamically discovered tools."""
        tool_section = _build_tool_section(tools)
        return _SYSTEM_PROMPT_TEMPLATE.format(tool_section=tool_section)

    async def _get_tools_and_prompt(self) -> tuple[list[ToolInfo], str]:
        """Return cached tools and system prompt, re-fetching after TTL expires."""
        now = time.monotonic()
        if self._cached_tools is None or (now - self._cache_time) > self.TOOL_CACHE_TTL:
            self._cached_tools = await self._registry.get_tools()
            self._cached_system_prompt = self._build_system_prompt(self._cached_tools)
            self._cache_time = now
        assert self._cached_system_prompt is not None  # narrowing for mypy
        return self._cached_tools, self._cached_system_prompt

    async def reload_tools(self) -> None:
        """Invalidate cached tools, forcing re-fetch on next event."""
        self._cached_tools = None
        self._cached_system_prompt = None

    def build_prompt(
        self,
        event: StateChangedEvent,
        preferences_text: str,
        tools: list[ToolInfo],
        context_text: str = "",
        *,
        _system_prompt: str | None = None,
    ) -> str:
        """Build the complete prompt for SLM inference.

        Public API for the evals pipeline. Returns the same prompt that
        process_event() sends to Ollama.

        The private ``_system_prompt`` kwarg lets process_event() pass its
        cached system prompt to avoid rebuilding on the hot path.
        """
        system_prompt = _system_prompt or self._build_system_prompt(tools)
        context_section = f"## Home State\n{context_text}\n\n" if context_text else ""
        return (
            f"{system_prompt}\n\n"
            f"{context_section}"
            f"## User Preferences\n{preferences_text}\n\n"
            f"## Event\n"
            f"Entity: {event.entity_id}\n"
            f"Domain: {event.domain}\n"
            f"Changed: {event.old_state} → {event.new_state}\n"
            f"Attributes: {json.dumps(event.attributes)}\n\n"
            f"## Your Decision (JSON only):"
        )

    def _parse_slm_json(
        self,
        response: dict[str, object],
        valid_services: set[str],
        log_label: str,
    ) -> ActionRequest | None:
        """Shared SLM JSON response parser."""
        try:
            raw = response.get("response", "")
            parsed = json.loads(str(raw))

            if parsed.get("action") == "none":
                logger.debug("No action for %s", log_label)
                return None

            tool_name = parsed.get("tool_name")
            if not tool_name:
                logger.warning("SLM response missing tool_name: %s", raw)
                return None

            target_service = str(parsed.get("target_service", ""))
            if target_service not in valid_services:
                logger.warning(
                    "SLM returned unregistered target_service: %s (valid: %s)",
                    target_service,
                    valid_services,
                )
                return None

            return ActionRequest(
                source="reflex-engine",
                target_service=target_service,
                tool_name=str(tool_name),
                parameters=dict(parsed.get("parameters", {})),
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Failed to parse SLM response: %s — %s", e, response)
            return None

    def parse_response(
        self,
        response: dict[str, object],
        event: StateChangedEvent,
        valid_services: set[str],
    ) -> ActionRequest | None:
        """Parse the SLM's JSON response into an ActionRequest or None.

        Public API for the evals pipeline. Same logic as used by process_event().
        """
        return self._parse_slm_json(response, valid_services, log_label=event.entity_id)

    def parse_trigger_response(
        self,
        response: dict[str, object],
        event: TriggerFired,
        valid_services: set[str],
    ) -> ActionRequest | None:
        """Parse SLM response for a TriggerFired event."""
        return self._parse_slm_json(response, valid_services, log_label=event.trigger_name)

    @traced(name="reflex.process_event")
    @track_latency(category="reflex")
    async def process_event(self, event: StateChangedEvent) -> ActionRequest | None:
        """Process a state change event and optionally produce an action."""
        preferences = self._get_preferences()
        tools, system_prompt = await self._get_tools_and_prompt()
        valid_services = ToolRegistry.get_registered_services(tools)

        context = ""
        if self._context_reader is not None:
            context = await self._context_reader.get_rendered_context()

        prompt = self.build_prompt(
            event, preferences, tools, context_text=context, _system_prompt=system_prompt
        )

        response = await ollama_client.infer(prompt)
        return self.parse_response(response, event, valid_services)

    @traced(name="reflex.process_trigger_fired")
    @track_latency(category="reflex")
    async def process_trigger_fired(self, event: TriggerFired) -> ActionRequest | None:
        """Process a TriggerFired event and optionally produce an action."""
        preferences = self._get_preferences()
        tools, _ = await self._get_tools_and_prompt()
        valid_services = ToolRegistry.get_registered_services(tools)

        tool_section = _build_tool_section(tools)
        system_prompt = _TRIGGER_FIRED_PROMPT_TEMPLATE.format(tool_section=tool_section)

        context = ""
        if self._context_reader is not None:
            context = await self._context_reader.get_rendered_context()

        context_section = f"## Home State\n{context}\n\n" if context else ""
        prompt = (
            f"{system_prompt}\n\n"
            f"{context_section}"
            f"## User Preferences\n{preferences}\n\n"
            f"## Trigger Fired\n"
            f"Name: {event.trigger_name}\n"
            f"Type: {event.trigger_type}\n"
            f"Context: {json.dumps(event.context)}\n\n"
            f"## Your Decision (JSON only):"
        )

        response = await ollama_client.infer(prompt)
        return self.parse_trigger_response(response, event, valid_services)
