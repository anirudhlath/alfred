"""Shadow report — a week of Reflex proposals for the owner's verdicts (#285)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import fakeredis
from redis import exceptions as redis_exceptions

from bus.schemas.events import (
    ActionRequest,
    ChangeContext,
    ReflexObservation,
    ReflexOutcome,
    ReflexProposal,
    StateChangedEvent,
    TriggerFired,
)
from core.reflex import shadow_report
from core.reflex.answers import AnswerKind, OpenAsk, ReflexAnswer
from core.reflex.decision_log import DecisionRecord
from core.reflex.shadow_report import build_report
from shared.streams import (
    REFLEX_ANSWERS_STREAM,
    REFLEX_ASK_OPEN_KEY,
    REFLEX_DECISION_LOG_STREAM,
)

if TYPE_CHECKING:
    import pytest

NOW = datetime(2026, 10, 8, 3, 30, tzinfo=UTC)  # Wed 7 Oct, 22:30 in Chicago

ACT = ReflexProposal(
    decision="act",
    reason="Film at night",
    action=ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="home.light_turn_on",
        parameters={"target": "Living Room", "brightness_pct": 30},
    ),
)
ASK = ReflexProposal(decision="ask", reason="Dim for the film?")


def _entry(
    at: datetime, proposal: ReflexProposal | None, trigger: TriggerFired | None = None
) -> tuple[bytes, dict[bytes, bytes]]:
    if trigger is not None:
        obs = ReflexObservation(
            source="reflex-engine",
            origin="trigger_fired",
            trigger_event=trigger.model_dump(),
            proposal=proposal,
            timestamp=at,
        )
    else:
        event = StateChangedEvent(
            source="home-service",
            domain="home",
            entity_id="media_player.living_room_tv",
            old_state="paused",
            new_state="playing",
            attributes={"friendly_name": "Living Room TV"},
        )
        obs = ReflexObservation(
            source="reflex-engine",
            origin="state_change",
            trigger_event=event.model_dump(),
            proposal=proposal,
            timestamp=at,
        )
    return f"{int(at.timestamp() * 1000)}-0".encode(), {b"event": obs.model_dump_json().encode()}


def _decision(
    at: datetime, outcome: ReflexOutcome, *, target: str = "Kitchen"
) -> tuple[bytes, dict[bytes, bytes]]:
    """One decision-log entry: Reflex proposed turning *target*'s lights on."""
    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="media_player.living_room_tv",
        old_state="paused",
        new_state="playing",
    )
    record = DecisionRecord(
        time=at,
        origin="state_change",
        event=event.model_dump(mode="json"),
        cause=None,
        prompt="prompt",
        proposal=ReflexProposal(
            decision="act",
            action=ActionRequest(
                source="reflex-engine",
                target_service="home-service",
                tool_name="home.light_turn_on",
                parameters={"target": target},
            ),
        ),
        outcome=outcome,
    )
    return f"{int(at.timestamp() * 1000)}-0".encode(), {
        b"decision": record.model_dump_json().encode()
    }


def _redis(
    entries: list[tuple[bytes, dict[bytes, bytes]]],
    counts: dict[str, dict[bytes, bytes]] | None = None,
    decisions: list[tuple[bytes, dict[bytes, bytes]]] | None = None,
) -> AsyncMock:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)  # no ask is open
    redis.xrevrange = AsyncMock(return_value=list(reversed(entries)))  # newest first

    def xrange(stream: str, **_: object) -> list[tuple[bytes, dict[bytes, bytes]]]:
        # The decision log, oldest first; the answers stream holds none here.
        return [] if stream == REFLEX_ANSWERS_STREAM else decisions or []

    redis.xrange = AsyncMock(side_effect=xrange)
    redis.hgetall = AsyncMock(side_effect=lambda key: (counts or {}).get(key, {}))
    return redis


