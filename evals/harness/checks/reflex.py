"""Checks over System 1's decisions. Reflex runs in shadow mode: it decides, nothing acts.

The calls a check reads are the System 1 calls that reached the proxy during one step,
an ``ha_event`` (``at_step``, which counts every step; default the golden's last one).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from evals.harness.checks.llm import normalize_tool
from evals.harness.checks.result import CheckResult, failed, passed
from evals.harness.evidence import Decision

if TYPE_CHECKING:
    from evals.harness.evidence import Evidence, ReflexCall


def _as_list(value: Any) -> Any:
    return [value] if isinstance(value, str) else value


Decisions = Annotated[list[Decision], BeforeValidator(_as_list), Field(min_length=1)]


def _proposals() -> list[Decision]:
    """The decisions that propose a tool call."""
    return ["act", "ask"]


class _AtStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at_step: int | None = None


class ReflexDecisionParams(_AtStep):
    decision: Decisions
    tool: str | None = None
    target: str | None = None  # an entity id or an area id


class ReflexNotProposedParams(_AtStep):
    tool: str
    target: str | None = None
    decision: Decisions = Field(default_factory=_proposals)


def _step(evidence: Evidence, at_step: int | None) -> int | None:
    return evidence.last_step("ha_event") if at_step is None else evidence.step_index(at_step)


def _calls(evidence: Evidence, step: int) -> list[ReflexCall]:
    start, end = evidence.step_window(step)
    return [c for c in evidence.reflex if start <= c.t < end]


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


def _tool_is(c: ReflexCall, tool: str) -> bool:
    return c.tool is not None and normalize_tool(c.tool) == normalize_tool(tool)


def _describe(c: ReflexCall) -> str:
    what = c.decision if c.tool is None else f"{c.decision} {c.tool}"
    named = [t for t in c.targets if "." in t] or c.targets
    if named:
        what += f" on {', '.join(named)}"
    why = c.problem or c.reason
    return f"{what} ({why})" if why else what


def _fits(c: ReflexCall, p: ReflexDecisionParams) -> bool:
    if c.decision not in p.decision:
        return False
    if c.decision in ("act", "ask"):
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
    step = _step(evidence, p.at_step)
    if step is None:
        return failed(name, "the sample has no ha_event step")
    calls = _calls(evidence, step)
    if (problem := _clock_problem(evidence, step, calls)) is not None:
        return CheckResult(name=name, status="error", reason=problem)
    if not calls:
        if "none" in p.decision:
            return passed(name, f"System 1 was not called for step {step}: Reflex let it pass")
        return failed(name, f"System 1 was not called for step {step}; wanted {_want(p)}")
    seen = "; ".join(_describe(c) for c in calls)
    if all(_fits(c, p) for c in calls):
        return passed(name, seen)
    return failed(name, f"wanted {_want(p)}; System 1 decided {seen}")


def reflex_not_proposed(evidence: Evidence, p: ReflexNotProposedParams) -> CheckResult:
    name = "reflex_not_proposed"
    step = _step(evidence, p.at_step)
    if step is None:
        return failed(name, "the sample has no ha_event step")
    calls = _calls(evidence, step)
    if (problem := _clock_problem(evidence, step, calls)) is not None:
        return CheckResult(name=name, status="error", reason=problem)
    for c in calls:
        if (
            c.decision in p.decision
            and _tool_is(c, p.tool)
            and (p.target is None or p.target in c.targets)
        ):
            return failed(name, f"System 1 proposed {_describe(c)}")
    seen = "; ".join(_describe(c) for c in calls) or "it was not called"
    on = "" if p.target is None else f" on {p.target}"
    return passed(name, f"no {'/'.join(p.decision)} {p.tool}{on} (System 1: {seen})")
