from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from evals.harness.checks import CHECK_PARAMS, run_check
from evals.harness.evidence import HaState
from tests.evals.harness.factories import call, evidence, llm

if TYPE_CHECKING:
    from evals.harness.checks.result import CheckResult
    from evals.harness.evidence import Evidence


def check(name: str, params: dict[str, Any], ev: Evidence) -> CheckResult:
    return run_check(name, CHECK_PARAMS[name].model_validate(params), ev)


def test_ha_called_matches_domain_service_entities_and_approx_data() -> None:
    ev = evidence(
        ha_calls=[call("light", "turn_on", ["light.a", "light.b"], {"brightness_pct": 31.0})]
    )
    ok = check(
        "ha_called",
        {
            "domain": "light",
            "service": "turn_on",
            "entity_id": "light.a",
            "data": {"brightness_pct": {"approx": 30, "tol": 2}},
        },
        ev,
    )
    assert ok.status == "pass"
    far = check(
        "ha_called",
        {
            "domain": "light",
            "service": "turn_on",
            "data": {"brightness_pct": {"approx": 50, "tol": 2}},
        },
        ev,
    )
    assert far.status == "fail" and "brightness_pct" in far.reason


def test_ha_called_after_step_ignores_earlier_calls() -> None:
    ev = evidence(ha_calls=[call("light", "turn_on", ["light.a"], t=1.0)], step_started=[0.0, 5.0])
    res = check("ha_called", {"domain": "light", "service": "turn_on", "after_step": 1}, ev)
    assert res.status == "fail"


def test_ha_not_called_with_no_filters_means_no_calls_at_all() -> None:
    assert check("ha_not_called", {}, evidence()).status == "pass"
    ev = evidence(ha_calls=[call("switch", "turn_on", ["switch.x"])])
    res = check("ha_not_called", {}, ev)
    assert res.status == "fail" and "switch.turn_on" in res.reason


def test_ha_not_called_by_entity_only() -> None:
    ev = evidence(ha_calls=[call("light", "turn_off", ["light.a"])])
    assert check("ha_not_called", {"entity_id": "light.b"}, ev).status == "pass"
    assert check("ha_not_called", {"entity_id": "light.a"}, ev).status == "fail"


def test_ha_state_checks_state_and_attributes() -> None:
    ev = evidence(ha_states={"light.a": HaState(state="on", attributes={"brightness": 130})})
    assert (
        check(
            "ha_state",
            {
                "entity_id": "light.a",
                "state": "on",
                "attributes": {"brightness": {"approx": 128, "tol": 13}},
            },
            ev,
        ).status
        == "pass"
    )
    assert check("ha_state", {"entity_id": "light.a", "state": "off"}, ev).status == "fail"
    assert check("ha_state", {"entity_id": "light.zzz"}, ev).status == "fail"


def test_tool_names_compare_after_dot_normalisation() -> None:
    ev = evidence(llm_calls=[llm("system2", ("home_light_turn_on", {"brightness_pct": 30}))])
    assert check("tool_called", {"tool": "home.light_turn_on"}, ev).status == "pass"
    assert (
        check("tool_called", {"tool": "home.light_turn_on", "role": "system1"}, ev).status == "fail"
    )
    assert check("tool_not_called", {"tool": "home.call_service"}, ev).status == "pass"


def test_llm_tool_args_and_absent() -> None:
    ev = evidence(llm_calls=[llm("system2", ("triggers_create_trigger", {"run_in_seconds": 1200}))])
    assert (
        check(
            "llm_tool_args",
            {
                "tool": "triggers.create_trigger",
                "args": {"run_in_seconds": {"approx": 1200, "tol": 60}},
            },
            ev,
        ).status
        == "pass"
    )
    assert (
        check(
            "llm_tool_args_absent", {"tool": "triggers.create_trigger", "key": "run_at"}, ev
        ).status
        == "pass"
    )
    assert (
        check(
            "llm_tool_args_absent", {"tool": "triggers.create_trigger", "key": "run_in_seconds"}, ev
        ).status
        == "fail"
    )


def test_reply_contains_defaults_to_last_reply_and_is_case_insensitive() -> None:
    ev = evidence(replies=["Priya is visiting.", "The door is LOCKED, sir."])
    assert check("reply_contains", {"text": "locked"}, ev).status == "pass"
    assert check("reply_contains", {"text": "priya"}, ev).status == "fail"
    assert check("reply_contains", {"text": "priya", "step": "any"}, ev).status == "pass"
    assert check("reply_contains", {"any": ["nope", "door"]}, ev).status == "pass"


