from __future__ import annotations

import pytest
from pydantic import ValidationError

from bus.schemas.events import ActionRequest
from evals.harness.checks import CHECK_PARAMS, run_check
from evals.harness.checks.home import HaCalledParams, HaNotCalledParams
from tests.evals.harness.factories import call, evidence


def pending_action(request_id: str = "a", **fields: object) -> ActionRequest:
    return ActionRequest.model_validate(
        {
            "request_id": request_id,
            "source": "conscious-engine",
            "tool_name": "home.lock_unlock",
            "target_service": "home-service",
            "parameters": {"target": "lock.front_door"},
            **fields,
        }
    )


@pytest.mark.parametrize("name", ["pending_action", "pending_action_absent"])
def test_missing_pending_snapshot_fails_even_an_absence_check(name: str) -> None:
    params = CHECK_PARAMS[name](at_step=0, tool="home.lock_unlock")
    result = run_check(name, params, evidence())
    assert result.status == "fail" and "snapshot" in result.reason


@pytest.mark.parametrize(
    ("actions", "positive", "absent"),
    [
        ([], "fail", "pass"),
        ([pending_action()], "pass", "fail"),
        ([pending_action("a"), pending_action("b")], "fail", "fail"),
    ],
)
def test_pending_checks_distinguish_empty_single_and_duplicate_matches(
    actions: list[ActionRequest], positive: str, absent: str
) -> None:
    from evals.harness.evidence import PendingSnapshot

    ev = evidence(pending=[PendingSnapshot(step=0, actions=actions)])
    for name, status in [("pending_action", positive), ("pending_action_absent", absent)]:
        p = CHECK_PARAMS[name](at_step=0, tool="home_lock_unlock")
        assert run_check(name, p, ev).status == status


def test_pending_checks_match_parameters_target_and_exact_count_per_snapshot() -> None:
    from evals.harness.evidence import PendingSnapshot

    ev = evidence(
        step_started=[0, 10],
        pending=[
            PendingSnapshot(step=0, actions=[pending_action(), pending_action("b")]),
            PendingSnapshot(step=1, actions=[]),
        ],
    )
    params = CHECK_PARAMS["pending_action"]
    good = {
        "at_step": 0,
        "tool": "home.lock_unlock",
        "count": 2,
        "parameters": {"target": {"regex": "LOCK\\.front_door"}},
        "target_service": "HOME-service",
    }
    assert run_check("pending_action", params(**good), ev).status == "pass"
    for wrong in [
        {"tool": "home.alarm_disarm"},
        {"target_service": "other"},
        {"parameters": {"target": "lock.other"}},
        {"count": 1},
        {"at_step": -1},
    ]:
        assert run_check("pending_action", params(**{**good, **wrong}), ev).status == "fail"
    absent = CHECK_PARAMS["pending_action_absent"](at_step=-1, tool="home.lock_unlock")
    assert run_check("pending_action_absent", absent, ev).status == "pass"


def test_pending_pattern_and_count_errors_are_rejected_at_load() -> None:
    params = CHECK_PARAMS["pending_action"]
    for bad in [{"count": -1}, {"parameters": {"target": {"regex": "["}}}]:
        with pytest.raises(ValidationError):
            params(at_step=0, tool="home.lock_unlock", **bad)


def test_ha_step_windows_prove_before_after_and_repeated_consent() -> None:
    ev = evidence(step_started=[0, 10, 20], ha_calls=[call("lock", "unlock", [], t=10)])
    for at in [0, -3, 2, -1]:
        p = HaNotCalledParams(domain="lock", at_step=at)
        assert run_check("ha_not_called", p, ev).status == "pass"
    for at in [1, -2]:
        p = HaCalledParams(domain="lock", service="unlock", at_step=at, count=1)
        assert run_check("ha_called", p, ev).status == "pass"
    ev.ha_calls.append(call("lock", "unlock", [], t=19.9))
    p = HaCalledParams(domain="lock", service="unlock", at_step=1, count=1)
    assert run_check("ha_called", p, ev).status == "fail"
    assert run_check("ha_called", p.model_copy(update={"count": 2}), ev).status == "pass"
    assert (
        run_check(
            "ha_called", HaCalledParams(domain="lock", service="unlock", after_step=1, count=2), ev
        ).status
        == "pass"
    )


@pytest.mark.parametrize("model", [HaCalledParams, HaNotCalledParams])
def test_conflicting_ha_step_selectors_are_rejected(model: type) -> None:
    with pytest.raises(ValidationError, match=r"at_step.*after_step"):
        model(domain="lock", service="unlock", at_step=0, after_step=0)
