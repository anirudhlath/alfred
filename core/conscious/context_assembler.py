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

    def __init__(self, personality_path: str = "core/conscious/prompts/personality.md") -> None:
        self._personality = Path(personality_path).read_text()

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
    ) -> str:
        """Build the complete system prompt for Claude."""
        parts: list[str] = []

        # 1. Personality (always)
        parts.append(self._personality)

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
