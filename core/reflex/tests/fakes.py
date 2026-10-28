"""Test helpers shared by the Reflex tests here and under ``tests/core/reflex``.

It lives in ``core`` because a module here that imported ``tests.*`` would bind the name
``tests`` to the root package before pytest collects ``sdk/tests``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.reflex.outcome import Decided, first_outcome

if TYPE_CHECKING:
    from bus.schemas.events import ReflexOutcome, ReflexProposal
    from core.reflex.reversal import PersonChange


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
