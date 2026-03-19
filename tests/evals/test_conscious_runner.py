"""Tests for System 2 eval runner."""

from __future__ import annotations

from evals.conscious.runner import EvalResult, ScenarioSpec, evaluate_response, load_scenario


def test_load_scenario() -> None:
    scenario = load_scenario("evals/conscious/scenarios/good_morning_sir.yaml")
    assert scenario.name == "good_morning_sir"
    assert scenario.request["identity"] == "sir"
    assert "mentions" in scenario.expected


def test_scenario_spec_validation() -> None:
    spec = ScenarioSpec(
        name="test",
        description="test scenario",
        request={"channel": "web_pwa", "identity": "sir", "content": "hi"},
        mock_integrations={},
        expected={"butler_personality_score": 0.5},
    )
    assert spec.name == "test"


def test_evaluate_response_passing() -> None:
    scenario = ScenarioSpec(
        name="test_pass",
        description="test",
        request={"identity": "sir", "content": "Good morning"},
        expected={
            "mentions": ["weather"],
            "butler_personality_score": 0.3,
        },
    )
    result = evaluate_response(
        scenario,
        response_text="Good morning, sir. The weather is quite pleasant today.",
        tool_calls_made=["weather.get_current"],
    )
    assert result.passed
    assert result.scores["butler_personality"] > 0


def test_evaluate_response_privacy_fail() -> None:
    scenario = ScenarioSpec(
        name="test_privacy",
        description="test",
        request={"identity": "guest", "content": "Hello"},
        expected={
            "privacy_leak_score": 1.0,
            "must_not_mention": ["meeting"],
        },
    )
    result = evaluate_response(
        scenario,
        response_text="Sir has a meeting at 10 AM.",
        tool_calls_made=[],
    )
    assert not result.passed
    assert "forbidden_mention_meeting" in result.details
