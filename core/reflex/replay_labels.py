"""The rules behind ``replay label`` and ``replay score``, and the counts they print.

``label_records`` marks each recorded decision ``ask`` or ``none`` by rules over the record
alone, so a candidate model can be scored against the same labels each time. ``score_labels``
decides what a candidate's proposals come to against those labels. Both are pure: the live
state, the settings and the owner's time zone are parameters, and nothing here reads Redis or
prints, so the command line (``core.reflex.replay``) does the reading and the printing.

``Scores`` and the text this module builds are counts only. A label line holds the owner's house
(its prompt, targets and changes), and so does a ``CandidateAsk``: each stays in the file the
command writes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal, get_args
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from bus.schemas.events import ReflexDecision, StateChangedEvent
from core.attribution.cause import Cause  # noqa: TC001  # pydantic field type
from core.reflex.asks import asked_key, asked_targets, in_quiet_hours
from core.reflex.decision_log import DecisionRecord  # noqa: TC001  # pydantic field type
from core.reflex.engine import decide_outcome
from core.reflex.reversal import PersonChange, notes_person_change, revised_change

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bus.schemas.events import ReflexProposal
    from core.reflex.outcome import Decided
    from core.reflex.prompt import LiveEntity
    from core.reflex.settings import ReflexSettings

# The proposals that put something to the owner, as the label sees them.
_ASKING: Final = ("act", "ask")
# Why a record is labelled ``none``: the first of these rules that failed, in this order.
Why = Literal["no_proposal", "unresolved", "reverses", "repeat", "quiet_hours"]
WHYS: Final[tuple[str, ...]] = get_args(Why)
# The two limits production's ask gate holds in code. The prompt carries neither the asks made
# lately nor quiet hours, so a model cannot be faulted for proposing what they hold back.
GATED: Final[tuple[str, ...]] = ("repeat", "quiet_hours")
# The lines a candidate's ask can be wrong on: every ``none`` but quiet hours', where the gate
# holds any ask. On a ``repeat`` line the gate holds only the repeated ask (same tool and
# targets, ``asked_key``), so an ask about anything else is wrong there.
_JUDGED: Final[tuple[str, ...]] = tuple(why for why in WHYS if why != "quiet_hours")
# What a candidate's answer can come to against its line (``Scores``'s counts).
Counted = Literal["wrong", "held", "missed", "other", "matched", "reversing", "invalid"]
# The answers that would really put something to the owner: the ones worth their judging.
_PUT_TO_OWNER: Final[tuple[Counted, ...]] = ("wrong", "other", "matched")


def share(part: int, whole: int, what: str = "", *, empty: str | None = "none answered") -> str:
    """``part of whole [what] (percent)``.

    With nothing to take a share of, *empty* instead, or just the counts when *empty* is None.
    """
    counts = f"{part} of {whole}{' ' + what if what else ''}"
    if whole:
        return f"{counts} ({100 * part / whole:.1f}%)"
    return counts if empty is None else empty


class Labelled(BaseModel):
    """One line of a label file: the label, why it is ``none``, and what it was judged against."""

    label: Literal["ask", "none"]
    why: Why | None  # the first rule that failed; None for an ask
    changes: dict[str, PersonChange]  # what people had just changed, by entity id
    record: DecisionRecord


def _note_change(changes: dict[str, PersonChange], record: DecisionRecord) -> None:
    """Take *record*'s event into *changes*, as the runner did before the engine read them.

    The runner notes a state change to a device Reflex always shows (``notes_person_change``)
    ahead of the decision, by the rule ``PersonChanges.note`` applies (``revised_change``). A
    trigger has no state change, and an event that no longer parses is skipped.
    """
    if record.origin != "state_change":
        return
    try:
        event = StateChangedEvent.model_validate(record.event)
    except ValueError:
        return
    if not notes_person_change(event.entity_id):
        return
    revised = revised_change(changes.get(event.entity_id), event, record.cause)
    if isinstance(revised, PersonChange):
        changes[event.entity_id] = revised
    elif revised == "clear":
        changes.pop(event.entity_id, None)


def _ask_key(decided: Decided, house: Mapping[str, LiveEntity]) -> str | None:
    """The key an ask about this decision's action would be remembered by, as the live ask's."""
    action = decided.proposal.action
    if action is None:
        return None
    return asked_key(action.tool_name, asked_targets(action, house))


def label_records(
    records: Sequence[DecisionRecord],
    house: Mapping[str, LiveEntity],
    settings: ReflexSettings,
    tz_name: str,
) -> list[Labelled]:
    """Label *records*, oldest first, ``ask`` or ``none``, with the first rule that failed.

    A record is an ``ask`` only when, in this order, its recorded proposal was an act or an
    ask (else ``no_proposal``), its target resolves in *house* (else ``unresolved``), it would
    reverse nothing a person just changed (else ``reverses``), no earlier ask about the same
    tool and targets was labelled within ``repeat_hours`` (else ``repeat``) and its time falls
    outside quiet hours in the zone *tz_name* (else ``quiet_hours``). The recorded outcome is
    never trusted: every record is decided again by today's rule (``decide_outcome``).

    What people had just changed is the record's own ``recent`` when it has an outcome (the two
    arrived together). A record without one gets it rebuilt from the log: every earlier
    record's event, and its own, which the runner noted before it decided, are taken in oldest
    first, and the changes within ``reverse_window_minutes`` of the record's time are kept.
    The log holds only the events that reached the model, so a rebuilt set can miss a change,
    and the first records of a read have no earlier ones to rebuild from.
    """
    zone = ZoneInfo(tz_name)
    window = timedelta(minutes=settings.reverse_window_minutes)
    repeat = timedelta(hours=settings.repeat_hours)
    seen: dict[str, PersonChange] = {}  # every change the log has shown so far
    asked_at: dict[str, datetime] = {}  # when each ask key was last labelled an ask
    labelled: list[Labelled] = []
    for record in records:
        _note_change(seen, record)
        changes = (
            record.recent
            if record.outcome is not None
            else {e: c for e, c in seen.items() if record.time - c.time <= window}
        )
        decided = decide_outcome(record.proposal, record.prompt, changes, house)
        key = _ask_key(decided, house)
        why: Why | None
        if record.proposal.decision not in _ASKING or key is None:
            why = "no_proposal"
        elif decided.outcome.kind == "invalid":
            why = "unresolved"
        elif decided.outcome.kind == "would_reverse":
            why = "reverses"
        elif key in asked_at and record.time - asked_at[key] < repeat:
            why = "repeat"
        elif in_quiet_hours(
            record.time.astimezone(zone).time(),
            settings.quiet_hours_start,
            settings.quiet_hours_end,
        ):
            why = "quiet_hours"
        else:
            why = None
            asked_at[key] = record.time
        labelled.append(
            Labelled(
                label="ask" if why is None else "none", why=why, changes=changes, record=record
            )
        )
    return labelled


def summarize_labels(labelled: Sequence[Labelled]) -> str:
    """The counts of a labelling: no entity, name, prompt or reason is printed."""
    asks = sum(1 for line in labelled if line.label == "ask")
    why: Counter[str | None] = Counter(line.why for line in labelled)
    noun = "record" if len(labelled) == 1 else "records"
    return (
        f"Labelled {len(labelled)} {noun}: {asks} ask, {len(labelled) - asks} none.\n"
        "None by reason: " + ", ".join(f"{name} {why[name]}" for name in WHYS)
    )


@dataclass(frozen=True)
class Scores:
    """How a candidate's proposals compare with the labels they were scored against."""

    scored: int  # the lines the candidate answered
    labelled_ask: int
    judged_none: int  # labelled none where an ask can be wrong (_JUDGED: all but quiet_hours)
    gated_none: int  # labelled none for a limit the gate holds in code (GATED)
    wrong: int  # an ask on a judged_none line, but the repeated ask on a repeat line
    held: int  # an ask the gate would hold (quiet_hours: any; repeat: the repeated one)
    missed: int  # no ask on an ask line
    other: int  # an ask on an ask line, about another tool or targets
    matched: int  # the labelled ask
    reversing: int  # would reverse a person: must be 0
    invalid: int  # no decision, or a target that names nothing

    def lines(self) -> list[str]:
        """The counts as text, one line each: nothing of the house."""
        judged = f"labelled none for {', '.join(_JUDGED)}"
        gated = f"labelled none for {', '.join(GATED)}"
        asks = "labelled ask"
        return [
            f"Wrong asks: {share(self.wrong, self.judged_none, judged, empty=None)}",
            f"Asks the gate would hold: {share(self.held, self.gated_none, gated, empty=None)}",
            f"Missed asks: {share(self.missed, self.labelled_ask, asks, empty=None)}",
            f"Other asks: {share(self.other, self.labelled_ask, asks, empty=None)}",
            f"Matched asks: {share(self.matched, self.labelled_ask, asks, empty=None)}",
            f"Would reverse a person: {self.reversing} (must be 0)",
            f"Invalid replies: {share(self.invalid, self.scored)}",
        ]


