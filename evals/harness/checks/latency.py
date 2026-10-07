"""Latency bounds. Slice 1 measures reply_ms; reflex_ms and reminder_fire_ms come later."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from evals.harness.checks.result import CheckResult, failed, passed

if TYPE_CHECKING:
    from evals.harness.evidence import Evidence


class LatencyParams(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    metric: Literal["reply_ms"]
    max_ms: float = Field(gt=0, alias="max")
    step: int = -1


def latency(evidence: Evidence, p: LatencyParams) -> CheckResult:
    try:
        reply = evidence.replies[p.step]
    except IndexError:
        return failed("latency", f"no reply at step {p.step}")
    took = reply.latency_ms
    if took <= p.max_ms:
        return passed("latency", f"reply in {took:.0f} ms (≤ {p.max_ms:.0f})")
    return failed("latency", f"reply took {took:.0f} ms, bound {p.max_ms:.0f}")
