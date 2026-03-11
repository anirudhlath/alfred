"""Tests for evals.scorer — the pass/partial/fail verdict engine."""

from __future__ import annotations

from datetime import UTC, datetime

from bus.schemas.events import ActionRequest, StateChangedEvent
from evals.models import ExpectedAction, Scenario, Verdict
from evals.scorer import score
from shared.tracing import TraceRecord


def _make_trace(
    parsed_action: ActionRequest | None = None,
    raw_response: str = "{}",
) -> TraceRecord:
    return TraceRecord(
        trace_id="t1",
        timestamp=datetime.now(UTC),
        model="test",
        event=StateChangedEvent(source="eval", domain="home", entity_id="light.lr", new_state="on"),
        preferences_text="",
        tools=[],
        prompt="",
        raw_response=raw_response,
        parsed_action=parsed_action,
        latency_ms=100.0,
        prompt_tokens=10,
        completion_tokens=5,
    )


def _make_action(
    tool_name: str = "lighting.dim_lights",
    target_service: str = "home-service",
    **params: object,
) -> ActionRequest:
    return ActionRequest(
        source="reflex-engine",
        target_service=target_service,
        tool_name=tool_name,
        parameters=dict(params),
    )


def _make_scenario(
    expected: ExpectedAction | None = None,
) -> Scenario:
    return Scenario(
        name="test",
        event=StateChangedEvent(source="eval", domain="home", entity_id="light.lr", new_state="on"),
        expected=expected,
    )


class TestNoActionExpected:
    def test_pass_when_no_action_returned(self) -> None:
        trace = _make_trace(parsed_action=None)
        result = score(trace, _make_scenario(expected=None))
        assert result.verdict == Verdict.PASS
        assert "no action" in result.reason

    def test_fail_when_action_returned(self) -> None:
        trace = _make_trace(parsed_action=_make_action())
        result = score(trace, _make_scenario(expected=None))
        assert result.verdict == Verdict.FAIL
        assert "lighting.dim_lights" in result.reason


class TestActionExpected:
    def test_fail_when_no_action_returned(self) -> None:
        expected = ExpectedAction(tool_name="lighting.dim_lights")
        trace = _make_trace(parsed_action=None)
        result = score(trace, _make_scenario(expected=expected))
        assert result.verdict == Verdict.FAIL
        assert "got no action" in result.reason

    def test_pass_exact_match(self) -> None:
        expected = ExpectedAction(
            tool_name="lighting.dim_lights",
            parameters={"room": "living_room"},
        )
        trace = _make_trace(
            parsed_action=_make_action(room="living_room", level=20),
        )
        result = score(trace, _make_scenario(expected=expected))
        assert result.verdict == Verdict.PASS

    def test_pass_no_params_specified(self) -> None:
        """When expected has no params, any params from SLM are accepted."""
        expected = ExpectedAction(tool_name="lighting.dim_lights")
        trace = _make_trace(parsed_action=_make_action(room="lr", level=20))
        result = score(trace, _make_scenario(expected=expected))
        assert result.verdict == Verdict.PASS

    def test_fail_wrong_tool(self) -> None:
        expected = ExpectedAction(tool_name="lighting.dim_lights")
        trace = _make_trace(parsed_action=_make_action(tool_name="media.play"))
        result = score(trace, _make_scenario(expected=expected))
        assert result.verdict == Verdict.FAIL
        assert "expected lighting.dim_lights" in result.reason

    def test_partial_wrong_params(self) -> None:
        expected = ExpectedAction(
            tool_name="lighting.dim_lights",
            parameters={"room": "living_room"},
        )
        trace = _make_trace(
            parsed_action=_make_action(room="bedroom"),
        )
        result = score(trace, _make_scenario(expected=expected))
        assert result.verdict == Verdict.PARTIAL
        assert "room" in result.reason

    def test_partial_missing_params(self) -> None:
        expected = ExpectedAction(
            tool_name="lighting.dim_lights",
            parameters={"room": "living_room", "level": 20},
        )
        trace = _make_trace(
            parsed_action=_make_action(room="living_room"),
        )
        result = score(trace, _make_scenario(expected=expected))
        assert result.verdict == Verdict.PARTIAL
        assert "level" in result.reason

    def test_type_coercion_string_to_int(self) -> None:
        """SLMs sometimes return '40' instead of 40."""
        expected = ExpectedAction(
            tool_name="lighting.dim_lights",
            parameters={"level": 40},
        )
        trace = _make_trace(
            parsed_action=_make_action(level="40"),
        )
        result = score(trace, _make_scenario(expected=expected))
        assert result.verdict == Verdict.PASS

    def test_target_service_match(self) -> None:
        expected = ExpectedAction(
            tool_name="lighting.dim_lights",
            target_service="home-service",
        )
        trace = _make_trace(
            parsed_action=_make_action(target_service="home-service"),
        )
        result = score(trace, _make_scenario(expected=expected))
        assert result.verdict == Verdict.PASS

    def test_target_service_mismatch(self) -> None:
        expected = ExpectedAction(
            tool_name="lighting.dim_lights",
            target_service="home-service",
        )
        trace = _make_trace(
            parsed_action=_make_action(target_service="wrong-service"),
        )
        result = score(trace, _make_scenario(expected=expected))
        assert result.verdict == Verdict.FAIL