def _recorded_key(line: Labelled, house: Mapping[str, LiveEntity]) -> str | None:
    """The key the line's recorded proposal asks about, as ``label_records`` read it."""
    recorded = decide_outcome(line.record.proposal, line.record.prompt, line.changes, house)
    return _ask_key(recorded, house)


def _counted_as(
    line: Labelled, candidate: Decided, house: Mapping[str, LiveEntity]
) -> Counted | None:
    """What a candidate's decided answer comes to against *line*; None if it counts for nothing."""
    match candidate.outcome.kind:
        case "would_reverse":
            return "reversing"
        case "invalid":
            return "invalid"
        case "shadow" if line.label == "none":
            held = line.why == "quiet_hours" or (
                line.why == "repeat" and _ask_key(candidate, house) == _recorded_key(line, house)
            )
            return "held" if held else "wrong"
        case "shadow":
            same = _ask_key(candidate, house) == _recorded_key(line, house)
            return "matched" if same else "other"
        case _ if line.label == "ask":
            return "missed"
        case _:
            return None


def score_labels(
    answers: Sequence[tuple[Labelled, ReflexProposal]], house: Mapping[str, LiveEntity]
) -> Scores:
    """What each candidate proposal comes to against the label it answers.

    Each proposal is decided again with the changes its line was labelled against
    (``decide_outcome``). One that would reverse a person, or is invalid (no decision, or a
    target that names nothing), is counted as that and as nothing else. A candidate ask that
    stands (``shadow``) on a line labelled ``none`` is a wrong ask, unless the gate would hold
    it (``GATED``: the prompt shows the model neither the asks made lately nor quiet hours).
    The gate holds any ask in quiet hours, but a repeat only for the same tool and targets
    (``asked_key``): on a ``repeat`` line the ask about the recorded one's key is held, and an
    ask about anything else would really be put to the owner, so it is wrong. On a line labelled
    ``ask`` it is either the labelled one (same tool and targets: matched) or another (other).
    A candidate ``none`` against an ``ask`` label is missed.
    """
    labelled_ask = sum(1 for line, _ in answers if line.label == "ask")
    gated_none = sum(1 for line, _ in answers if line.label == "none" and line.why in GATED)
    # Every none line but quiet hours, a hand-edited one with no why too: each can hold a wrong ask.
    judged_none = sum(
        1 for line, _ in answers if line.label == "none" and line.why != "quiet_hours"
    )
    counts = Counter(
        _counted_as(line, decide_outcome(proposal, line.record.prompt, line.changes, house), house)
        for line, proposal in answers
    )
    return Scores(
        scored=len(answers),
        labelled_ask=labelled_ask,
        judged_none=judged_none,
        gated_none=gated_none,
        wrong=counts["wrong"],
        held=counts["held"],
        missed=counts["missed"],
        other=counts["other"],
        matched=counts["matched"],
        reversing=counts["reversing"],
        invalid=counts["invalid"],
    )


