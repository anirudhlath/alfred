from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from evals.harness.checks import run_check
from evals.harness.checks.reflex import (
    PROPOSALS,
    ReflexDecisionParams,
    ReflexNotProposedParams,
)
from evals.harness.evidence import ClockSet, Evidence, ReflexCall, ReflexEvent, StepKind
from tests.evals.harness.factories import about, evidence, pushes


def rc(
    decision: str,
    tool: str | None = None,
    targets: tuple[str, ...] = (),
    t: float = 1.0,
    hour: int | None = None,
    reason: str = "because",
    problem: str | None = None,
    event: ReflexEvent | None = None,
    status: int = 200,
) -> ReflexCall:
    """A System 1 call about the step's push (``factories.about``), unless *event* says."""
    return ReflexCall(
        t=t,
        latency_ms=800,
        status=status,
        decision=decision,
        tool=tool,
        targets=list(targets),
        local_hour=hour,
        reason=reason,
        problem=problem,
        event=event or about(),
    )


def ev(
    *calls: ReflexCall,
    kinds: tuple[StepKind, ...] = ("ha_event",),
    starts: tuple[float, ...] = (0.0,),
    clocks: tuple[ClockSet, ...] = (),
) -> Evidence:
    return evidence(
        step_started=list(starts),
        step_kinds=list(kinds),
        reflex=list(calls),
        clocks=list(clocks),
        state_pushes=pushes(list(kinds)),
    )


def decide(e: Evidence, **params: Any) -> tuple[str, str]:
    r = run_check("reflex_decision", ReflexDecisionParams.model_validate(params), e)
    return r.status, r.reason


def not_proposed(e: Evidence, **params: Any) -> tuple[str, str]:
    r = run_check("reflex_not_proposed", ReflexNotProposedParams.model_validate(params), e)
    return r.status, r.reason


def test_a_decision_in_the_set_passes() -> None:
    e = ev(rc("ask", "home.light_turn_off", ("light.living_room_lamp", "living_room")))
    assert decide(e, decision=["act", "ask"])[0] == "pass"
    assert decide(e, decision=["act", "ask"], tool="home_light_turn_off")[0] == "pass"
    assert decide(e, decision=["act", "ask"], target="living_room")[0] == "pass"


def test_the_wrong_tool_or_target_fails_and_says_what_system1_decided() -> None:
    e = ev(rc("act", "home.light_turn_on", ("light.bedroom_lamp", "bedroom")))
    status, reason = decide(e, decision=["act"], tool="home.light_turn_off")
    assert status == "fail" and "act home.light_turn_on" in reason
    assert decide(e, decision=["act"], target="living_room")[0] == "fail"


def test_none_passes_when_system1_was_not_called_and_says_so() -> None:
    status, reason = decide(ev(), decision="none")
    assert status == "pass" and "not called" in reason
    assert decide(ev(), decision=["act", "ask"])[0] == "fail"


def test_calls_outside_the_step_window_do_not_count() -> None:
    e = ev(
        rc("act", "home.light_turn_off", t=5.0),
        kinds=("ha_event", "ha_event"),
        starts=(0.0, 10.0),
    )
    assert decide(e, decision="none")[0] == "pass"  # default: the last ha_event step
    assert decide(e, decision="none", at_step=0)[0] == "fail"


def test_a_call_about_another_change_is_not_the_steps_and_is_named() -> None:
    """A restore's backlog, a replay or an earlier step's late call can land in the window:
    only a call about the step's own change is judged."""
    alex = about("person.alex", "home", name="Alex")
    e = ev(rc("act", "home.light_turn_on", ("living_room",), event=alex), rc("none", t=2.0))
    status, reason = decide(e, decision="none")
    assert status == "pass"
    assert "unattributed: act home.light_turn_on on living_room (because) about Alex → home" in (
        reason
    )
    status, reason = not_proposed(e, tool="home.light_turn_on")
    assert status == "pass" and "unattributed" in reason
    trigger = rc("act", "home.light_turn_on", t=3.0).model_copy(update={"event": None})
    status, reason = decide(ev(rc("none"), trigger), decision="none")
    assert status == "pass" and "about no state change" in reason
    # The same entity with another state is another step's change, too.
    other = rc("act", "home.light_turn_on", event=about(state="on"))
    assert decide(ev(rc("none"), other), decision="none")[0] == "pass"


