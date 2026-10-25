"""The attribution report: counts by cause, what Alfred changed, and what others changed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis

from bus.schemas.events import ActionRequest, ActionResult, ChangeContext, StateChangedEvent
from core.attribution.log import record_call, record_change
from core.attribution.report import PARTS, build_report
from shared.streams import ATTRIBUTION_CALLS_STREAM, ATTRIBUTION_CHANGES_STREAM

_NOW = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)  # Sat 24 Oct, 13:00 in Chicago
_TZ = "America/Chicago"
_MISMATCH = (
    "{n} changes matched Alfred's calls but were not attributed to Alfred: "
    "home-service is not connected to Home Assistant as a user of its own."
)


def _redis() -> Any:
    return fakeredis.FakeAsyncRedis()


async def _call(
    redis: Any,
    at: datetime,
    *,
    source: str = "conscious-engine",
    context_id: str | None = "abc",
    tool: str = "smart_home.dim_lights",
    parameters: dict[str, Any] | None = None,
    reason: str | None = None,
) -> None:
    action = ActionRequest(
        source=source,
        target_service="home",
        tool_name=tool,
        parameters=parameters or {"entity_id": "light.bedroom_lamp", "brightness": 20},
        reason=reason,
    )
    result = ActionResult(
        source="home-agent",
        request_id=action.request_id,
        tool_name=tool,
        status="success",
        result={"id": action.request_id, "result": {"context_id": context_id}}
        if context_id
        else {"id": action.request_id, "result": {}},
    )
    await record_call(redis, action, result, now=at)


async def _change(
    redis: Any,
    at: datetime,
    context: ChangeContext | None,
    *,
    entity_id: str = "light.bedroom_lamp",
    old: str | None = "on",
    new: str = "off",
) -> None:
    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id=entity_id,
        old_state=old,
        new_state=new,
        context=context,
        timestamp=at,
    )
    await record_change(redis, event, now=at)


async def test_counts_table_has_every_cause_column() -> None:
    redis = _redis()
    day_before = _NOW - timedelta(days=1)
    await _change(redis, day_before, ChangeContext(user_id="u1", person="person.alex"))
    await _change(redis, day_before, None)
    await _change(redis, _NOW, ChangeContext(by_service=True, id="x"))
    await _change(redis, _NOW, ChangeContext(parent_id="p"))
    await _change(redis, _NOW, ChangeContext(id="wall-switch"))

    report = await build_report(redis, days=1, now=_NOW, tz_name=_TZ)

    assert "| Day (UTC) | person | alfred | automation | outside | unknown |" in report
    assert "| 2026-10-23 | 1 | 0 | 0 | 0 | 1 |" in report
    assert "| 2026-10-24 | 0 | 1 | 1 | 1 | 0 |" in report


async def test_a_day_with_no_changes_still_has_a_row() -> None:
    report = await build_report(_redis(), days=1, now=_NOW, tz_name=_TZ)

    assert "| 2026-10-23 | 0 | 0 | 0 | 0 | 0 |" in report
    assert "| 2026-10-24 | 0 | 0 | 0 | 0 | 0 |" in report


async def test_alfred_call_lists_the_changes_it_caused() -> None:
    redis = _redis()
    at = _NOW - timedelta(hours=2)
    await _call(redis, at, context_id="abc", reason="bedtime")
    await _change(redis, at, ChangeContext(id="abc", by_service=True))
    await _change(
        redis,
        at,
        ChangeContext(id="abc", by_service=True),
        entity_id="light.hall_lamp",
        old="off",
        new="on",
    )

    report = await build_report(redis, days=1, now=_NOW, tz_name=_TZ)

    assert "### What Alfred changed (1)" in report
    bullet = (
        "- Sat 24 Oct 11:00 · System 2 · smart_home.dim_lights(entity_id=light.bedroom_lamp, "
        'brightness=20) — "bedtime"'
    )
    assert bullet in report
    after = report.split(bullet, 1)[1].split("###", 1)[0]
    assert "light.bedroom_lamp: on → off" in after
    assert "light.hall_lamp: off → on" in after
    assert "by Alfred" in after
    assert "no change seen" not in after
    assert "no context id" not in after


async def test_a_call_without_a_reason_has_no_quotes() -> None:
    redis = _redis()
    await _call(redis, _NOW - timedelta(hours=1), reason=None)

    report = await build_report(redis, days=1, now=_NOW, tz_name=_TZ)

    line = next(ln for ln in report.splitlines() if "smart_home.dim_lights" in ln)
    assert line.endswith("brightness=20)")


async def test_a_script_alfred_ran_is_joined_by_parent_id() -> None:
    redis = _redis()
    at = _NOW - timedelta(hours=2)
    await _call(redis, at, context_id="abc", tool="smart_home.run_script")
    await _change(
        redis, at, ChangeContext(id="child", parent_id="abc"), entity_id="light.hall_lamp"
    )

    report = await build_report(redis, days=1, now=_NOW, tz_name=_TZ)

    after = report.split("smart_home.run_script", 1)[1].split("###", 1)[0]
    assert "light.hall_lamp: on → off" in after
    assert "by an automation" in after
    # A script's children are an automation's work by design: not the operator-step mismatch.
    assert "were not attributed to Alfred" not in report


async def test_call_without_context_id_says_so() -> None:
    redis = _redis()
    await _call(redis, _NOW - timedelta(hours=1), context_id=None)

    report = await build_report(redis, days=1, now=_NOW, tz_name=_TZ)

    assert "no context id (older home-service)" in report
    assert "no change seen" not in report


async def test_call_with_no_change_says_so() -> None:
    redis = _redis()
    await _call(redis, _NOW - timedelta(hours=1), context_id="abc")
    await _change(redis, _NOW - timedelta(hours=1), ChangeContext(id="other", by_service=True))

    report = await build_report(redis, days=1, now=_NOW, tz_name=_TZ)

    assert "no change seen" in report
    assert "no context id" not in report


async def test_a_source_not_in_parts_is_named_as_it_is() -> None:
    redis = _redis()
    await _call(redis, _NOW - timedelta(hours=1), source="some-new-engine")

    report = await build_report(redis, days=1, now=_NOW, tz_name=_TZ)

    assert "· some-new-engine ·" in report
    assert PARTS["reflex-engine"] == "Reflex"
    assert PARTS["trigger-engine"] == "a trigger"
    assert PARTS["admin-api"] == "the admin API"


async def test_people_and_automations_section_excludes_matched_changes() -> None:
    redis = _redis()
    at = _NOW - timedelta(hours=3)
    await _call(redis, at, context_id="abc")
    await _change(redis, at, ChangeContext(id="abc", by_service=True), entity_id="light.matched")
    await _change(
        redis,
        at + timedelta(minutes=5),
        ChangeContext(id="p1", user_id="u1", person="person.alex"),
        entity_id="light.kitchen",
        old="off",
        new="on",
    )
    await _change(
        redis,
        at + timedelta(minutes=10),
        ChangeContext(id="a1", parent_id="auto"),
        entity_id="cover.blind",
        old="open",
        new="closed",
    )
    # Alfred's own change that no call in the window explains is not a person's or an automation's.
    await _change(
        redis, at + timedelta(minutes=15), ChangeContext(id="z", by_service=True), entity_id="x.y"
    )

    report = await build_report(redis, days=1, now=_NOW, tz_name=_TZ)

    section = report.split("### Changes by people and automations", 1)[1]
    assert "### Changes by people and automations (2)" in report
    assert "light.kitchen: off → on · by person.alex" in section
    assert "cover.blind: open → closed · by an automation" in section
    assert "light.matched" not in section
    assert "x.y" not in section
    assert section.index("light.kitchen") < section.index("cover.blind")  # oldest first


async def test_times_are_in_the_users_zone() -> None:
    redis = _redis()
    await _change(redis, datetime(2026, 10, 24, 3, 30, tzinfo=UTC), ChangeContext(user_id="u1"))

    report = await build_report(redis, days=1, now=_NOW, tz_name=_TZ)

    assert "- Fri 23 Oct 22:30 · light.bedroom_lamp: on → off · by a person" in report
    assert "times in America/Chicago" in report


async def test_a_change_with_no_old_state_reads_as_none() -> None:
    redis = _redis()
    await _change(redis, _NOW, ChangeContext(user_id="u1"), old=None, new="on")

    report = await build_report(redis, days=1, now=_NOW, tz_name=_TZ)

    assert "light.bedroom_lamp: (none) → on" in report


async def test_flags_alfred_changes_read_as_a_person() -> None:
    redis = _redis()
    at = _NOW - timedelta(hours=2)
    await _call(redis, at, context_id="abc")
    await _change(redis, at, ChangeContext(id="abc", user_id="owner", person="person.alex"))

    report = await build_report(redis, days=1, now=_NOW, tz_name=_TZ)

    assert report.startswith(_MISMATCH.format(n=1))
    assert "light.bedroom_lamp: on → off · by person.alex" in report


async def test_no_mismatch_line_when_every_matched_change_is_alfreds() -> None:
    redis = _redis()
    at = _NOW - timedelta(hours=2)
    await _call(redis, at, context_id="abc")
    await _change(redis, at, ChangeContext(id="abc", by_service=True))

    report = await build_report(redis, days=1, now=_NOW, tz_name=_TZ)

    assert report.startswith("## Alfred attribution report: the last 1 days")
    assert "were not attributed to Alfred" not in report


async def test_window_excludes_older_entries() -> None:
    redis = _redis()
    old = _NOW - timedelta(days=8)
    await _call(redis, old, context_id="old", tool="smart_home.old_call")
    await _change(redis, old, ChangeContext(user_id="u1"), entity_id="light.old")
    await _call(redis, _NOW - timedelta(days=6), context_id="new", tool="smart_home.new_call")

    report = await build_report(redis, days=7, now=_NOW, tz_name=_TZ)

    assert "smart_home.old_call" not in report
    assert "light.old" not in report
    assert "smart_home.new_call" in report


async def test_the_report_skips_entries_that_do_not_validate() -> None:
    redis = _redis()
    await redis.xadd(ATTRIBUTION_CALLS_STREAM, {"call": "{not json"})
    await redis.xadd(ATTRIBUTION_CALLS_STREAM, {"other": "field"})
    await redis.xadd(ATTRIBUTION_CHANGES_STREAM, {"change": '{"entity_id": 3}'})
    await _call(redis, _NOW - timedelta(hours=1))

    report = await build_report(redis, days=1, now=_NOW, tz_name=_TZ)

    assert "### What Alfred changed (1)" in report


async def test_an_empty_window_says_so() -> None:
    report = await build_report(_redis(), days=7, now=_NOW, tz_name=_TZ)

    assert report.count("None in this window.") == 2
    assert "### What Alfred changed (0)" in report
    assert "### Changes by people and automations (0)" in report
