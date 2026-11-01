"""Checks over System 1's decisions. Reflex runs in shadow mode: it decides, nothing acts.

The calls a check reads are the System 1 calls about one step's state change that reached
the proxy during that step, an ``ha_event`` (``at_step``, which counts every step; default
the golden's last one). A call in the window about anything else is named in the reason
as unattributed, never judged.

``reflex_outcome`` reads Reflex's decision log instead: what the code made of the model's
decision (a proposal that would act on what a person just changed is ``would_reverse``).
``reflex_ask_parked`` reads the log and the sample's last pending snapshot: an ask the log says
was made is a pending action Reflex parked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, NamedTuple, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from core.reflex.answers import ASK_SOURCE
from evals.harness.checks.llm import same_tool
from evals.harness.checks.result import CheckResult, failed, passed
from evals.harness.evidence import Decision, Outcome, Reason

if TYPE_CHECKING:
    from evals.harness.evidence import Evidence, LoggedDecision, ReflexCall


def _as_list(value: Any) -> Any:
    return [value] if isinstance(value, str) else value


Decisions = Annotated[list[Decision], BeforeValidator(_as_list), Field(min_length=1)]
Tools = Annotated[list[str], BeforeValidator(_as_list), Field(min_length=1)]  # any of them
Outcomes = Annotated[list[Outcome], BeforeValidator(_as_list), Field(min_length=1)]
Reasons = Annotated[list[Reason], BeforeValidator(_as_list), Field(min_length=1)]

# The outcomes that can carry a reason: a held ask says why it was held, and a shadow one
# says which limit would have held it.
REASONED: frozenset[Outcome] = frozenset({"held", "shadow"})


PROPOSALS: tuple[Decision, ...] = ("act", "ask")  # the decisions that propose a tool call


def _proposals() -> list[Decision]:
    return list(PROPOSALS)


class _AtStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at_step: int | None = None
    # Reflex may rightly skip the event: only for an entity it does not attend to. Without
    # it, no call means the event was lost, so there is no judgment to score (error).
    uncalled_ok: bool = False


class ReflexDecisionParams(_AtStep):
    decision: Decisions
    tool: Tools | None = None
    target: str | None = None  # an entity id or an area id


class ReflexNotProposedParams(_AtStep):
    tool: str
    target: str | None = None
    decision: Decisions = Field(default_factory=_proposals)


class ReflexOutcomeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Outcomes
    # Why it was held. Checked only on a decision whose outcome carries a reason: a ``none``
    # has none to be wrong about, so it satisfies ``{outcome: [held, none], reason: dnd}``.
    reason: Reasons | None = None
    target: str | None = None  # an entity id or an area id
    at_step: int | None = None

    @model_validator(mode="after")
    def _a_reason_needs_an_outcome_that_carries_one(self) -> Self:
        if self.reason is not None and not REASONED.intersection(self.outcome):
            raise ValueError(
                f"reason applies to {' or '.join(sorted(REASONED))} outcomes only; "
                f"the outcome set is {'/'.join(self.outcome)}"
            )
        return self


class ReflexAskParkedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # The ha_event step whose change must have a decision in the log: every ask in the sample
    # is judged whichever step it was about.
    at_step: int | None = None


def _clock_problem(evidence: Evidence, step: int, calls: list[ReflexCall]) -> str | None:
    """Why these calls cannot be judged against the golden's clock, or None."""
    clock = evidence.clock_at(step)
    if clock is None:
        return None
    for c in calls:
        if c.local_hour != clock.hour:
            saw = "no clock line" if c.local_hour is None else f"{c.local_hour:02d}:xx"
            return (
                f"the clock step set {clock.hour:02d}:xx ({clock.tz}), but System 1's prompt "
                f"showed {saw}: the evidence is about the wrong time of day"
            )
    return None


def upstream_problem(calls: list[ReflexCall]) -> str | None:
    """Why these calls hold no judgment: System 1's LLM upstream failed. None if it did not."""
    failed = next((c for c in calls if c.upstream_failed), None)
    if failed is None:
        return None
    return f"System 1's LLM upstream answered HTTP {failed.status}: the LLM failed, not Reflex"


class _Window(NamedTuple):
    """The ``ha_event`` step a reflex check judges, the System 1 calls about its change, and
    the calls in its window about anything else."""

    step: int
    calls: list[ReflexCall]
    unattributed: list[ReflexCall]

    def note(self) -> str:
        return unattributed_note(self.unattributed)


def unattributed_note(calls: list[ReflexCall]) -> str:
    """``"; unattributed: …"`` for calls about another change, or ``""``."""
    if not calls:
        return ""
    return "; unattributed: " + "; ".join(describe_unattributed(c) for c in calls)