async def test_the_counts_table_covers_every_utc_day_in_the_window() -> None:
    redis = _redis([], {"alfred:reflex:decisions:2026-10-07": {b"act": b"1", b"none": b"40"}})

    report = await build_report(redis, days=2, now=NOW, tz_name="America/Chicago")

    assert "| 2026-10-06 | 0 | 0 | 0 | 0 |" in report
    assert "| 2026-10-07 | 1 | 0 | 40 | 0 |" in report
    assert "| 2026-10-08 | 0 | 0 | 0 | 0 |" in report


async def test_shadow_report_prints_outcomes() -> None:
    redis = _redis(
        [],
        {
            "alfred:reflex:decisions:2026-10-07": {
                b"act": b"5",
                b"none": b"40",
                b"outcome:none": b"40",
                b"outcome:shadow": b"3",
                b"outcome:would_reverse": b"2",
                b"outcome:held": b"1",
                b"held:tool_off": b"1",
            }
        },
    )

    report = await build_report(redis, days=2, now=NOW, tz_name="America/Chicago")

    assert "### Outcomes" in report
    assert "| Day (UTC) | none | invalid | would_reverse | shadow | asked | held |" in report
    assert "| 2026-10-07 | 40 | 0 | 2 | 3 | 0 | 1 |" in report
    # Between the decision counts and the proposals, so a reader meets them in that order.
    assert report.index("| 2026-10-07 | 5 |") < report.index("### Outcomes")
    assert report.index("### Outcomes") < report.index("### Proposals")


async def test_a_day_with_no_outcome_counts_shows_dashes_but_the_decisions_stay_zeros() -> None:
    """A day written before outcomes were counted has nothing to show: not a zero nobody counted."""
    redis = _redis(
        [],
        {
            "alfred:reflex:decisions:2026-10-06": {b"act": b"2", b"none": b"9"},
            "alfred:reflex:decisions:2026-10-07": {b"outcome:shadow": b"3"},
        },
    )

    report = await build_report(redis, days=2, now=NOW, tz_name="America/Chicago")

    assert "| 2026-10-06 | 2 | 0 | 9 | 0 |" in report  # the decisions table
    assert "| 2026-10-06 | — | — | — | — | — | — |" in report  # the outcomes table
    assert "| 2026-10-08 | — | — | — | — | — | — |" in report  # no hash at all
    assert "| 2026-10-07 | 0 | 0 | 0 | 3 | 0 | 0 |" in report  # counted: the missing kinds are 0


async def test_would_reverse_decisions_are_listed_newest_first_in_local_time() -> None:
    reversing = ReflexOutcome(kind="would_reverse", reversed=["light.kitchen_pendants"])
    redis = _redis(
        [],
        decisions=[
            _decision(datetime(2026, 10, 5, 3, 0, tzinfo=UTC), reversing),  # before the window
            _decision(datetime(2026, 10, 7, 3, 0, tzinfo=UTC), reversing),  # Tue 6 Oct 22:00 local
            _decision(datetime(2026, 10, 7, 4, 0, tzinfo=UTC), ReflexOutcome(kind="shadow")),
            _decision(
                datetime(2026, 10, 8, 2, 0, tzinfo=UTC),  # Wed 7 Oct 21:00 local
                ReflexOutcome(kind="would_reverse", reversed=["light.a", "light.b"]),
                target="Hall",
            ),
        ],
    )

    report = await build_report(redis, days=2, now=NOW, tz_name="America/Chicago")

    newest = "- Wed 07 Oct 21:00 · home.light_turn_on · target Hall · reversed light.a, light.b"
    older = (
        "- Tue 06 Oct 22:00 · home.light_turn_on · target Kitchen · reversed light.kitchen_pendants"
    )
    assert report.index(newest) < report.index(older)
    assert report.count("target Kitchen") == 1  # the one before the window and the shadow: absent
    # After the Outcomes table and before the Proposals, so a reader meets them in that order.
    assert report.index("### Outcomes") < report.index("### Would have reversed")
    assert report.index(older) < report.index("### Proposals")


