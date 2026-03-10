"""Reflex Engine — System 1 fast-path SLM inference.

Consumes events from Redis Streams, reads Markdown preferences,
prompts the local SLM, and produces structured ActionRequests.

Design for eval-ability: structured (event, preferences) in → structured action out.
No side effects during inference.
"""

from __future__ import annotations

import json
import logging

from bus.schemas.events import ActionRequest, StateChangedEvent
from core.reflex import ollama_client
from core.reflex.memory_reader import read_preferences
from sdk.alfred_sdk.telemetry import track_latency

logger = logging.getLogger(__name__)

_TARGET_SERVICE = "home-service"

SYSTEM_PROMPT = f"""You are Alfred's Reflex Engine — a fast-acting steward for a smart home.

Given an event from the smart home and the user's preferences, decide if an action is needed.

Rules:
- Only act if the event clearly matches a user preference
- If no action is needed, respond with: {{"action": "none"}}
- If an action IS needed, respond with:
  {{"tool_name": "<tool>", "target_service": "{_TARGET_SERVICE}", "parameters": {{<params>}}}}

Available tools (all on target_service "{_TARGET_SERVICE}"):
- smart_home.dim_lights(room: str, level: int 0-100)
- smart_home.turn_off_lights(room: str)
- smart_home.set_scene(scene_name: str)

Always set "target_service" to "{_TARGET_SERVICE}".
Respond ONLY with valid JSON. No explanation."""


class ReflexEngine:
    """The System 1 fast-path inference engine."""

    def __init__(self, preferences_dir: str) -> None:
        self.preferences_dir = preferences_dir
        # Cache preferences at init — they're read-only at runtime (Librarian Pattern)
        self._cached_preferences: str | None = None

    def _get_preferences(self) -> str:
        """Return cached preferences, loading from disk on first call."""
        if self._cached_preferences is None:
            self._cached_preferences = read_preferences(self.preferences_dir)
        return self._cached_preferences

    @track_latency(category="reflex")
    async def process_event(self, event: StateChangedEvent) -> ActionRequest | None:
        """Process a state change event and optionally produce an action."""
        preferences = self._get_preferences()

        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"## User Preferences\n{preferences}\n\n"
            f"## Event\n"
            f"Entity: {event.entity_id}\n"
            f"Domain: {event.domain}\n"
            f"Changed: {event.old_state} → {event.new_state}\n"
            f"Attributes: {json.dumps(event.attributes)}\n\n"
            f"## Your Decision (JSON only):"
        )

        response = await ollama_client.infer(prompt)
        return self._parse_response(response, event)

    def _parse_response(
        self, response: dict[str, object], event: StateChangedEvent
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

            target_service = str(parsed.get("target_service", _TARGET_SERVICE))
            if target_service != _TARGET_SERVICE:
                logger.warning("SLM returned unexpected target_service: %s", target_service)
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
