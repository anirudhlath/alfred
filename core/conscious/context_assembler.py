"""Context assembler — builds Claude's system prompt dynamically per request.

Two-stage context model:
  1. Involuntary recall — semantic search results injected automatically
  2. Deliberate recall — memory tools available during agentic reasoning

Static memory injection (preferences, episodic, procedural, HA state) has been
removed.  Context now surfaces via the unified context index or tool calls.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from core.identity.schemas import IdentityResult
    from core.memory.vector_store import SearchResult

logger = logging.getLogger(__name__)


class ContextAssembler:
    """Builds the system prompt for the Conscious Engine.

    Personality is always included. Personal data (integrations, memory)
    is excluded for guests.
    """

    _DEFAULT_PERSONALITY = Path(__file__).parent / "prompts" / "personality.md"
    _VOICE_DELIVERY = Path(__file__).parent / "prompts" / "voice_delivery.md"

    # Channels that have TTS output
    _VOICE_CHANNELS: frozenset[str] = frozenset({"web_pwa"})

    def __init__(self, personality_path: str | Path | None = None) -> None:
        path = Path(personality_path) if personality_path else self._DEFAULT_PERSONALITY
        self._personality = path.read_text()
        self._voice_delivery = (
            self._VOICE_DELIVERY.read_text() if self._VOICE_DELIVERY.exists() else ""
        )

    def assemble(
        self,
        identity: IdentityResult,
        tools_section: str,
        integrations_section: str = "",
        proactivity_level: str = "opinionated",
        now: datetime | None = None,
        relevant_context: list[SearchResult] | None = None,
        channel: str = "",
        content_type: str = "text",
    ) -> str:
        """Build the complete system prompt for Claude.

        Parameters that were removed (now handled via involuntary/deliberate recall):
            preferences_text, context_text, history, episodic_text, procedural_text
        """
        parts: list[str] = []

        # 1. Personality (always)
        parts.append(self._personality)

        # 1b. Voice delivery — only when TTS output will actually be produced
        # (audio input on a TTS-capable channel), not for text-only web sessions
        tts_active = content_type == "audio" and channel in self._VOICE_CHANNELS
        if tts_active and self._voice_delivery:
            parts.append(self._voice_delivery)

        # 2. Identity
        if identity.identity == "sir":
            parts.append("\n## Identity\nYou are speaking with sir (authenticated).")
        else:
            parts.append(
                "\n## Identity\nYou are speaking with a guest. "
                "Do NOT share any personal information about sir."
            )

        # 2b. Current time (always — needed for time-based triggers/reminders)
        if now is not None:
            parts.append(f"\n## Current Time\n{now.strftime('%Y-%m-%dT%H:%M:%SZ')}")

        # 3. Tools (always — guest can use allowed tools)
        if tools_section:
            parts.append(f"\n## Available Tools\n{tools_section}")

        # 4. Integrations hint (sir only) — actual capabilities are exposed as tools
        if identity.identity == "sir" and integrations_section:
            parts.append(
                "\n## Integrations\n"
                "You have integration tools (prefixed `integration_`) for calendar, "
                "weather, health, and finance data. Use them when sir asks about "
                "these topics — they are callable just like other tools."
            )

        # 5. Relevant context from involuntary recall
        if identity.identity == "sir" and relevant_context:
            ctx_lines: list[str] = []
            for r in relevant_context:
                ctx_lines.append(f"- [{r.metadata.type}] {r.content} (relevance: {r.score:.2f})")
            parts.append("\n## Relevant Context\n" + "\n".join(ctx_lines))

        # 6. Proactivity instruction (sir only)
        if identity.identity == "sir":
            parts.append(f"\n## Proactivity Level: {proactivity_level}")

        return "\n".join(parts)