async def test_the_would_reverse_list_is_capped_and_counts_the_rest() -> None:
    start = datetime(2026, 10, 7, 0, 0, tzinfo=UTC)
    redis = _redis(
        [],
        decisions=[
            _decision(
                start + timedelta(minutes=n),
                ReflexOutcome(kind="would_reverse", reversed=[f"light.l{n}"]),
            )
            for n in range(23)
        ],
    )

    report = await build_report(redis, days=2, now=NOW, tz_name="UTC")

    listed = [line for line in report.splitlines() if "· target Kitchen ·" in line]
    assert len(listed) == shadow_report.MAX_REVERSED == 20
    assert "reversed light.l22" in listed[0]  # the newest
    assert "reversed light.l3" in listed[-1]  # the twentieth newest
    assert "light.l2\n" not in report
    assert "(3 more)" in report


async def test_no_would_reverse_decisions_says_none() -> None:
    redis = _redis(
        [],
        decisions=[
            _decision(datetime(2026, 10, 7, 3, 0, tzinfo=UTC), ReflexOutcome(kind="shadow"))
        ],
    )

    report = await build_report(redis, days=2, now=NOW, tz_name="UTC")

    assert "### Would have reversed\n\nNone.\n" in report


async def test_an_unreadable_decision_log_costs_only_its_section() -> None:
    redis = _redis([_entry(datetime(2026, 10, 7, 3, 0, tzinfo=UTC), ACT)])
    redis.xrange = AsyncMock(side_effect=redis_exceptions.ConnectionError("reset"))

    report = await build_report(redis, days=2, now=NOW, tz_name="UTC")

    assert "### Would have reversed\n\nThe decision log could not be read" in report
    assert "### Proposals (1)" in report
    assert "· **act** ·" in report


async def test_the_decision_log_is_read_over_the_reports_window() -> None:
    redis = _redis([])

    await build_report(redis, days=2, now=NOW, tz_name="UTC")

    start_ms = int(datetime(2026, 10, 6, 3, 30, tzinfo=UTC).timestamp() * 1000)
    [log_read] = [
        call for call in redis.xrange.await_args_list if call.args[0] == REFLEX_DECISION_LOG_STREAM
    ]
    assert log_read.kwargs["min"] == str(start_ms)


async def test_proposals_are_listed_oldest_first_in_local_time() -> None:
    redis = _redis(
        [
            _entry(datetime(2026, 10, 7, 3, 0, tzinfo=UTC), ACT),  # Tue 6 Oct 22:00 local
            _entry(datetime(2026, 10, 7, 4, 0, tzinfo=UTC), None),  # passive: left out
            _entry(datetime(2026, 10, 7, 5, 0, tzinfo=UTC), ReflexProposal(decision="none")),
            _entry(datetime(2026, 10, 8, 2, 0, tzinfo=UTC), ASK),  # Wed 7 Oct 21:00 local
        ]
    )

    report = await build_report(redis, days=2, now=NOW, tz_name="America/Chicago")

    act = (
        '- Tue 06 Oct 22:00 · **act** · Living Room TV: paused → playing — "Film at night"'
        " — home.light_turn_on(target=Living Room, brightness_pct=30)"
    )
    ask = '- Wed 07 Oct 21:00 · **ask** · Living Room TV: paused → playing — "Dim for the film?"'
    assert "### Proposals (2)" in report
    assert act in report
    assert ask in report
    assert report.index(act) < report.index(ask)


async def test_an_invalid_proposal_shows_its_problem_and_raw_text_on_one_line() -> None:
    invalid = ReflexProposal(decision="invalid", raw="```json\n{}\n```", problem="not JSON")
    redis = _redis([_entry(datetime(2026, 10, 7, 3, 0, tzinfo=UTC), invalid)])

    report = await build_report(redis, days=2, now=NOW, tz_name="America/Chicago")

    line = (
        "· **invalid** · Living Room TV: paused → playing"
        " — problem: not JSON · raw: `'''json {} '''`"
    )
    assert line in report


