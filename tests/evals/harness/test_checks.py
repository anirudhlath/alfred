from __future__ import annotations

from evals.harness.checks import CHECK_PARAMS, run_check
from evals.harness.evidence import HaState
from tests.evals.harness.factories import call, evidence, llm


def check(name: str, params: dict, ev):  # type: ignore[no-untyped-def]
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
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CHECK_PARAMS["reply_contains"].model_validate({"text": "a", "regex": "b"})


def test_judge_is_a_known_check_name() -> None:
    assert "judge" in CHECK_PARAMS
