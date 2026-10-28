from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from evals.harness.checks import reads_decision_log, run_check, step_kind_needed, watches_reflex
from evals.harness.checks.reflex import (
    PROPOSALS,
    ReflexDecisionParams,
    ReflexNotProposedParams,
    ReflexOutcomeParams,
)
from evals.harness.evidence import (
    ClockSet,
    Evidence,
    LoggedDecision,
    ReflexCall,
    ReflexEvent,
    StepKind,
)
from tests.evals.harness.factories import LAMP, LAMP_STATE, about, evidence, pushes


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
    decisions: tuple[LoggedDecision, ...] = (),
) -> Evidence:
    return evidence(
        step_started=list(starts),
        step_kinds=list(kinds),
        reflex=list(calls),
        clocks=list(clocks),
        state_pushes=pushes(list(kinds)),
        decisions=list(decisions),
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


def test_no_call_is_no_judgment_unless_the_golden_says_reflex_may_skip_it() -> None:
    """Every attended entity gets a call, so none means the event was lost, the consumer
    is down, or the attention set regressed: the harness cannot score that as Alfred's."""
    for params in ({"decision": "none"}, {"decision": ["act", "ask"]}):
        status, reason = decide(ev(), **params)
        assert status == "error"
        assert "System 1 was not called for step 0: no judgment to score" in reason
    status, reason = not_proposed(ev(), tool="home.light_turn_on")
    assert status == "error" and "no judgment to score" in reason
    # A deliberately unattended entity (sensor noise): not calling is the right answer.
    status, reason = decide(ev(), decision="none", uncalled_ok=True)
    assert status == "pass" and "not called" in reason
    assert decide(ev(), decision="act", uncalled_ok=True)[0] == "error"
    assert not_proposed(ev(), tool="home.light_turn_on", uncalled_ok=True)[0] == "pass"
    # A call about another change does not make the step's own.
    stray = rc("none", event=about("person.alex", "home", name="Alex"))
    status, reason = decide(ev(stray), decision="none")
    assert status == "error" and "unattributed: none (because) about Alex → home" in reason


def test_calls_outside_the_step_window_do_not_count() -> None:
    e = ev(
        rc("act", "home.light_turn_off", t=5.0),
        kinds=("ha_event", "ha_event"),
        starts=(0.0, 10.0),
    )
    assert decide(e, decision="none", uncalled_ok=True)[0] == "pass"  # the last ha_event step
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


def test_an_unreadable_change_line_is_not_called_no_state_change() -> None:
    unread = rc("act", "home.light_turn_on", t=3.0).model_copy(
        update={"event": None, "unread_change": "Alex left the house"}
    )
    status, reason = decide(ev(rc("none"), unread), decision="none")
    assert status == "pass"
    assert "about a change line the harness could not read: 'Alex left the house'" in reason
    assert "no state change" not in reason


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


def test_a_list_of_tools_takes_any_of_them() -> None:
    lights = ["home.light_turn_on", "home_light_turn_off"]
    dims = ev(rc("act", "home.light_turn_off", ("light.living_room_lamp", "living_room")))
    assert decide(dims, decision=["act", "ask"], tool=lights)[0] == "pass"
    pause = ev(rc("act", "home.media_player_media_pause", ("living_room",)))
    status, reason = decide(pause, decision=["act", "ask"], tool=lights)
    assert status == "fail"
    assert "wanted act/ask home.light_turn_on or home_light_turn_off" in reason
    with pytest.raises(ValidationError):
        ReflexDecisionParams.model_validate({"decision": "act", "tool": []})


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


def logged(
    outcome: str | None,
    decision: str = "act",
    tool: str | None = "home.light_turn_on",
    targets: tuple[str, ...] = (),
    t: float = 1.0,
    entity_id: str = LAMP,
    state: str = LAMP_STATE,
) -> LoggedDecision:
    """One decision log entry about the step's push (``factories.pushes``), unless said."""
    return LoggedDecision(
        t=t,
        entity_id=entity_id,
        state=state,
        decision=decision,
        outcome=outcome,
        tool=tool,
        targets=list(targets),
    )


def outcome(e: Evidence, **params: Any) -> tuple[str, str]:
    r = run_check("reflex_outcome", ReflexOutcomeParams.model_validate(params), e)
    return r.status, r.reason


LIVING = ("light.living_room_lamp", "living_room")
KITCHEN = ("light.kitchen_pendants", "kitchen")


def test_an_outcome_in_the_set_passes_and_one_outside_it_fails() -> None:
    reversed_ = ev(decisions=(logged("would_reverse", targets=LIVING),))
    status, reason = outcome(reversed_, outcome=["none", "would_reverse"])
    assert status == "pass" and "act home.light_turn_on on light.living_room_lamp" in reason
    assert "would_reverse" in reason
    stands = ev(decisions=(logged("shadow", targets=LIVING),))
    status, reason = outcome(stands, outcome=["none", "would_reverse"])
    assert status == "fail"
    assert "wanted outcome none/would_reverse" in reason and "act home.light_turn_on" in reason
    assert "shadow" in reason


def test_one_outcome_may_be_a_bare_string_and_the_set_may_not_be_empty() -> None:
    assert ReflexOutcomeParams.model_validate({"outcome": "held"}).outcome == ["held"]
    for bad in ({"outcome": []}, {"outcome": "reversed"}, {"outcome": "none", "step": 0}, {}):
        with pytest.raises(ValidationError):
            ReflexOutcomeParams.model_validate(bad)
    assert ReflexOutcomeParams.model_validate({"outcome": "none"}).target is None


def test_without_a_target_every_decision_about_the_change_is_judged() -> None:
    mixed = ev(
        decisions=(logged("none", "none", None), logged("shadow", targets=LIVING, t=2.0)),
    )
    assert outcome(mixed, outcome="none")[0] == "fail"
    assert outcome(mixed, outcome=["none", "shadow"])[0] == "pass"


def test_a_target_judges_only_the_decisions_that_propose_it() -> None:
    e = ev(
        decisions=(
            logged("would_reverse", targets=LIVING),
            logged("shadow", "ask", "home.light_turn_off", KITCHEN, t=2.0),
        )
    )
    # By entity: the lamp's proposal was a reversal, the pendants' stands.
    assert outcome(e, outcome="would_reverse", target="light.living_room_lamp")[0] == "pass"
    status, reason = outcome(e, outcome="would_reverse", target="light.kitchen_pendants")
    assert status == "fail" and "ask home.light_turn_off on light.kitchen_pendants" in reason
    # By room, the same way.
    assert outcome(e, outcome="would_reverse", target="living_room")[0] == "pass"
    assert outcome(e, outcome="shadow", target="kitchen")[0] == "pass"


def test_no_decision_proposing_the_target_is_a_pass() -> None:
    """A model that says nothing, or acts elsewhere, never reversed the person."""
    quiet = ev(decisions=(logged("none", "none", None),))
    status, reason = outcome(quiet, outcome=["none", "would_reverse"], target=LIVING[0])
    assert status == "pass" and "no decision proposed light.living_room_lamp" in reason
    assert "none" in reason  # what Reflex did decide is in the reason
    elsewhere = ev(decisions=(logged("shadow", targets=KITCHEN),))
    assert outcome(elsewhere, outcome="would_reverse", target=LIVING[0])[0] == "pass"


def test_no_decision_in_the_log_is_no_outcome_to_judge() -> None:
    """The change was lost, or its record was: the harness cannot score that as Alfred's."""
    status, reason = outcome(ev(), outcome="none")
    assert status == "error"
    assert "decision log holds no decision about step 0's change" in reason
    assert "System 1 was not called" in reason
    called = ev(rc("none"))
    status, reason = outcome(called, outcome="none")
    assert status == "error" and "System 1 was called" in reason
    # A target does not turn the absence into a vacuous pass.
    assert outcome(ev(), outcome="none", target=LIVING[0])[0] == "error"


def test_a_decision_about_another_change_or_before_the_step_is_not_the_steps() -> None:
    other_entity = logged("shadow", entity_id="light.kitchen_pendants")
    other_state = logged("shadow", state="on")
    early = logged("shadow", t=4.0)
    e = ev(
        decisions=(other_entity, other_state, early),
        kinds=("ha_event", "ha_event"),
        starts=(0.0, 5.0),
    )
    status, reason = outcome(e, outcome="shadow")
    assert status == "error" and "step 1" in reason
    # The same record is the earlier step's, which pushes the same change.
    assert outcome(e, outcome="shadow", at_step=0)[0] == "pass"


def test_a_decision_with_no_outcome_is_an_error_only_when_it_is_judged() -> None:
    old = ev(decisions=(logged(None, targets=LIVING),))
    status, reason = outcome(old, outcome="none")
    assert status == "error" and "no outcome" in reason
    # Not judged under a target it does not propose.
    assert outcome(old, outcome="none", target="kitchen")[0] == "pass"


def test_the_outcome_check_names_unattributed_calls_and_refuses_a_failed_upstream() -> None:
    stray = rc("act", "home.light_turn_on", ("living_room",), event=about("person.alex", "home"))
    status, reason = outcome(
        ev(stray, decisions=(logged("shadow", targets=LIVING),)), outcome="shadow"
    )
    assert status == "pass" and "unattributed: act home.light_turn_on on living_room" in reason
    failed = rc("invalid", problem="no reply (HTTP 502)", status=502)
    status, reason = outcome(
        ev(failed, decisions=(logged("invalid", "invalid", None),)), outcome="invalid"
    )
    assert status == "error" and "System 1's LLM upstream answered HTTP 502" in reason


def test_the_outcome_check_errors_without_an_ha_event_step() -> None:
    status, reason = outcome(ev(kinds=("user",)), outcome="none")
    assert status == "error" and "no ha_event step" in reason


def test_the_outcome_check_reads_the_decision_log_and_an_ha_event_step() -> None:
    params = ReflexOutcomeParams.model_validate({"outcome": "none"})
    assert step_kind_needed("reflex_outcome", params) == "ha_event"
    assert watches_reflex("reflex_outcome", params)
    assert reads_decision_log("reflex_outcome")
    assert not reads_decision_log("reflex_decision")
    assert not reads_decision_log("prompt_contains")
