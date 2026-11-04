"""The replay's labels: which recorded decisions were right to ask the owner about, and why not.

``label_records`` is pure: the live state, the settings and the owner's zone are parameters, so
these tests pass them directly. The command that reads them and writes the file is in
``test_replay.py``.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from bus.schemas.events import ActionRequest, ReflexDecision, ReflexOutcome, ReflexProposal
from core.reflex.replay_labels import (
    Labelled,
    label_records,
    score_labels,
    share,
    summarize_labels,
)
from core.reflex.settings import ReflexSettings
from tests.core.reflex.label_fakes import (
    ALFRED,
    BEDROOM,
    DAY,
    HOUSE,
    LAMP,
    MOTION,
    PENDANTS,
    PERSON,
    fired_at,
    minutes,
    person_change,
    record_at,
    standard_log,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from core.reflex.decision_log import DecisionRecord


def _verdicts(
    records: Sequence[DecisionRecord],
    settings: ReflexSettings | None = None,
    tz_name: str = "UTC",
) -> list[tuple[str, str | None]]:
    labelled = label_records(records, HOUSE, settings or ReflexSettings(), tz_name)
    return [(line.label, line.why) for line in labelled]


# --- the rules --------------------------------------------------------------------


def test_labels_cover_a_reversal_a_repeat_quiet_hours_and_asks() -> None:
    assert _verdicts(standard_log()) == [
        ("none", "no_proposal"),
        ("none", "reverses"),
        ("ask", None),
        ("ask", None),
        ("none", "repeat"),
        ("ask", None),
        ("none", "unresolved"),
        ("none", "no_proposal"),
        ("none", "quiet_hours"),
    ]


def test_a_label_carries_the_changes_it_was_judged_against() -> None:
    log = standard_log()

    labelled = label_records(log, HOUSE, ReflexSettings(), "UTC")

    assert set(labelled[1].changes) == {BEDROOM}  # rebuilt from the log's earlier record
    assert labelled[1].changes[BEDROOM].cause == "person"
    assert labelled[5].changes == {}  # that change is past the window by then
    assert labelled[1].record == log[1]


def test_a_person_change_within_the_window_is_reversed_and_past_it_is_not() -> None:
    def log(gap: int) -> list[DecisionRecord]:
        return [
            record_at(DAY, entity=BEDROOM, context=PERSON),
            record_at(DAY + minutes(gap), decision="act", target="Bedroom Lamp"),
        ]

    assert _verdicts(log(59))[1] == ("none", "reverses")
    assert _verdicts(log(61))[1] == ("ask", None)
    assert _verdicts(log(61), ReflexSettings(reverse_window_minutes=120))[1] == ("none", "reverses")


def test_the_event_a_record_decided_about_counts_as_a_change_it_must_not_reverse() -> None:
    # The runner notes a change before the engine reads what people just changed.
    record = record_at(
        DAY, entity=LAMP, new="on", context=PERSON, decision="act", target="Living Room Lamp"
    )

    assert _verdicts([record]) == [("none", "reverses")]


def test_a_change_by_alfred_replaces_a_person_s_as_it_does_in_the_record() -> None:
    records = [
        record_at(DAY, entity=LAMP, new="on", context=PERSON),
        record_at(DAY + minutes(5), entity=LAMP, old="on", new="off", context=ALFRED),
        record_at(DAY + minutes(10), decision="act", target="Living Room Lamp"),
    ]

    assert _verdicts(records)[2] == ("ask", None)


def test_a_change_to_something_reflex_does_not_note_is_not_folded() -> None:
    records = [
        record_at(DAY, entity=MOTION, context=PERSON),  # a sensor: never noted
        record_at(DAY + minutes(5), decision="act", target="Kitchen Pendants"),
    ]

    assert _verdicts(records)[1] == ("ask", None)


def test_a_record_with_an_outcome_is_judged_by_the_changes_it_recorded() -> None:
    shadow = ReflexOutcome(kind="shadow")
    records = [
        record_at(DAY, entity=BEDROOM, context=PERSON),  # the log shows a change to this lamp...
        record_at(
            DAY + minutes(10),
            decision="act",
            target="Bedroom Lamp",
            outcome=shadow,
            recent={},  # ...but this record saw none, and is judged by what it saw
        ),
        record_at(
            DAY + minutes(20),
            decision="act",
            target="Living Room Lamp",
            outcome=shadow,  # recorded as standing, though what it saw says otherwise
            recent={LAMP: person_change(DAY + minutes(15))},
        ),
    ]

    labelled = label_records(records, HOUSE, ReflexSettings(), "UTC")

    assert [(x.label, x.why) for x in labelled] == [
        ("none", "no_proposal"),
        ("ask", None),
        ("none", "reverses"),
    ]
    assert labelled[1].changes == {}
    assert set(labelled[2].changes) == {LAMP}


def test_the_recorded_outcome_is_never_trusted() -> None:
    record = record_at(
        DAY,
        decision="act",
        target="Kitchen Pendants",
        outcome=ReflexOutcome(kind="would_reverse", reversed=[PENDANTS]),
        recent={},
    )

    assert _verdicts([record]) == [("ask", None)]  # today's rule: nothing was changed


def test_a_fired_trigger_is_labelled_and_folds_nothing() -> None:
    records = [
        fired_at(DAY, decision="act", target="Kitchen Pendants"),
        fired_at(DAY + minutes(5), decision="ask", target="Kitchen Pendants"),
    ]

    labelled = label_records(records, HOUSE, ReflexSettings(), "UTC")

    assert [(x.label, x.why) for x in labelled] == [("ask", None), ("none", "repeat")]
    assert labelled[0].changes == {}


def test_a_repeat_is_the_same_tool_and_targets_within_the_repeat_hours() -> None:
    two = [
        record_at(DAY, decision="act", target="Kitchen Pendants"),
        record_at(DAY + minutes(10), decision="act", target="Kitchen Pendants"),
    ]
    by_area = [
        record_at(DAY, decision="act", target="Kitchen Pendants"),
        record_at(DAY + minutes(10), decision="act", target=PENDANTS),  # the same light by id
    ]

    assert _verdicts(two)[1] == ("none", "repeat")
    assert _verdicts(by_area)[1] == ("none", "repeat")  # compared on what it resolves to
    assert _verdicts(two, ReflexSettings(repeat_hours=0))[1] == ("ask", None)  # none asked for
    assert _verdicts(two, ReflexSettings(repeat_hours=0.1))[1] == ("ask", None)  # 6 minutes
    assert _verdicts(two, ReflexSettings(repeat_hours=0.2))[1] == ("none", "repeat")  # 12


def test_a_record_that_is_not_an_ask_does_not_start_a_repeat_window() -> None:
    records = [
        record_at(DAY, decision="act", target="Garage Light"),  # unresolved: labelled none
        record_at(DAY + minutes(5), decision="act", target="Kitchen Pendants"),
    ]

    assert _verdicts(records) == [("none", "unresolved"), ("ask", None)]


def test_quiet_hours_are_the_owners_local_time() -> None:
    record = record_at(
        datetime(2026, 10, 27, 13, 0, tzinfo=UTC), decision="act", target="Kitchen Pendants"
    )

    assert _verdicts([record], tz_name="UTC") == [("ask", None)]
    assert _verdicts([record], tz_name="America/Los_Angeles") == [("none", "quiet_hours")]  # 06:00


def test_the_why_is_the_first_rule_that_failed() -> None:
    late = DAY + minutes(570)  # 23:30, in quiet hours
    records = [
        record_at(late, entity=BEDROOM, context=PERSON),
        record_at(late + minutes(5), decision="act", target="Bedroom Lamp"),  # both rules fail
        record_at(late + minutes(6), decision="act", target="Garage Light"),
    ]

    assert _verdicts(records)[1:] == [("none", "reverses"), ("none", "unresolved")]


def test_an_act_with_no_action_names_nothing_to_ask_about() -> None:
    assert _verdicts([record_at(DAY, decision="act")]) == [("none", "no_proposal")]


# --- the edges of the windows -------------------------------------------------------------


def test_a_change_exactly_the_window_old_is_still_one_to_leave_alone() -> None:
    # `recent` keeps a change no older than the window, and so does the rebuilt set. A record's
    # event is stamped two seconds before the record, so the change is as old as the gap less 2s.
    def log(extra: timedelta) -> list[DecisionRecord]:
        return [
            record_at(DAY, entity=BEDROOM, context=PERSON),
            record_at(
                DAY + minutes(60) - timedelta(seconds=2) + extra,
                decision="act",
                target="Bedroom Lamp",
            ),
        ]

    assert _verdicts(log(timedelta(0)))[1] == ("none", "reverses")  # exactly 60 minutes old
    assert _verdicts(log(timedelta(seconds=1)))[1] == ("ask", None)  # a second past it


def test_two_asks_exactly_the_repeat_hours_apart_are_not_a_repeat() -> None:
    # The live repeat key expires at the repeat window, so an ask then is a new one.
    def log(gap: int) -> list[DecisionRecord]:
        return [
            record_at(DAY, decision="act", target="Kitchen Pendants"),
            record_at(DAY + minutes(gap), decision="act", target="Kitchen Pendants"),
        ]

    assert _verdicts(log(119))[1] == ("none", "repeat")
    assert _verdicts(log(120))[1] == ("ask", None)  # repeat_hours is 2.0


# --- the counts ---------------------------------------------------------------------------


def test_the_label_summary_says_record_for_one_and_records_for_the_rest() -> None:
    one = label_records(
        [record_at(DAY, decision="act", target="Kitchen Pendants")],
        HOUSE,
        ReflexSettings(),
        "UTC",
    )
    nine = label_records(standard_log(), HOUSE, ReflexSettings(), "UTC")

    assert summarize_labels(one).startswith("Labelled 1 record: 1 ask, 0 none.\n")
    assert summarize_labels(nine).startswith("Labelled 9 records: 3 ask, 6 none.\n")
    assert summarize_labels([]).startswith("Labelled 0 records: 0 ask, 0 none.\n")


def test_a_share_is_the_counts_and_a_percent() -> None:
    assert share(1, 4, "labelled ask") == "1 of 4 labelled ask (25.0%)"
    assert share(1, 4) == "1 of 4 (25.0%)"
    assert share(0, 0) == "none answered"  # nothing was answered: no share to take
    assert share(0, 0, "labelled ask", empty=None) == "0 of 0 labelled ask"  # just the counts


# --- the scores ---------------------------------------------------------------------------


def _answer(decision: ReflexDecision, target: str | None = None) -> ReflexProposal:
    """What a candidate model proposed: an act or an ask on the lamp tool, or no action."""
    action = (
        None
        if target is None
        else ActionRequest(
            source="reflex-engine",
            target_service="home-service",
            tool_name="home.light_turn_on",
            parameters={"target": target},
        )
    )
    return ReflexProposal(decision=decision, reason="dark", action=action)


def _line(label: str, why: str | None, **changes: object) -> Labelled:
    """A labelled record whose recorded proposal asks about the kitchen pendants, unless it
    proposed nothing (``no_proposal``)."""
    record = (
        record_at(DAY)
        if why == "no_proposal"
        else record_at(DAY, decision="act", target="Kitchen Pendants")
    )
    return Labelled.model_validate(
        {"label": label, "why": why, "changes": changes.get("changes", {}), "record": record}
    )


_REVERSES = {"changes": {LAMP: person_change(DAY - minutes(5))}}
_PENDANTS_ASK = _answer("act", "Kitchen Pendants")
_LAMP_ASK = _answer("act", "Living Room Lamp")

# One labelled line and one candidate answer for each way a candidate can come out, and the
# counts that answer alone comes to: every count not named is 0.
_BUCKETS: list[tuple[str, Labelled, ReflexProposal, dict[str, int]]] = [
    ("matched", _line("ask", None), _PENDANTS_ASK, {"labelled_ask": 1, "matched": 1}),
    (
        "matched, named by its id and asked rather than acted",
        _line("ask", None),
        _answer("ask", PENDANTS),
        {"labelled_ask": 1, "matched": 1},
    ),
    ("other", _line("ask", None), _LAMP_ASK, {"labelled_ask": 1, "other": 1}),
    ("missed", _line("ask", None), _answer("none"), {"labelled_ask": 1, "missed": 1}),
    ("wrong", _line("none", "no_proposal"), _LAMP_ASK, {"judged_none": 1, "wrong": 1}),
    ("none against none", _line("none", "no_proposal"), _answer("none"), {"judged_none": 1}),
    (
        "held in quiet hours, whatever it asks",
        _line("none", "quiet_hours"),
        _LAMP_ASK,
        {"gated_none": 1, "held": 1},
    ),
    (
        "held on a repeat of the same ask",
        _line("none", "repeat"),
        _PENDANTS_ASK,
        {"judged_none": 1, "gated_none": 1, "held": 1},
    ),
    (
        "held, the same light named by its id",
        _line("none", "repeat"),
        _answer("ask", PENDANTS),
        {"judged_none": 1, "gated_none": 1, "held": 1},
    ),
    (
        "wrong on a repeat line when it asks about another light",
        _line("none", "repeat"),
        _LAMP_ASK,
        {"judged_none": 1, "gated_none": 1, "wrong": 1},
    ),
    (
        "reversing",
        _line("none", "reverses", **_REVERSES),
        _LAMP_ASK,
        {"judged_none": 1, "reversing": 1},
    ),
    (
        "invalid: no decision",
        _line("ask", None),
        _answer("invalid"),
        {"labelled_ask": 1, "invalid": 1},
    ),
    (
        "invalid: a light there is not",
        _line("none", "no_proposal"),
        _answer("act", "Garage Light"),
        {"judged_none": 1, "invalid": 1},
    ),
]


@pytest.mark.parametrize(
    ("line", "answer", "counts"),
    [pytest.param(*case[1:], id=case[0]) for case in _BUCKETS],
)
def test_a_candidate_answer_lands_in_one_bucket(
    line: Labelled, answer: ReflexProposal, counts: dict[str, int]
) -> None:
    scores = score_labels([(line, answer)], HOUSE)

    assert {name: n for name, n in asdict(scores).items() if n} == {"scored": 1, **counts}


def test_the_buckets_add_up_and_read_as_lines() -> None:
    scores = score_labels([(line, answer) for _, line, answer, _ in _BUCKETS], HOUSE)

    assert asdict(scores) == {
        "scored": 13,
        "labelled_ask": 5,
        "judged_none": 7,
        "gated_none": 4,
        "wrong": 2,
        "held": 3,
        "missed": 1,
        "other": 1,
        "matched": 2,
        "reversing": 1,
        "invalid": 2,
    }
    assert scores.lines() == [
        "Wrong asks: 2 of 7 labelled none for no_proposal, unresolved, reverses, repeat (28.6%)",
        "Asks the gate would hold: 3 of 4 labelled none for repeat, quiet_hours (75.0%)",
        "Missed asks: 1 of 5 labelled ask (20.0%)",
        "Other asks: 1 of 5 labelled ask (20.0%)",
        "Matched asks: 2 of 5 labelled ask (40.0%)",
        "Would reverse a person: 1 (must be 0)",
        "Invalid replies: 2 of 13 (15.4%)",
    ]


def test_with_nothing_scored_the_lines_give_the_counts_and_no_percent() -> None:
    assert score_labels([], HOUSE).lines() == [
        "Wrong asks: 0 of 0 labelled none for no_proposal, unresolved, reverses, repeat",
        "Asks the gate would hold: 0 of 0 labelled none for repeat, quiet_hours",
        "Missed asks: 0 of 0 labelled ask",
        "Other asks: 0 of 0 labelled ask",
        "Matched asks: 0 of 0 labelled ask",
        "Would reverse a person: 0 (must be 0)",
        "Invalid replies: none answered",
    ]
