"""Shadow report — a week of Reflex proposals for the owner's verdicts (#285)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from bus.schemas.events import (
    ActionRequest,
    ReflexObservation,
    ReflexProposal,
    StateChangedEvent,
    TriggerFired,
)
from core.reflex import shadow_report
from core.reflex.shadow_report import build_report

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


def _redis(
    entries: list[tuple[bytes, dict[bytes, bytes]]],
    counts: dict[str, dict[bytes, bytes]] | None = None,
) -> AsyncMock:
    redis = AsyncMock()
    redis.xrevrange = AsyncMock(return_value=list(reversed(entries)))  # newest first
    redis.hgetall = AsyncMock(side_effect=lambda key: (counts or {}).get(key, {}))
    return redis


async def test_the_counts_table_covers_every_utc_day_in_the_window() -> None:
    redis = _redis([], {"alfred:reflex:decisions:2026-10-07": {b"act": b"1", b"none": b"40"}})

    report = await build_report(redis, days=2, now=NOW, tz_name="America/Chicago")

    assert "| 2026-10-06 | 0 | 0 | 0 | 0 |" in report
    assert "| 2026-10-07 | 1 | 0 | 40 | 0 |" in report
    assert "| 2026-10-08 | 0 | 0 | 0 | 0 |" in report


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