async def test_a_trigger_proposal_names_the_trigger() -> None:
    trigger = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")
    redis = _redis([_entry(datetime(2026, 10, 7, 3, 0, tzinfo=UTC), ASK, trigger)])

    report = await build_report(redis, days=2, now=NOW, tz_name="America/Chicago")

    assert "· **ask** · trigger bedtime —" in report


async def test_the_scan_starts_at_the_window() -> None:
    redis = _redis([])

    await build_report(redis, days=2, now=NOW, tz_name="UTC")

    start_ms = int(datetime(2026, 10, 6, 3, 30, tzinfo=UTC).timestamp() * 1000)
    assert redis.xrevrange.await_args.kwargs["min"] == f"{start_ms}-0"


async def test_a_full_scan_says_it_was_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shadow_report, "MAX_SCAN", 1)
    redis = _redis([_entry(datetime(2026, 10, 7, 3, 0, tzinfo=UTC), ASK)])

    report = await build_report(redis, days=2, now=NOW, tz_name="UTC")

    assert "Scanned the newest 1 observations only" in report


async def test_no_proposals_says_so_and_bad_entries_are_skipped() -> None:
    redis = _redis([(b"1-0", {b"event": b"garbage"}), (b"2-0", {b"other": b"x"})])

    report = await build_report(redis, days=1, now=NOW, tz_name="UTC")

    assert "### Proposals (0)" in report
    assert "None in this window." in report


async def test_a_proposal_shows_the_change_as_the_prompt_rendered_it() -> None:
    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="media_player.living_room_tv",
        old_state="paused",
        new_state="playing",
        attributes={
            "friendly_name": "Living Room TV",
            "media_title": "A Film",
            "app_name": "Streamer",
        },
    )
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event=event.model_dump(),
        proposal=ASK,
        timestamp=datetime(2026, 10, 7, 3, 0, tzinfo=UTC),
    )
    redis = _redis([(b"1-0", {b"event": obs.model_dump_json().encode()})])

    report = await build_report(redis, days=2, now=NOW, tz_name="UTC")

    assert '· **ask** · Living Room TV: paused → playing · "A Film" · Streamer —' in report


async def test_report_shows_the_cause() -> None:
    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="media_player.living_room_tv",
        old_state="paused",
        new_state="playing",
        attributes={"friendly_name": "Living Room TV"},
        context=ChangeContext(parent_id="p"),
    )
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event=event.model_dump(),
        proposal=ASK,
        timestamp=datetime(2026, 10, 7, 3, 0, tzinfo=UTC),
    )
    redis = _redis([(b"1-0", {b"event": obs.model_dump_json().encode()})])

    report = await build_report(redis, days=2, now=NOW, tz_name="UTC")

    assert "· **ask** · Living Room TV: paused → playing · by an automation —" in report


async def test_a_change_that_is_not_a_state_change_still_lists() -> None:
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={"entity_id": "light.a"},
        proposal=ASK,
        timestamp=datetime(2026, 10, 7, 3, 0, tzinfo=UTC),
    )
    redis = _redis([(b"1-0", {b"event": obs.model_dump_json().encode()})])

    report = await build_report(redis, days=2, now=NOW, tz_name="UTC")

    assert "· **ask** · light.a —" in report


# --- the Asks section: counts and answers, never a name --------------------------------------

DEADLINE = datetime(2026, 10, 7, 3, 10, tzinfo=UTC)  # before NOW: an ask due then has lapsed


def an_ask(ask_id: str = "ask-1", *, deadline: datetime = DEADLINE) -> OpenAsk:
    return OpenAsk(
        ask_id=ask_id,
        tool_name="home.light_turn_on",
        targets=["light.secret_room_lamp"],
        origin="state_change",
        asked_at=deadline - timedelta(seconds=600),
        deadline=deadline,
        noticed="Maya's Film Room TV: paused to playing",
        script=False,
    )


