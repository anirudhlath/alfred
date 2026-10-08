"""Latency bounds: a reply, Reflex's decision on an event, and a due reminder's notification."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.harness.checks.result import CheckResult, failed, passed

if TYPE_CHECKING:
    from evals.harness.evidence import Evidence, StepKind

TRIGGER_SOURCE = "trigger-engine"  # the source of the notification a trigger's fire sends

# The kind of step each step-timed metric measures from: its at_step, by default the
# golden's last step of that kind.
STEP_KIND_FOR_METRIC: dict[str, StepKind] = {
    "reflex_ms": "ha_event",
    "reminder_fire_ms": "advance_trigger",
}


class LatencyParams(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    metric: Literal["reply_ms", "reflex_ms", "reminder_fire_ms"]
    max_ms: float = Field(gt=0, alias="max")
    # reply_ms: a reply index, one reply per user step (default -1, the last reply).
    step: int | None = None
    # reflex_ms, reminder_fire_ms: counts every step, like after_step. Default: the golden's
    # last ha_event (reflex_ms) or advance_trigger (reminder_fire_ms) step.
    at_step: int | None = None

    @model_validator(mode="after")
    def _index_fits_the_metric(self) -> Self:
        if self.metric == "reply_ms" and self.at_step is not None:
            raise ValueError("reply_ms takes step (a reply index), not at_step")
        if self.metric != "reply_ms" and self.step is not None:
            raise ValueError(f"{self.metric} takes at_step (it counts every step), not step")
        return self


Measured = tuple[float, str] | str  # (milliseconds, what was measured), or why nothing was


def _reply_ms(evidence: Evidence, step: int) -> Measured:
    try:
        reply = evidence.replies[step]
    except IndexError:
        return f"no reply at step {step}"
    return reply.latency_ms, "reply"


def _reflex_ms(evidence: Evidence, step: int) -> Measured:
    start, end = evidence.step_window(step)
    calls = [c for c in evidence.reflex if start <= c.t < end]
    if not calls:
        return f"System 1 was not called for step {step}"
    return (calls[0].done - start) * 1000, f"Reflex decided on step {step}"


def _reminder_fire_ms(evidence: Evidence, step: int) -> Measured:
    advance = next((a for a in evidence.advances if a.step == step), None)
    if advance is None:
        return f"no trigger was brought forward at step {step}"
    sent = [
        n.t
        for n in evidence.notifications
        if n.t is not None
        and n.t >= advance.t
        and n.source == TRIGGER_SOURCE
        and advance.name.lower() in n.title.lower()
    ]
    if not sent:
        return f"no notification for {advance.name!r} after it was due"
    return (sent[0] - advance.t) * 1000, f"{advance.name!r} notified"


def latency(evidence: Evidence, p: LatencyParams) -> CheckResult:
    if p.metric == "reply_ms":
        measured = _reply_ms(evidence, -1 if p.step is None else p.step)
    else:
        kind = STEP_KIND_FOR_METRIC[p.metric]
        step = evidence.step_at(kind, p.at_step)
        if step is None:  # a golden with this metric has such a step: the harness lost it
            return CheckResult(
                name="latency",
                status="error",
                reason=f"the sample has no {kind} step: the harness recorded none to time",
            )
        measure = _reflex_ms if p.metric == "reflex_ms" else _reminder_fire_ms
        measured = measure(evidence, step)
    if isinstance(measured, str):
        return failed("latency", measured)
    took, what = measured
    if took <= p.max_ms:
        return passed("latency", f"{what} in {took:.0f} ms (≤ {p.max_ms:.0f})")
    return failed("latency", f"{what} took {took:.0f} ms, bound {p.max_ms:.0f}")
