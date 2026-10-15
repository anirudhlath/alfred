"""Context assembler — builds System 2's instructions per request.

Two-stage context model:
  1. Involuntary recall — semantic search results injected automatically
  2. Deliberate recall — memory tools available during agentic reasoning

The prose sections come rendered from the prompt store
(``core.conscious.system2_prompts``); this module places the turn's data between them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from datetime import datetime

    from core.conscious.system2_prompts import System2Prompts
    from core.memory.vector_store import SearchResult


def assemble(
    prompts: System2Prompts,
    *,
    who: str,
    now: datetime | None = None,
    relevant_context: list[SearchResult] | None = None,
    tz_name: str = "UTC",
    routine_hint: str = "",
) -> str:
    """Build System 2's instructions for one turn: the rendered prose sections, with the
    turn's data (time, recall, routine hint) between them.

    The tools are not listed here: the model is offered them as tool definitions,
    under the names it must call them by (#299).
    """
    parts: list[str] = [prompts.personality.text]
    if prompts.voice_delivery is not None:
        parts.append(prompts.voice_delivery.text)
    parts.append("\n" + prompts.identity.text)
    if prompts.location is not None:
        parts.append("\n" + prompts.location.text)

    # Always, when known: time-based triggers and reminders need it.
    if now is not None:
        local = now.astimezone(ZoneInfo(tz_name))
        stamp = f"{local.strftime('%A')} {local.isoformat(timespec='seconds')}"
        parts.append(f"\n## Current Time\n{stamp} ({tz_name})")

    if prompts.integrations is not None:
        parts.append("\n" + prompts.integrations.text)

    # Involuntary recall, sir only.
    if who == "sir" and relevant_context:
        lines = [
            f"- [{r.metadata.type}] {r.content} (relevance: {r.score:.2f})"
            for r in relevant_context
        ]
        parts.append("\n## Relevant Context\n" + "\n".join(lines))

    if prompts.proactivity is not None:
        parts.append("\n" + prompts.proactivity.text)

    prompt = "\n".join(parts)
    if routine_hint:
        prompt += "\n\n" + routine_hint
    return prompt