async def answered(
    redis: fakeredis.FakeAsyncRedis, ask_id: str, answer: AnswerKind, at: datetime
) -> None:
    """The answers stream gets an entry for *answer*, made at *at* (the day the report counts)."""
    entry = ReflexAnswer(
        ask_id=ask_id,
        answer=answer,
        time=at,
        channel=None if answer == "expired" else "door",
        tool_name="home.light_turn_on",
        targets=["light.secret_room_lamp"],
        origin="state_change",
        noticed="Maya's Film Room TV: paused to playing",
    )
    await redis.xadd(
        REFLEX_ANSWERS_STREAM,
        {"event": entry.model_dump_json()},
        id=f"{int(at.timestamp() * 1000)}-0",
    )


def rows_of(report: str, heading: str) -> list[str]:
    """The table rows under *heading* up to the next heading."""
    section = report.split(heading, 1)[1].split("\n### ", 1)[0]
    return [line for line in section.splitlines() if line.startswith("| 2026-")]


async def test_the_asks_section_counts_asked_and_held_by_reason_per_day() -> None:
    redis = _redis(
        [],
        {
            "alfred:reflex:decisions:2026-10-07": {
                b"outcome:asked": b"2",
                b"outcome:held": b"7",
                b"held:tool_off": b"3",
                b"held:dnd": b"1",
                b"held:ask_open": b"2",
                b"held:daily_cap": b"1",
            }
        },
    )

    report = await build_report(redis, days=2, now=NOW, tz_name="America/Chicago")

    assert (
        "| Day (UTC) | asked | held | tool_off | quiet_hours | dnd | ask_open"
        " | asked_recently | daily_cap |"
    ) in report
    assert "| 2026-10-07 | 2 | 7 | 3 | 0 | 1 | 2 | 0 | 1 |" in report
    # Between the would-have-reversed list and the proposals, so a reader meets them in order.
    assert report.index("### Would have reversed") < report.index("### Asks")
    assert report.index("### Asks") < report.index("### Proposals")


async def test_a_day_written_before_outcomes_were_counted_shows_dashes_in_the_asks_table() -> None:
    redis = _redis([], {"alfred:reflex:decisions:2026-10-06": {b"act": b"2"}})

    report = await build_report(redis, days=2, now=NOW, tz_name="UTC")

    assert "| 2026-10-06 | — | — | — | — | — | — | — | — |" in rows_of(report, "### Asks")


async def test_the_answers_are_counted_per_day_they_were_recorded() -> None:
    redis = fakeredis.FakeAsyncRedis()
    await answered(redis, "a5", "yes", datetime(2026, 10, 5, 1, 0, tzinfo=UTC))  # before the window
    await answered(redis, "a1", "yes", datetime(2026, 10, 7, 1, 0, tzinfo=UTC))
    await answered(redis, "a2", "yes", datetime(2026, 10, 7, 2, 0, tzinfo=UTC))
    await answered(redis, "a3", "no", datetime(2026, 10, 7, 3, 0, tzinfo=UTC))
    await answered(redis, "a4", "expired", datetime(2026, 10, 8, 1, 0, tzinfo=UTC))

    report = await build_report(redis, days=2, now=NOW, tz_name="UTC")

    assert "| Day (UTC) | yes | no | expired |" in report
    answers = [line for line in rows_of(report, "### Asks") if line.count("|") == 5]
    assert answers == [
        "| 2026-10-06 | 0 | 0 | 0 |",
        "| 2026-10-07 | 2 | 1 | 0 |",
        "| 2026-10-08 | 0 | 0 | 1 |",
    ]


