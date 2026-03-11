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

from bus.schemas.events import ActionRequest, StateChangedEvent
from core.reflex import ollama_client
from core.reflex.memory_reader import read_preferences
from core.reflex.tool_registry import ToolInfo, ToolRegistry
from sdk.alfred_sdk.telemetry import track_latency

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

    def __init__(self, preferences_dir: str, tool_registry: ToolRegistry) -> None:
        self.preferences_dir = preferences_dir
        self._registry = tool_registry
        self._cached_preferences: str | None = None
        self._cached_tools: list[ToolInfo] | None = None
        self._cached_system_prompt: str | None = None
        self._cache_time: float = 0.0

    def _get_preferences(self) -> str:
        """Return cached preferences, loading from disk on first call."""
        if self._cached_preferences is None:
            self._cached_preferences = read_preferences(self.preferences_dir)
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

    @track_latency(category="reflex")
    async def process_event(self, event: StateChangedEvent) -> ActionRequest | None:
        """Process a state change event and optionally produce an action."""
        preferences = self._get_preferences()
        tools, system_prompt = await self._get_tools_and_prompt()
        valid_services = ToolRegistry.get_registered_services(tools)

        prompt = (
            f"{system_prompt}\n\n"
            f"## User Preferences\n{preferences}\n\n"
            f"## Event\n"
            f"Entity: {event.entity_id}\n"
            f"Domain: {event.domain}\n"
            f"Changed: {event.old_state} → {event.new_state}\n"
            f"Attributes: {json.dumps(event.attributes)}\n\n"
            f"## Your Decision (JSON only):"
        )

        response = await ollama_client.infer(prompt)
        return self._parse_response(response, event, valid_services)

    def _parse_response(
        self,
        response: dict[str, object],
        event: StateChangedEvent,
        valid_services: set[str],
    ) -> ActionRequest | None:
        """Parse the SLM's JSON response into an ActionRequest or None."""
        try:
            raw = response.get("response", "")
            parsed = json.loads(str(raw))

            if parsed.get("action") == "none":
                logger.debug("No action for event %s", event.entity_id)
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
