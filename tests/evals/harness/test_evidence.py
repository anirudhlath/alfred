from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from evals.harness.evidence import (
    STEP_KINDS,
    Advance,
    ClockSet,
    Evidence,
    NotificationRecord,
    ReflexCall,
    StatePush,
    TriggerFire,
    TriggerRecord,
)
from tests.evals.harness.factories import about, evidence, pushes


def three_steps() -> Evidence:
    return evidence(
        step_started=[0.0, 10.0, 20.0],
        step_kinds=["clock", "ha_event", "ha_event"],
        clocks=[ClockSet(step=0, hour=22, tz="Etc/GMT-7")],
        state_pushes=pushes(["clock", "ha_event", "ha_event"]),
    )


def test_step_window_runs_to_the_next_step_and_the_last_to_the_end() -> None:
    ev = three_steps()
    assert ev.step_window(0) == (0.0, 10.0)
    assert ev.step_window(1) == (10.0, 20.0)
    assert ev.step_window(-1) == (20.0, 100.0)  # ended_at is 100 in the factory


def test_step_index_counts_every_step_and_rejects_out_of_range() -> None:
    ev = three_steps()
    assert ev.step_index(-3) == 0
    with pytest.raises(IndexError):
        ev.step_index(3)


def test_last_step_and_clock_at() -> None:
    ev = three_steps()
    assert ev.last_step("ha_event") == 2
    assert ev.last_step("dnd") is None
    assert ev.clock_at(2) == ClockSet(step=0, hour=22, tz="Etc/GMT-7")
    assert evidence(step_started=[0.0], step_kinds=["ha_event"]).clock_at(0) is None


def test_step_at_takes_at_step_as_given_or_defaults_to_the_last_of_the_kind() -> None:
    ev = three_steps()  # clock, ha_event, ha_event
    assert ev.step_at("ha_event", None) == 2
    assert ev.step_at("ha_event", -2) == 1
    assert ev.step_at("ha_event", 0) == 0  # an explicit step is taken whatever its kind
    assert ev.step_at("dnd", None) is None
    with pytest.raises(IndexError):
        ev.step_at("ha_event", 3)


def test_reflex_during_keeps_the_calls_about_the_steps_own_change() -> None:
    def call(t: float, entity_id: str, state: str) -> ReflexCall:
        return ReflexCall(t=t, latency_ms=1, decision="none", event=about(entity_id, state))

    ours, theirs = call(10.5, "light.bedroom_lamp", "off"), call(11.0, "person.alex", "home")
    unnamed = ReflexCall(t=12.0, latency_ms=1, decision="none")  # a trigger's: no change
    ev = three_steps().model_copy(update={"reflex": [ours, theirs, unnamed]})
    assert ev.reflex_during(1) == [ours]
    assert ev.reflex_unattributed(1) == [theirs, unnamed]
    assert ev.state_push(1) == StatePush(step=1, entity_id="light.bedroom_lamp", state="off")
    assert ev.state_push(0) is None  # a clock step pushes nothing
    assert ev.reflex_during(0) == []


def test_step_start_and_reflex_during_read_one_step() -> None:
    calls = [
        ReflexCall(t=t, latency_ms=1, decision="none", event=about())
        for t in (9.9, 10.0, 19.9, 20.0)
    ]
    ev = three_steps().model_copy(update={"reflex": calls})  # steps at 0, 10 and 20
    assert ev.step_start(1) == 10.0 and ev.step_start(-1) == 20.0
    assert [c.t for c in ev.reflex_during(1)] == [10.0, 19.9]
    assert [c.t for c in ev.reflex_during(-1)] == [20.0]
    with pytest.raises(IndexError):
        ev.step_start(3)


def test_calls_after_step_names_a_step_outside_the_sample() -> None:
    with pytest.raises(IndexError, match="step 3 is outside the sample's 3 steps"):
        three_steps().calls_after_step(3)


