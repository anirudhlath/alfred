"""Scoring logic — compare TraceRecord output to expected scenario outcome."""

from __future__ import annotations

from typing import TYPE_CHECKING

from evals.models import ExpectedAction, Scenario, ScenarioResult, Verdict

if TYPE_CHECKING:
    from shared.tracing import TraceRecord


def _coerce_equal(actual: object, expected: object) -> bool:
    """Type-coerced comparison. Handles SLM returning '40' for 40."""
    if actual == expected:
        return True
    try:
        return str(actual) == str(expected)
    except (TypeError, ValueError):
        return False


def _check_parameters(
    actual_params: dict[str, object],
    expected_params: dict[str, object],
) -> tuple[bool, list[str]]:
    """Check expected params against actual. Returns (all_match, mismatch_details)."""
    mismatches: list[str] = []
    for key, expected_val in expected_params.items():
        if key not in actual_params:
            mismatches.append(f"missing {key}")
        elif not _coerce_equal(actual_params[key], expected_val):
            mismatches.append(f"{key}: expected {expected_val!r}, got {actual_params[key]!r}")
    return len(mismatches) == 0, mismatches


def score(trace: TraceRecord, scenario: Scenario) -> ScenarioResult:
    """Score a trace against a scenario's expected outcome."""
    expected: ExpectedAction | None = scenario.expected
    actual = trace.parsed_action

    if expected is None:
        if actual is None:
            verdict = Verdict.PASS
            reason = "correctly took no action"
        else:
            verdict = Verdict.FAIL
            reason = f"expected no action, got {actual.tool_name}"
    elif actual is None:
        verdict = Verdict.FAIL
        reason = f"expected {expected.tool_name}, got no action"
    elif actual.tool_name != expected.tool_name:
        verdict = Verdict.FAIL
        reason = f"expected {expected.tool_name}, got {actual.tool_name}"
    elif expected.target_service and actual.target_service != expected.target_service:
        verdict = Verdict.FAIL
        reason = f"expected service {expected.target_service}, got {actual.target_service}"
    elif expected.parameters:
        all_match, mismatches = _check_parameters(actual.parameters, expected.parameters)
        if all_match:
            verdict = Verdict.PASS
            reason = "exact match"
        else:
            verdict = Verdict.PARTIAL
            reason = f"correct tool, wrong params: {', '.join(mismatches)}"
    else:
        verdict = Verdict.PASS
        reason = "exact match"

    return ScenarioResult(
        scenario=scenario,
        verdict=verdict,
        reason=reason,
        trace=trace,
    )
