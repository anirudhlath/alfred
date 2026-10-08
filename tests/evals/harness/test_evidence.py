from __future__ import annotations

from datetime import UTC, datetime
from typing import get_args

import pytest
from pydantic import ValidationError

from bus.schemas.events import ReflexDecision
from core.notifications.schema import Urgency as NotificationUrgency
from evals.harness.evidence import (
    STEP_KINDS,
    Advance,
    ClockSet,
    Decision,
    Evidence,
    NotificationRecord,
    ReflexCall,
    TriggerFire,
    TriggerRecord,
    Urgency,
)
from tests.evals.harness.factories import evidence


def three_steps() -> Evidence:
    return evidence(
        step_started=[0.0, 10.0, 20.0],
        step_kinds=["clock", "ha_event", "ha_event"],
        clocks=[ClockSet(step=0, hour=22, tz="Etc/GMT-7")],
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


def test_reflex_call_done_is_arrival_plus_latency() -> None:
    assert ReflexCall(t=2.0, latency_ms=500, decision="none").done == 2.5


def test_step_kinds_are_the_scenario_step_keys() -> None:
    assert STEP_KINDS == ("user", "ha_event", "wait", "clock", "advance_trigger", "dnd")


def test_new_evidence_round_trips_through_json() -> None:
    # tasks.py stores Evidence in Inspect's sample store as JSON and reads it back.
    created = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)
    ev = evidence(
        step_started=[0.0, 5.0],
        step_kinds=["user", "advance_trigger"],
        reflex=[ReflexCall(t=1.0, latency_ms=900, decision="act", tool="home.light_turn_off")],
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
                t=6.5, title="Trigger: Laundry", urgency="informational", source="trigger-engine"
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


def test_decision_matches_what_reflex_proposals_carry() -> None:
    assert get_args(Decision) == get_args(ReflexDecision)


def test_urgency_matches_the_notification_urgencies() -> None:
    assert set(get_args(Urgency)) == {u.value for u in NotificationUrgency}
