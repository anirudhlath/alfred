"""A Reflex agent over a scripted model, for tests that need an engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.reflex.agent import build_reflex_agent
from core.reflex.outcome import Decided, first_outcome
from tests.llm_fakes import Script

if TYPE_CHECKING:
    from pydantic_ai import Agent

    from bus.schemas.events import ReflexOutcome, ReflexProposal
    from core.reflex.reversal import PersonChange
    from core.reflex.tool_registry import ToolInfo
    from tests.llm_fakes import Reply


def reflex_agent(*replies: Reply) -> tuple[Agent[list[ToolInfo], ReflexProposal], Script]:
    """The Reflex agent over a ``Script`` that answers with *replies* in order.

    The script records every request, so a test reads the prompt the engine sent from
    ``script.prompt()`` and counts model calls with ``script.call_count``.
    """
    script = Script(*replies)
    return build_reflex_agent(script.model), script


def decided(
    proposal: ReflexProposal,
    *,
    prompt: str = "the prompt",
    outcome: ReflexOutcome | None = None,
    recent: dict[str, PersonChange] | None = None,
) -> Decided:
    """What the engine returns for *proposal*: its first outcome (nothing reversed) unless given."""
    return Decided(
        proposal=proposal,
        outcome=outcome or first_outcome(proposal, []),
        prompt=prompt,
        recent=recent or {},
    )
