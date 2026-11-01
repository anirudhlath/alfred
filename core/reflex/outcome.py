"""Every Reflex decision ends in one outcome.

The model answers with a ``ReflexProposal``; the code then reduces the answer to a
``ReflexOutcome`` and returns both as a ``Decided``. The engine decides, and the runner
records, counts and publishes what it decided (``core/reflex/runner.py``): shadow mode still
executes nothing.

``first_outcome`` is the first reduction: the model's own ``none`` and ``invalid``, a
proposal that would act on what a person just changed (``core/reflex/reversal.py``), and
every other proposal, which stands as ``shadow``. The engine makes a proposal whose target
names nothing Reflex can check ``invalid`` before it gets here. The runner then puts a
standing proposal to the owner or holds it (``ask_or_hold``), which makes it ``asked`` or
``held``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from bus.schemas.events import ReflexOutcome

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bus.schemas.events import OutcomeKind, ReflexProposal
    from core.reflex.prompt import LiveEntity
    from core.reflex.reversal import PersonChange

# The outcomes that are observed passively, not published as a proposal: nothing was proposed
# (``none``), or what was proposed must not reach anyone (``would_reverse``).
_UNPUBLISHED: Final[frozenset[OutcomeKind]] = frozenset({"none", "would_reverse"})


@dataclass(frozen=True)
class Decided:
    """One decision: what the model proposed, what it came to, and what it was decided from."""

    proposal: ReflexProposal
    outcome: ReflexOutcome
    prompt: str  # exactly what the model saw
    recent: dict[str, PersonChange]  # what people had just changed, by entity id
    # The live state the model saw, by entity id: what the runner resolves a target against when
    # it asks. Empty when the engine had none.
    snapshot: Mapping[str, LiveEntity] = field(default_factory=dict)


def first_outcome(proposal: ReflexProposal, reversed: Sequence[str]) -> ReflexOutcome:  # noqa: A002
    """The outcome of *proposal*, given the entities a person just changed that it acts on.

    ``none`` and ``invalid`` stay as they are. An ``act`` or ``ask`` that acts on any of them is
    ``would_reverse``, naming them; every other one is ``shadow``.
    """
    if proposal.decision == "none":
        return ReflexOutcome(kind="none")
    if proposal.decision == "invalid":
        return ReflexOutcome(kind="invalid")
    if reversed:
        return ReflexOutcome(kind="would_reverse", reversed=list(reversed))
    return ReflexOutcome(kind="shadow")


def publishes(outcome: ReflexOutcome) -> bool:
    """Whether *outcome* is published as a proposal observation.

    ``none`` and ``would_reverse`` are not: the first is observed passively, as a change Reflex
    saw and did nothing about, and the second becomes a ``none`` for everything downstream.
    """
    return outcome.kind not in _UNPUBLISHED
