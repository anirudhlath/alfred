"""Diff two EvalRuns — verdict changes, latency deltas, added/removed scenarios."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from evals.models import EvalRun, Verdict


class VerdictChange(StrEnum):
    IMPROVED = "improved"
    REGRESSED = "regressed"
    UNCHANGED = "unchanged"


_VERDICT_RANK = {Verdict.FAIL: 0, Verdict.PARTIAL: 1, Verdict.PASS: 2}


class ScenarioComparison(BaseModel):
    """Comparison of a single scenario across two runs."""

    name: str
    old_verdict: Verdict
    new_verdict: Verdict
    change: VerdictChange
    old_latency_ms: float
    new_latency_ms: float
    latency_delta_ms: float


class RunComparison(BaseModel):
    """Full comparison of two EvalRuns."""

    old_run_id: str
    new_run_id: str
    comparisons: list[ScenarioComparison]
    added_scenarios: list[str]
    removed_scenarios: list[str]
    improved: int
    regressed: int
    unchanged: int


def compare_runs(old: EvalRun, new: EvalRun) -> RunComparison:
    """Compare two runs and produce a structured diff."""
    old_by_name = {r.scenario.name: r for r in old.results}
    new_by_name = {r.scenario.name: r for r in new.results}

    shared = set(old_by_name) & set(new_by_name)
    added = sorted(set(new_by_name) - set(old_by_name))
    removed = sorted(set(old_by_name) - set(new_by_name))

    comparisons: list[ScenarioComparison] = []
    improved = 0
    regressed = 0
    unchanged = 0

    for name in sorted(shared):
        old_r = old_by_name[name]
        new_r = new_by_name[name]

        old_rank = _VERDICT_RANK[old_r.verdict]
        new_rank = _VERDICT_RANK[new_r.verdict]

        if new_rank > old_rank:
            change = VerdictChange.IMPROVED
            improved += 1
        elif new_rank < old_rank:
            change = VerdictChange.REGRESSED
            regressed += 1
        else:
            change = VerdictChange.UNCHANGED
            unchanged += 1

        comparisons.append(
            ScenarioComparison(
                name=name,
                old_verdict=old_r.verdict,
                new_verdict=new_r.verdict,
                change=change,
                old_latency_ms=old_r.trace.latency_ms,
                new_latency_ms=new_r.trace.latency_ms,
                latency_delta_ms=new_r.trace.latency_ms - old_r.trace.latency_ms,
            )
        )

    return RunComparison(
        old_run_id=old.run_id,
        new_run_id=new.run_id,
        comparisons=comparisons,
        added_scenarios=added,
        removed_scenarios=removed,
        improved=improved,
        regressed=regressed,
        unchanged=unchanged,
    )
