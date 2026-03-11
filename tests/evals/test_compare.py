"""Tests for evals.compare — diff two EvalRuns."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bus.schemas.events import StateChangedEvent
from evals.compare import VerdictChange, compare_runs
from evals.models import EvalRun, Scenario, ScenarioResult, Verdict
from shared.tracing import TraceRecord


def _make_result(
    name: str,
    verdict: Verdict,
    latency_ms: float = 100.0,
) -> ScenarioResult:
    event = StateChangedEvent(source="eval", domain="home", entity_id="light.lr", new_state="on")
    trace = TraceRecord(
        trace_id="t1",
        timestamp=datetime.now(UTC),
        model="test",
        event=event,
        preferences_text="",
        tools=[],
        prompt="",
        raw_response="{}",
        parsed_action=None,
        latency_ms=latency_ms,
        prompt_tokens=10,
        completion_tokens=5,
    )
    return ScenarioResult(
        scenario=Scenario(name=name, event=event, expected=None),
        verdict=verdict,
        reason="test",
        trace=trace,
    )


def _make_run(
    run_id: str,
    results: list[ScenarioResult],
) -> EvalRun:
    summary: dict[str, int] = {v.value: 0 for v in Verdict}
    for r in results:
        summary[r.verdict.value] += 1
    return EvalRun(
        run_id=run_id,
        timestamp=datetime.now(UTC),
        model="test",
        scenario_count=len(results),
        results=results,
        summary=summary,
    )


def test_unchanged_verdicts() -> None:
    old = _make_run("old", [_make_result("s1", Verdict.PASS)])
    new = _make_run("new", [_make_result("s1", Verdict.PASS)])
    diff = compare_runs(old, new)
    assert len(diff.comparisons) == 1
    assert diff.comparisons[0].change == VerdictChange.UNCHANGED


def test_improved_verdict() -> None:
    old = _make_run("old", [_make_result("s1", Verdict.FAIL)])
    new = _make_run("new", [_make_result("s1", Verdict.PASS)])
    diff = compare_runs(old, new)
    assert diff.comparisons[0].change == VerdictChange.IMPROVED
    assert diff.improved == 1


def test_regressed_verdict() -> None:
    old = _make_run("old", [_make_result("s1", Verdict.PASS)])
    new = _make_run("new", [_make_result("s1", Verdict.FAIL)])
    diff = compare_runs(old, new)
    assert diff.comparisons[0].change == VerdictChange.REGRESSED
    assert diff.regressed == 1


def test_latency_delta() -> None:
    old = _make_run("old", [_make_result("s1", Verdict.PASS, latency_ms=400.0)])
    new = _make_run("new", [_make_result("s1", Verdict.PASS, latency_ms=350.0)])
    diff = compare_runs(old, new)
    assert diff.comparisons[0].latency_delta_ms == pytest.approx(-50.0)


def test_new_scenario_flagged() -> None:
    old = _make_run("old", [_make_result("s1", Verdict.PASS)])
    new = _make_run(
        "new",
        [
            _make_result("s1", Verdict.PASS),
            _make_result("s2", Verdict.FAIL),
        ],
    )
    diff = compare_runs(old, new)
    assert "s2" in diff.added_scenarios


def test_removed_scenario_flagged() -> None:
    old = _make_run(
        "old",
        [
            _make_result("s1", Verdict.PASS),
            _make_result("s2", Verdict.PASS),
        ],
    )
    new = _make_run("new", [_make_result("s1", Verdict.PASS)])
    diff = compare_runs(old, new)
    assert "s2" in diff.removed_scenarios