def test_reply_regex_word_boundary_keeps_unlocked_out() -> None:
    ev = evidence(replies=["The front door is unlocked, sir."])
    assert check("reply_contains", {"regex": r"\blocked\b"}, ev).status == "fail"
    assert check("reply_not_contains", {"text": "unlocked"}, ev).status == "fail"


def test_reply_check_without_reply_fails_with_reason() -> None:
    res = check("reply_contains", {"text": "x", "step": 3}, evidence(replies=["a"]))
    assert res.status == "fail" and "no reply" in res.reason


def test_latency_bound() -> None:
    ev = evidence(replies=["ok"], latencies=[4200.0])
    assert check("latency", {"metric": "reply_ms", "max": 5000}, ev).status == "pass"
    assert check("latency", {"metric": "reply_ms", "max": 4000}, ev).status == "fail"


def test_reply_params_need_exactly_one_needle() -> None:
    with pytest.raises(ValidationError):
        CHECK_PARAMS["reply_contains"].model_validate({"text": "a", "regex": "b"})


def test_judge_is_a_known_check_name() -> None:
    assert "judge" in CHECK_PARAMS


def test_reply_not_contains_fails_when_the_reply_is_missing() -> None:
    res = check("reply_not_contains", {"text": "x", "step": 3}, evidence(replies=["a"]))
    assert res.status == "fail" and "no reply" in res.reason
    res = check("reply_not_contains", {"text": "x", "step": "any"}, evidence())
    assert res.status == "fail" and "no reply" in res.reason
    res = check("reply_not_contains", {"text": "x"}, evidence())
    assert res.status == "fail" and "no reply" in res.reason


def test_reply_not_contains_passes_when_the_needle_is_absent() -> None:
    ev = evidence(replies=["The front door is locked, sir."])
    assert check("reply_not_contains", {"text": "unlocked"}, ev).status == "pass"
    assert check("reply_not_contains", {"any": ["open", "ajar"], "step": "any"}, ev).status == (
        "pass"
    )


@pytest.mark.parametrize(
    "params",
    [{"text": ""}, {"any": []}, {"any": ["door", ""]}, {"regex": ""}],
)
def test_reply_params_reject_empty_needles(params: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CHECK_PARAMS["reply_contains"].model_validate(params)


def test_reply_params_reject_a_bad_regex_at_validation() -> None:
    with pytest.raises(ValidationError, match="regex"):
        CHECK_PARAMS["reply_contains"].model_validate({"regex": "(unclosed"})


def test_reply_contains_any_step_failure_lists_every_reply_truncated() -> None:
    long = "x" * 300
    ev = evidence(replies=["Priya is visiting.", "The door is locked.", long])
    res = check("reply_contains", {"text": "garage", "step": "any"}, ev)
    assert res.status == "fail"
    assert "Priya is visiting." in res.reason and "The door is locked." in res.reason
    assert "x" * 120 in res.reason and "x" * 121 not in res.reason


def test_ha_state_compares_state_case_insensitively() -> None:
    ev = evidence(ha_states={"lock.front": HaState(state="locked")})
    assert check("ha_state", {"entity_id": "lock.front", "state": "LOCKED"}, ev).status == "pass"


def test_tool_not_called_fails_when_the_tool_was_called() -> None:
    ev = evidence(llm_calls=[llm("system2", ("home_call_service", {}))])
    res = check("tool_not_called", {"tool": "home.call_service"}, ev)
    assert res.status == "fail" and "home.call_service" in res.reason


def test_ha_called_after_step_keeps_calls_at_or_after_the_step_start() -> None:
    ev = evidence(ha_calls=[call("light", "turn_on", ["light.a"], t=5.0)], step_started=[0.0, 5.0])
    res = check("ha_called", {"domain": "light", "service": "turn_on", "after_step": 1}, ev)
    assert res.status == "pass"


def test_run_check_scores_a_raising_check_as_error() -> None:
    ev = evidence(ha_calls=[call("light", "turn_on", ["light.a"])], step_started=[0.0])
    res = check("ha_called", {"domain": "light", "service": "turn_on", "after_step": 5}, ev)
    assert res.status == "error" and "IndexError" in res.reason