def _window(evidence: Evidence, name: str, at_step: int | None) -> _Window | CheckResult:
    """The step's System 1 calls, or the ``error`` that says why they cannot be judged."""
    step = evidence.step_at("ha_event", at_step)
    if step is None:
        return CheckResult(
            name=name,
            status="error",
            reason="the sample has no ha_event step: the harness recorded no event to judge",
        )
    calls = evidence.reflex_during(step)
    problem = upstream_problem(calls) or _clock_problem(evidence, step, calls)
    if problem is not None:
        return CheckResult(name=name, status="error", reason=problem)
    return _Window(step, calls, evidence.reflex_unattributed(step))


def uncalled(name: str, step: int, unattributed: list[ReflexCall]) -> CheckResult:
    """No call about the step's change: Reflex attends to the entity, so its event was lost
    (swallowed by a cooldown, a stopped consumer, a regressed attention set). No judgment
    to score, and nothing to time: the reflex checks and ``reflex_ms`` all say this."""
    return CheckResult(
        name=name,
        status="error",
        reason=(
            f"System 1 was not called for step {step}: no judgment to score"
            + unattributed_note(unattributed)
        ),
    )


def _tool_is(c: ReflexCall, tool: str) -> bool:
    return c.tool is not None and same_tool(c.tool, tool)


def _describe(c: ReflexCall) -> str:
    what = c.decision if c.tool is None else f"{c.decision} {c.tool}"
    named = [t for t in c.targets if "." in t] or c.targets
    if named:
        what += f" on {', '.join(named)}"
    why = c.problem or c.reason
    return f"{what} ({why})" if why else what


def describe_unattributed(c: ReflexCall) -> str:
    """A call about another change, and what that change was."""
    if c.event is None:
        if c.unread_change is not None:
            line = c.unread_change
            return f"{_describe(c)} about a change line the harness could not read: {line!r}"
        return f"{_describe(c)} about no state change"
    return f"{_describe(c)} about {c.event.name} → {c.event.state}"


def _fits(c: ReflexCall, p: ReflexDecisionParams) -> bool:
    if c.decision not in p.decision:
        return False
    if c.decision in PROPOSALS:
        if p.tool is not None and not any(_tool_is(c, tool) for tool in p.tool):
            return False
        if p.target is not None and p.target not in c.targets:
            return False
    return True


def _want(p: ReflexDecisionParams) -> str:
    want = "/".join(p.decision)
    if p.tool is not None:
        want += f" {' or '.join(p.tool)}"
    if p.target is not None:
        want += f" on {p.target}"
    return want


def reflex_decision(evidence: Evidence, p: ReflexDecisionParams) -> CheckResult:
    name = "reflex_decision"
    window = _window(evidence, name, p.at_step)
    if isinstance(window, CheckResult):
        return window
    step, calls, _ = window
    if not calls:
        if p.uncalled_ok and "none" in p.decision:
            return passed(
                name, f"System 1 was not called for step {step}: Reflex let it pass{window.note()}"
            )
        return uncalled(name, window.step, window.unattributed)
    seen = "; ".join(_describe(c) for c in calls)
    if all(_fits(c, p) for c in calls):
        return passed(name, seen + window.note())
    return failed(name, f"wanted {_want(p)}; System 1 decided {seen}{window.note()}")


def reflex_not_proposed(evidence: Evidence, p: ReflexNotProposedParams) -> CheckResult:
    name = "reflex_not_proposed"
    window = _window(evidence, name, p.at_step)
    if isinstance(window, CheckResult):
        return window
    if not window.calls and not p.uncalled_ok:
        return uncalled(name, window.step, window.unattributed)
    for c in window.calls:
        if (
            c.decision in p.decision
            and _tool_is(c, p.tool)
            and (p.target is None or p.target in c.targets)
        ):
            return failed(name, f"System 1 proposed {_describe(c)}{window.note()}")
    seen = "; ".join(_describe(c) for c in window.calls) or "it was not called"
    on = "" if p.target is None else f" on {p.target}"
    return passed(name, f"no {'/'.join(p.decision)} {p.tool}{on} (System 1: {seen}){window.note()}")


def _describe_logged(d: LoggedDecision) -> str:
    what = d.decision if d.tool is None else f"{d.decision} {d.tool}"
    named = [t for t in d.targets if "." in t] or d.targets
    if named:
        what += f" on {', '.join(named)}"
    why = "" if d.reason is None else f" ({d.reason})"
    return f"{what} → {d.outcome}{why}"


