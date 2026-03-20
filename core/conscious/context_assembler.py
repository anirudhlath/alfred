"""Context assembler — builds Claude's system prompt dynamically per request."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.identity.schemas import IdentityResult

logger = logging.getLogger(__name__)


class ContextAssembler:
    """Builds the system prompt for the Conscious Engine.

    Personality is always included. Personal data (preferences, integrations,
    memory) is excluded for guests.
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
        integrations_section: str,
        preferences_text: str,
        context_text: str,
        history: list[dict[str, str]],
        proactivity_level: str = "opinionated",
        episodic_text: str = "",
        procedural_text: str = "",
        channel: str = "",
        content_type: str = "text",
    ) -> str:
        """Build the complete system prompt for Claude."""
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

        # 3. Tools (always — guest can use allowed tools)
        if tools_section:
            parts.append(f"\n## Available Tools\n{tools_section}")

        # 4. Integrations (sir only)
        if identity.identity == "sir" and integrations_section:
            parts.append(f"\n## Available Integrations\n{integrations_section}")

        # 5. Preferences (sir only)
        if identity.identity == "sir" and preferences_text:
            parts.append(f"\n## Preferences\n{preferences_text}")

        # 6. Live context (always — HA state is not personal)
        if context_text:
            parts.append(f"\n## Current State\n{context_text}")

        # 7. Episodic memory (sir only)
        if identity.identity == "sir" and episodic_text:
            parts.append(f"\n## Recent Events\n{episodic_text}")

        # 8. Procedural memory (sir only)
        if identity.identity == "sir" and procedural_text:
            parts.append(f"\n## Known Routines\n{procedural_text}")

        # 9. Proactivity instruction (sir only)
        if identity.identity == "sir":
            parts.append(f"\n## Proactivity Level: {proactivity_level}")

        return "\n".join(parts)