def test_every_call_in_the_window_must_fit() -> None:
    e = ev(rc("none", t=1.0), rc("act", "home.light_turn_on", t=2.0))
    assert decide(e, decision="none")[0] == "fail"


def test_reflex_check_errors_when_the_clock_did_not_take() -> None:
    clock = ClockSet(step=0, hour=22, tz="Etc/GMT-7")
    kinds: tuple[StepKind, ...] = ("clock", "ha_event")
    took = ev(rc("none", t=11.0, hour=22), kinds=kinds, starts=(0.0, 10.0), clocks=(clock,))
    assert decide(took, decision="none")[0] == "pass"
    for hour in (16, None):
        wrong = ev(rc("none", t=11.0, hour=hour), kinds=kinds, starts=(0.0, 10.0), clocks=(clock,))
        status, reason = decide(wrong, decision="none")
        assert status == "error" and "22" in reason
        assert not_proposed(wrong, tool="home.light_turn_on")[0] == "error"


def test_not_proposed_fails_on_the_named_tool_and_target_only() -> None:
    e = ev(rc("act", "home.light_turn_on", ("light.living_room_ceiling", "living_room")))
    status, reason = not_proposed(e, tool="home.light_turn_on", target="light.living_room_ceiling")
    assert status == "fail" and "light_turn_on" in reason
    assert not_proposed(e, tool="home.light_turn_on", target="light.bedroom_lamp")[0] == "pass"
    assert not_proposed(e, tool="home.light_turn_off")[0] == "pass"
    asked = ev(rc("ask", "home.light_turn_on"))
    assert not_proposed(asked, tool="home.light_turn_on", decision="act")[0] == "pass"


def test_decision_params_take_one_string_and_reject_unknown_decisions() -> None:
    assert ReflexDecisionParams.model_validate({"decision": "none"}).decision == ["none"]
    with pytest.raises(ValidationError):
        ReflexDecisionParams.model_validate({"decision": "maybe"})
    with pytest.raises(ValidationError):
        ReflexDecisionParams.model_validate({"decision": []})
    with pytest.raises(ValidationError):
        ReflexDecisionParams.model_validate({"decision": "none", "step": 0})


@pytest.mark.parametrize("kinds", [("user",), ()], ids=["user step only", "no kinds recorded"])
def test_reflex_checks_error_when_the_sample_has_no_ha_event_step(
    kinds: tuple[StepKind, ...],
) -> None:
    e = ev(rc("act", "home.light_turn_on"), kinds=kinds)
    for status, reason in (decide(e, decision="none"), not_proposed(e, tool="home.light_turn_on")):
        assert status == "error" and "no ha_event step" in reason


def test_not_proposed_catches_an_ask_by_default() -> None:
    assert ReflexNotProposedParams.model_validate({"tool": "x"}).decision == list(PROPOSALS)
    asked = ev(rc("ask", "home.light_turn_on", ("light.bedroom_lamp", "bedroom")))
    status, reason = not_proposed(asked, tool="home.light_turn_on")
    assert status == "fail" and "ask home.light_turn_on on light.bedroom_lamp" in reason


def test_an_llm_upstream_failure_is_an_error_not_reflexs_invalid() -> None:
    """A 5xx is vLLM failing, never Reflex deciding: the step has no judgment to score."""
    failed = rc("invalid", problem="no reply (HTTP 502)", status=502)
    for status, reason in (
        decide(ev(failed), decision="none"),
        not_proposed(ev(rc("none"), failed), tool="home.light_turn_on"),
    ):
        assert status == "error"
        assert "System 1's LLM upstream answered HTTP 502: the LLM failed, not Reflex" in reason
    # A 4xx is Reflex's own request (say, a prompt too long): Alfred's invalid.
    refused = rc("invalid", problem="no reply (HTTP 400)", status=400)
    assert decide(ev(refused), decision="none")[0] == "fail"


def test_an_invalid_call_names_its_problem_in_the_failure() -> None:
    e = ev(rc("invalid", problem="tool home.vacuum_start is not registered"))
    status, reason = decide(e, decision="none")
    assert status == "fail"
    assert "invalid (tool home.vacuum_start is not registered)" in reason
    assert "because" not in reason  # the problem, not System 1's own reason