def _unlogged(name: str, window: _Window) -> CheckResult:
    """The ``error`` for a change the decision log holds nothing about."""
    called = "System 1 was called" if window.calls else "System 1 was not called"
    return CheckResult(
        name=name,
        status="error",
        reason=(
            f"Reflex's decision log holds no decision about step {window.step}'s change "
            f"({called}): no outcome to judge" + window.note()
        ),
    )


def _reason_fits(d: LoggedDecision, p: ReflexOutcomeParams) -> bool:
    """Whether *d* is within the reason the golden wants: a decision with no reason always is."""
    return p.reason is None or d.reason is None or d.reason in p.reason


def reflex_outcome(evidence: Evidence, p: ReflexOutcomeParams) -> CheckResult:
    """Every decision in Reflex's log about the step's change (and, with a ``target``, every
    one that proposes it) came to an outcome in the set, and, with a ``reason``, a decision
    that carries one (a held ask) carries a wanted one.

    A decision that proposes nothing there has nothing to reverse, so none proposing the
    target is a pass. No decision in the log at all is an ``error``: the change was lost, or
    its record was, and there is no outcome to score.
    """
    name = "reflex_outcome"
    window = _window(evidence, name, p.at_step)
    if isinstance(window, CheckResult):
        return window
    logged = evidence.decisions_about(window.step)
    if not logged:
        return _unlogged(name, window)
    seen = "; ".join(_describe_logged(d) for d in logged)
    judged = logged if p.target is None else [d for d in logged if p.target in d.targets]
    if not judged:
        return passed(name, f"no decision proposed {p.target} (Reflex: {seen}){window.note()}")
    if any(d.outcome is None for d in judged):
        return CheckResult(
            name=name,
            status="error",
            reason=(
                "a decision in Reflex's log has no outcome (written before outcomes were "
                f"kept): {seen}{window.note()}"
            ),
        )
    if all(d.outcome in p.outcome and _reason_fits(d, p) for d in judged):
        return passed(name, "; ".join(_describe_logged(d) for d in judged) + window.note())
    on = "" if p.target is None else f" on {p.target}"
    wanted = "/".join(p.outcome)
    if p.reason is not None:
        wanted += f" (reason {'/'.join(p.reason)})"
    return failed(
        name,
        f"wanted outcome {wanted}{on}; Reflex decided "
        + "; ".join(_describe_logged(d) for d in judged)
        + window.note(),
    )


def reflex_ask_parked(evidence: Evidence, p: ReflexAskParkedParams) -> CheckResult:
    """Every ask the decision log says Reflex made in the sample is a pending action from
    Reflex in the sample's last pending snapshot.

    All of the sample's asks are judged, not only those about the step's change: a golden can
    be held for an ask that went out earlier (``held`` for ``ask_open``), and that earlier ask
    must be parked too. The ask is parked just after the model's reply, which can be after the
    ``ha_event`` step's own snapshot: the last one is what a golden's closing ``wait`` step
    settles. A decision that came to anything but ``asked`` has nothing to park, so a sample
    nothing was asked in is a pass. The step's own change must still have a decision in the
    log, or the check is an ``error``, as for ``reflex_outcome``.
    """
    name = "reflex_ask_parked"
    window = _window(evidence, name, p.at_step)
    if isinstance(window, CheckResult):
        return window
    logged = evidence.decisions_about(window.step)
    if not logged:
        return _unlogged(name, window)
    asked = [d for d in evidence.decisions if d.outcome == "asked"]
    if not asked:
        seen = "; ".join(_describe_logged(d) for d in logged)
        return passed(
            name, f"nothing was asked, so nothing to park (Reflex: {seen}){window.note()}"
        )
    snapshot = evidence.pending_at_step(-1) if evidence.pending else None
    if snapshot is None:
        return CheckResult(
            name=name,
            status="error",
            reason="the sample has no pending snapshot: the harness recorded none to look in",
        )
    mine = [a for a in snapshot.actions if a.source == ASK_SOURCE]
    missing = [
        d for d in asked if d.tool is None or not any(same_tool(a.tool_name, d.tool) for a in mine)
    ]
    held = "; ".join(f"{a.source} {a.tool_name}" for a in snapshot.actions) or "none"
    if missing:
        wanted = ", ".join(d.tool or "?" for d in missing)
        return failed(
            name,
            f"Reflex asked about {wanted}, but there is no pending action from {ASK_SOURCE} "
            f"for it (pending after the last step: {held}){window.note()}",
        )
    return passed(
        name,
        f"asked {', '.join(d.tool or '?' for d in asked)}; parked by {ASK_SOURCE}{window.note()}",
    )