def test_reflex_call_done_is_when_the_reply_came_back() -> None:
    queued = ReflexCall(t=2.0, latency_ms=500, answered_at=2.9, decision="none")
    assert queued.done == 2.9  # the proxy's queue and the body read count too
    # A record from before answered_at was kept: upstream's time is all it has.
    assert ReflexCall(t=2.0, latency_ms=500, decision="none").done == 2.5


def test_step_kinds_are_the_scenario_step_keys() -> None:
    assert STEP_KINDS == ("user", "ha_event", "wait", "clock", "advance_trigger", "dnd")


def test_new_evidence_round_trips_through_json() -> None:
    # tasks.py stores Evidence in Inspect's sample store as JSON and reads it back.
    created = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)
    ev = evidence(
        step_started=[0.0, 5.0],
        step_kinds=["ha_event", "advance_trigger"],
        reflex=[
            ReflexCall(
                t=1.0,
                latency_ms=900,
                answered_at=1.95,
                decision="act",
                tool="home.light_turn_off",
                event=about(),
            )
        ],
        state_pushes=pushes(["ha_event", "advance_trigger"]),
        triggers_created=[
            TriggerRecord(
                t=1.0,
                trigger_id="t1",
                trigger_type="time",
                name="Laundry",
                created_by="tool-call",
                conditions={"run_at": "2026-10-08T12:20:00-06:00"},
                created_at=created,
            )
        ],
        triggers_fired=[
            TriggerFire(
                t=6.0,
                trigger_id="t1",
                name="Laundry",
                trigger_type="time",
                urgency="informational",
                fired_by="engine",
            )
        ],
        notifications=[
            NotificationRecord(
                t=6.5,
                title="Trigger: Laundry",
                urgency="informational",
                source="trigger-engine",
                trigger="Laundry",
            )
        ],
        deferred=[NotificationRecord(t=None, title="x", urgency="important", source="librarian")],
        advances=[Advance(step=1, trigger_id="t1", name="Laundry", t=5.1)],
    )
    assert Evidence.model_validate(ev.model_dump(mode="json")) == ev


def test_clock_at_picks_the_last_clock_at_or_before_the_step() -> None:
    first, second = ClockSet(step=0, hour=22, tz="Etc/GMT-7"), ClockSet(step=2, hour=7, tz="UTC")
    ev = evidence(
        step_started=[0.0, 10.0, 20.0],
        step_kinds=["clock", "ha_event", "clock"],
        clocks=[first, second],
    )
    assert ev.clock_at(1) == first
    assert ev.clock_at(2) == second
    late = evidence(
        step_started=[0.0, 10.0],
        step_kinds=["ha_event", "clock"],
        clocks=[ClockSet(step=1, hour=22, tz="Etc/GMT-7")],
    )
    assert late.clock_at(0) is None


def test_step_index_rejects_below_the_first_step_and_an_empty_sample() -> None:
    with pytest.raises(IndexError):
        three_steps().step_index(-4)
    empty = Evidence(
        scenario_id="suite.case",
        variant=0,
        epoch=1,
        session_id="eval-test",
        started_at=0.0,
        ended_at=1.0,
    )
    with pytest.raises(IndexError):
        empty.step_index(0)


def test_step_kinds_must_match_the_steps_when_recorded() -> None:
    assert evidence().step_kinds == []  # step_started=[0.0] with no kinds is fine
    with pytest.raises(ValidationError, match="step_kinds"):
        evidence(step_started=[0.0, 10.0], step_kinds=["user"])


def test_a_naive_trigger_created_at_reads_as_utc() -> None:
    record = TriggerRecord(
        t=1.0,
        trigger_id="t1",
        trigger_type="time",
        name="Laundry",
        created_by="tool-call",
        created_at=datetime(2026, 10, 8, 18, 0),  # naive on purpose
    )
    assert record.created_at == datetime(2026, 10, 8, 18, 0, tzinfo=UTC)
    assert record.created_at.tzinfo is UTC
