"""Tests for evals.models."""

from __future__ import annotations

from datetime import UTC, datetime

from bus.schemas.events import StateChangedEvent
from evals.models import EvalRun, ExpectedAction, Scenario, ScenarioResult, Verdict
from shared.tracing import TraceRecord


def _make_event() -> StateChangedEvent:
    return StateChangedEvent(
        source="eval",
        domain="home",
        entity_id="media_player.tv",
        new_state="on",
    )


def test_scenario_with_expected_action() -> None:
    scenario = Scenario(
        name="test_scenario",
        event=_make_event(),
        expected=ExpectedAction(
            tool_name="lighting.dim_lights",
            parameters={"room": "living_room"},
        ),
    )
    assert scenario.expected is not None
    assert scenario.expected.tool_name == "lighting.dim_lights"
    assert scenario.tags == []


def test_scenario_no_action_expected() -> None:
    scenario = Scenario(
        name="negative_test",
        tags=["negative"],
        event=_make_event(),
        expected=None,
    )
    assert scenario.expected is None


def test_scenario_with_tags() -> None:
    scenario = Scenario(
        name="tagged",
        tags=["home", "lighting"],
        event=_make_event(),
        expected=None,
    )
    assert "home" in scenario.tags
    assert "lighting" in scenario.tags


def test_verdict_enum_values() -> None:
    assert Verdict.PASS == "pass"
    assert Verdict.PARTIAL == "partial"
    assert Verdict.FAIL == "fail"


def test_eval_run_summary() -> None:
    trace = TraceRecord(
        trace_id="t1",
        timestamp=datetime.now(UTC),
        model="test",
        event=_make_event(),
        preferences_text="",
        tools=[],
        prompt="",
        raw_response="{}",
        parsed_action=None,
        latency_ms=100.0,
        prompt_tokens=10,
        completion_tokens=5,
    )
    result = ScenarioResult(
        scenario=Scenario(name="s1", event=_make_event(), expected=None),
        verdict=Verdict.PASS,
        reason="correctly took no action",
        trace=trace,
    )
    run = EvalRun(
        run_id="run-001",
        timestamp=datetime.now(UTC),
        model="test",
        scenario_count=1,
        results=[result],
        summary={"pass": 1, "partial": 0, "fail": 0},
    )
    assert run.summary["pass"] == 1
