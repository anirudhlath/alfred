"""Checks over System 1's decisions. Reflex runs in shadow mode: it decides, nothing acts.

The calls a check reads are the System 1 calls about one step's state change that reached
the proxy during that step, an ``ha_event`` (``at_step``, which counts every step; default
the golden's last one). A call in the window about anything else is named in the reason
as unattributed, never judged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, NamedTuple

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from evals.harness.checks.llm import normalize_tool
from evals.harness.checks.result import CheckResult, failed, passed
from evals.harness.evidence import Decision

if TYPE_CHECKING:
    from evals.harness.evidence import Evidence, ReflexCall


def _as_list(value: Any) -> Any:
    return [value] if isinstance(value, str) else value


Decisions = Annotated[list[Decision], BeforeValidator(_as_list), Field(min_length=1)]


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
    tool: str | None = None
    target: str | None = None  # an entity id or an area id


class ReflexNotProposedParams(_AtStep):
    tool: str
    target: str | None = None
    decision: Decisions = Field(default_factory=_proposals)


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
        """``"; unattributed: …"`` for the calls about anything else, or ``""``."""
        if not self.unattributed:
            return ""
        return "; unattributed: " + "; ".join(describe_unattributed(c) for c in self.unattributed)


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


def _uncalled(name: str, window: _Window) -> CheckResult:
    """No call about the step's change: Reflex attends to the entity, so its event was lost
    (swallowed by a cooldown, a stopped consumer, a regressed attention set)."""
    return CheckResult(
        name=name,
        status="error",
        reason=(
            f"System 1 was not called for step {window.step}: no judgment to score" + window.note()
        ),
    )


def _tool_is(c: ReflexCall, tool: str) -> bool:
    return c.tool is not None and normalize_tool(c.tool) == normalize_tool(tool)


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
        return f"{_describe(c)} about no state change"
    return f"{_describe(c)} about {c.event.name} → {c.event.state}"


def _fits(c: ReflexCall, p: ReflexDecisionParams) -> bool:
    if c.decision not in p.decision:
        return False
    if c.decision in PROPOSALS:
        if p.tool is not None and not _tool_is(c, p.tool):
            return False
        if p.target is not None and p.target not in c.targets:
            return False
    return True


def _want(p: ReflexDecisionParams) -> str:
    want = "/".join(p.decision)
    if p.tool is not None:
        want += f" {p.tool}"
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
        return _uncalled(name, window)
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
        return _uncalled(name, window)
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