async def test_an_open_ask_past_its_deadline_is_settled_and_counted_as_expired() -> None:
    redis = fakeredis.FakeAsyncRedis()
    real_xadd = redis.xadd

    async def stamped_at_now(*args: Any, **kwargs: Any) -> Any:
        # fakeredis would stamp the answer with the real clock, which crosses a UTC day at
        # midnight and leaves the answer outside the report's window.
        return await real_xadd(*args, **{**kwargs, "id": f"{int(NOW.timestamp() * 1000)}-0"})

    redis.xadd = stamped_at_now  # type: ignore[method-assign]
    await redis.set(
        REFLEX_ASK_OPEN_KEY, an_ask(deadline=NOW - timedelta(minutes=1)).model_dump_json()
    )

    report = await build_report(redis, days=1, now=NOW, tz_name="UTC")

    assert await redis.exists(REFLEX_ASK_OPEN_KEY) == 0
    answers = [line for line in rows_of(report, "### Asks") if line.count("|") == 5]
    assert answers[-1].endswith("| 0 | 0 | 1 |")


async def test_an_ask_still_waiting_is_left_open() -> None:
    redis = fakeredis.FakeAsyncRedis()
    waiting = an_ask(deadline=NOW + timedelta(minutes=5))
    await redis.set(REFLEX_ASK_OPEN_KEY, waiting.model_dump_json())

    await build_report(redis, days=2, now=NOW, tz_name="UTC")

    assert await redis.exists(REFLEX_ASK_OPEN_KEY) == 1
    assert await redis.xlen(REFLEX_ANSWERS_STREAM) == 0


async def test_the_asks_section_prints_counts_only() -> None:
    redis = fakeredis.FakeAsyncRedis()
    await answered(redis, "a1", "yes", datetime(2026, 10, 7, 1, 0, tzinfo=UTC))
    await redis.set(REFLEX_ASK_OPEN_KEY, an_ask("a2").model_dump_json())

    report = await build_report(redis, days=2, now=NOW, tz_name="UTC")

    asks = report.split("### Asks", 1)[1].split("### Proposals", 1)[0]
    for private in ("secret_room", "Maya", "Film Room", "light_turn_on", "a1", "a2", "door"):
        assert private not in asks


async def test_an_unreadable_answers_stream_costs_only_the_answers_table() -> None:
    redis = _redis([], {"alfred:reflex:decisions:2026-10-07": {b"outcome:asked": b"1"}})
    redis.xrange = AsyncMock(side_effect=redis_exceptions.ConnectionError("reset"))

    report = await build_report(redis, days=2, now=NOW, tz_name="UTC")

    assert "| 2026-10-07 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |" in report
    assert "The answers could not be read, so their table is unavailable." in report
    assert "### Proposals (0)" in report


async def test_a_failed_settle_costs_nothing_else() -> None:
    redis = _redis([], {"alfred:reflex:decisions:2026-10-07": {b"outcome:asked": b"1"}})
    redis.get = AsyncMock(side_effect=redis_exceptions.ConnectionError("reset"))

    report = await build_report(redis, days=2, now=NOW, tz_name="UTC")

    assert "| 2026-10-07 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |" in report
    assert "| Day (UTC) | yes | no | expired |" in report


async def test_entries_on_the_answers_stream_that_cannot_be_read_are_skipped() -> None:
    redis = fakeredis.FakeAsyncRedis()
    await redis.xadd(
        REFLEX_ANSWERS_STREAM, {"event": "garbage"}, id="1791344400000-0"
    )  # 2026-10-07 03:40 UTC
    await redis.xadd(REFLEX_ANSWERS_STREAM, {"other": "x"}, id="1791344400001-0")

    report = await build_report(redis, days=2, now=NOW, tz_name="UTC")

    answers = [line for line in rows_of(report, "### Asks") if line.count("|") == 5]
    assert answers == [
        "| 2026-10-06 | 0 | 0 | 0 |",
        "| 2026-10-07 | 0 | 0 | 0 |",
        "| 2026-10-08 | 0 | 0 | 0 |",
    ]
