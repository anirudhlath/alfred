from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from evals.harness.checks import needs_reply, run_check, step_kind_needed, watches_reflex
from evals.harness.checks.latency import LatencyParams
from evals.harness.checks.llm import PromptParams
from evals.harness.checks.notifications import NotificationParams
from evals.harness.checks.triggers import (
    TriggerCreatedParams,
    TriggerFiredParams,
    TriggerTypeParams,
)
from evals.harness.evidence import (
    Advance,
    Evidence,
    NotificationRecord,
    ReflexCall,
    TriggerFire,
    TriggerRecord,
)
from tests.evals.harness.factories import evidence

CREATED = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)
CRON = r"0 7 \* \* (1-5|mon-fri)"


def rec(
    conditions: dict[str, Any],
    *,
    ttype: str = "time",
    name: str = "Laundry reminder",
    urgency: str = "informational",
    tid: str = "t1",
) -> TriggerRecord:
    return TriggerRecord(
        t=1.0,
        trigger_id=tid,
        trigger_type=ttype,
        name=name,
        created_by="tool-call",
        conditions=conditions,
        urgency=urgency,
        created_at=CREATED,
    )


def due_in(seconds: float) -> dict[str, Any]:
    """Conditions as normalize_conditions stores them: run_at in the user's zone."""
    run_at = (CREATED + timedelta(seconds=seconds)).astimezone(ZoneInfo("America/Denver"))
    return {"run_at": run_at.isoformat()}


def created(e: Evidence, **params: Any) -> tuple[str, str]:
    r = run_check("trigger_created", TriggerCreatedParams.model_validate(params), e)
    return r.status, r.reason


def test_trigger_created_measures_a_relative_delay_from_creation() -> None:
    e = evidence(triggers_created=[rec(due_in(1230))])
    assert created(e, type="time", run_in_seconds={"approx": 1200, "tol": 60})[0] == "pass"
    status, reason = created(e, run_in_seconds={"approx": 1200, "tol": 10})
    assert status == "fail" and "(in 1230s)" in reason


def test_at_local_reads_run_at_in_the_given_zone() -> None:
    new_york = evidence(triggers_created=[rec({"run_at": "2026-10-08T19:00:00-04:00"})])
    utc = evidence(triggers_created=[rec({"run_at": "2026-10-08T19:00:00+00:00"})])
    at = {"time": "19:00", "tz": "America/New_York"}
    assert created(new_york, at_local=at)[0] == "pass"
    assert created(utc, at_local=at)[0] == "fail"


def test_conditions_match_a_cron_pattern_and_sensor_fields() -> None:
    cron = evidence(triggers_created=[rec({"cron": "0 7 * * MON-FRI"})])
    assert created(cron, conditions={"cron": {"regex": CRON}})[0] == "pass"
    door = {"entity_id": "binary_sensor.front_door", "state_match": "on"}
    sensor = evidence(triggers_created=[rec(door, ttype="sensor")])
    assert created(sensor, type="sensor", conditions=door)[0] == "pass"
    assert created(sensor, conditions={"state_match": "open"})[0] == "fail"
    assert created(sensor, type="time")[0] == "fail"


def test_a_top_level_condition_named_regex_is_a_field_not_a_pattern() -> None:
    e = evidence(triggers_created=[rec({"regex": 5})])
    assert created(e, conditions={"regex": 5})[0] == "pass"


def test_name_and_urgency_narrow_the_match() -> None:
    e = evidence(triggers_created=[rec({}, name="Airport", urgency="urgent")])
    assert created(e, name="airport", urgency="urgent")[0] == "pass"
    assert created(e, urgency="informational")[0] == "fail"
    assert created(evidence(), type="time")[1].endswith("created: none")