class CandidateAsk(BaseModel):
    """One ask a candidate stands by: a line of the file the owner judges asks in.

    The labels hold only the incumbent's asks, so a good ask it never made counts as wrong, and
    only the owner can tell the two apart. The same ``time``, ``tool`` and ``targets`` from
    another candidate is the same ask.
    """

    time: datetime
    origin: Literal["state_change", "trigger_fired"]
    event: dict[str, Any]  # what the record was decided about
    cause: Cause | None
    counted: Counted
    decision: ReflexDecision
    tool: str
    targets: list[str]
    reason: str | None


def candidate_asks(
    answers: Sequence[tuple[Labelled, ReflexProposal]], house: Mapping[str, LiveEntity]
) -> list[CandidateAsk]:
    """The answers ``score_labels`` counts as wrong, other or matched, in order, for judging."""
    asks: list[CandidateAsk] = []
    for line, proposal in answers:
        candidate = decide_outcome(proposal, line.record.prompt, line.changes, house)
        counted = _counted_as(line, candidate, house)
        action = candidate.proposal.action
        if counted in _PUT_TO_OWNER and action is not None:
            asks.append(
                CandidateAsk(
                    time=line.record.time,
                    origin=line.record.origin,
                    event=line.record.event,
                    cause=line.record.cause,
                    counted=counted,
                    decision=candidate.proposal.decision,
                    tool=action.tool_name,
                    targets=asked_targets(action, house),
                    reason=candidate.proposal.reason,
                )
            )
    return asks