@pytest.mark.parametrize(
    "params",
    [
        {"run_in_seconds": {"regex": "x"}},
        {"at_local": {"time": "7pm", "tz": "America/New_York"}},
        {"at_local": {"time": "19:00", "tz": "Mars/Base"}},
        {"conditions": {"cron": {"regex": "("}}},
        {"kind": "time"},
    ],
)
def test_trigger_created_params_reject_bad_input(params: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TriggerCreatedParams.model_validate(params)


def test_trigger_not_created() -> None:
    sensor = evidence(triggers_created=[rec({}, ttype="sensor")])
    check = "trigger_not_created"
    assert run_check(check, TriggerTypeParams(), evidence()).status == "pass"
    assert run_check(check, TriggerTypeParams(type="time"), sensor).status == "pass"
    assert run_check(check, TriggerTypeParams(type="sensor"), sensor).status == "fail"


def fire(t: float, tid: str = "t1", name: str = "Laundry reminder") -> TriggerFire:
    return TriggerFire(
        t=t,
        trigger_id=tid,
        name=name,
        trigger_type="time",
        urgency="informational",
        fired_by="engine",
    )


def test_trigger_fired_ignores_triggers_from_other_samples() -> None:
    p = TriggerFiredParams()
    stale = evidence(triggers_fired=[fire(5.0, tid="from-an-earlier-epoch")])
    result = run_check("trigger_fired", p, stale)
    assert result.status == "fail" and "created in this sample" in result.reason
    mine = evidence(triggers_created=[rec({})], triggers_fired=[fire(5.0)])
    assert run_check("trigger_fired", p, mine).status == "pass"


def test_trigger_fired_within_counts_from_after_step() -> None:
    def ev(t: float) -> Evidence:
        return evidence(
            step_started=[0.0, 10.0],
            step_kinds=["user", "advance_trigger"],
            triggers_created=[rec({})],
            triggers_fired=[fire(t)],
        )

    p = TriggerFiredParams(after_step=1, within_s=5)
    assert run_check("trigger_fired", p, ev(13.0)).status == "pass"
    late = run_check("trigger_fired", p, ev(16.0))
    assert late.status == "fail" and "6.0s after step 1" in late.reason
    early = run_check("trigger_fired", p, ev(9.0))  # fired before the step started
    assert early.status == "fail"
    with pytest.raises(ValidationError, match="after_step"):
        TriggerFiredParams(within_s=5)


def note(
    t: float | None, title: str = "Trigger: Laundry reminder", **kw: Any
) -> NotificationRecord:
    return NotificationRecord(
        t=t,
        title=title,
        urgency=kw.get("urgency", "informational"),
        source=kw.get("source", "trigger-engine"),
    )


def test_notification_sent_deferred_and_after_step() -> None:
    e = evidence(
        step_started=[0.0, 10.0],
        notifications=[note(4.0, urgency="urgent"), note(12.0, title="Trigger: Vet")],
        deferred=[note(None, title="Trigger: Plants")],
    )

    def check(**params: Any) -> str:
        return run_check("notification", NotificationParams.model_validate(params), e).status

    assert check(urgency="urgent") == "pass"
    assert check(urgency="urgent", after_step=1) == "fail"
    assert check(text="vet", after_step=1, source="trigger-engine") == "pass"
    assert check(text="plants") == "fail"  # held, not sent
    assert check(text="plants", deferred=True) == "pass"
    with pytest.raises(ValidationError, match="deferred"):
        NotificationParams(deferred=True, after_step=0)


def test_reflex_ms_runs_from_the_event_to_system1s_reply() -> None:
    def ev(*calls: ReflexCall) -> Evidence:
        return evidence(
            step_started=[0.0, 10.0], step_kinds=["ha_event", "ha_event"], reflex=list(calls)
        )

    call = ReflexCall(t=10.2, latency_ms=600, decision="none")
    fast = LatencyParams.model_validate({"metric": "reflex_ms", "max": 1000})
    slow = LatencyParams.model_validate({"metric": "reflex_ms", "max": 500})
    assert run_check("latency", fast, ev(call)).status == "pass"
    result = run_check("latency", slow, ev(call))
    assert result.status == "fail" and "800 ms" in result.reason
    missed = run_check("latency", fast, ev())
    assert missed.status == "fail" and "not called" in missed.reason


def test_reminder_fire_ms_runs_from_the_advance_to_its_notification() -> None:
    p = LatencyParams.model_validate({"metric": "reminder_fire_ms", "max": 5000})
    e = evidence(
        step_started=[0.0, 10.0],
        step_kinds=["user", "advance_trigger"],
        advances=[Advance(step=1, trigger_id="t1", name="Laundry reminder", t=10.0)],
        notifications=[note(9.0), note(12.5)],
    )
    result = run_check("latency", p, e)
    assert result.status == "pass" and "2500 ms" in result.reason
    no_advance = run_check("latency", p, e.model_copy(update={"advances": []}))
    assert no_advance.status == "fail" and "brought forward" in no_advance.reason
    unsent = run_check("latency", p, e.model_copy(update={"notifications": [note(9.0)]}))
    assert unsent.status == "fail" and "after it was due" in unsent.reason


@pytest.mark.parametrize(
    ("metric", "kind"), [("reflex_ms", "ha_event"), ("reminder_fire_ms", "advance_trigger")]
)
def test_a_step_metric_errors_when_the_sample_has_no_step_of_its_kind(
    metric: str, kind: str
) -> None:
    """A loaded golden has the step, so its absence means the harness lost it: E, not I."""
    p = LatencyParams.model_validate({"metric": metric, "max": 1000})
    e = evidence(step_started=[0.0, 10.0], step_kinds=["user", "wait"])
    result = run_check("latency", p, e)
    assert result.status == "error" and f"no {kind} step" in result.reason


def test_latency_params_tie_the_index_to_the_metric() -> None:
    LatencyParams.model_validate({"metric": "reply_ms", "max": 1, "step": 0})
    LatencyParams.model_validate({"metric": "reflex_ms", "max": 1, "at_step": 0})
    with pytest.raises(ValidationError, match="at_step"):
        LatencyParams.model_validate({"metric": "reflex_ms", "max": 1, "step": 0})
    with pytest.raises(ValidationError, match="reply index"):
        LatencyParams.model_validate({"metric": "reply_ms", "max": 1, "at_step": 0})


def test_which_checks_need_a_reply_a_step_kind_or_system1() -> None:
    reply = LatencyParams.model_validate({"metric": "reply_ms", "max": 1})
    reflex = LatencyParams.model_validate({"metric": "reflex_ms", "max": 1})
    fire_ms = LatencyParams.model_validate({"metric": "reminder_fire_ms", "max": 1})
    assert needs_reply("latency", reply) and not needs_reply("latency", reflex)
    assert step_kind_needed("latency", reflex) == "ha_event"
    assert step_kind_needed("latency", fire_ms) == "advance_trigger"
    assert step_kind_needed("latency", reply) is None
    prompt = PromptParams.model_validate({"role": "system1", "text": "notify."})
    assert watches_reflex("prompt_not_contains", prompt) and watches_reflex("latency", reflex)
    assert not watches_reflex("latency", fire_ms)
