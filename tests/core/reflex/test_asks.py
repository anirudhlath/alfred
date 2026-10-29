"""Asks: the gate that keeps them few, and the asking that parks and announces one.

The tool names and ids are test data (a generic ``home`` service and a made-up ``media`` one);
the code under test names no tool.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import create_autospec
from zoneinfo import ZoneInfo

import fakeredis
import pytest

from bus.schemas.events import ActionRequest, OutcomeKind, ReflexOutcome, ReflexProposal
from core.notifications.dnd import DNDChecker
from core.notifications.publisher import NotificationPublisher
from core.notifications.schema import Urgency
from core.reflex.answers import OpenAsk
from core.reflex.asks import (
    ask_text,
    asked_key,
    asks_key,
    gate,
    in_quiet_hours,
    open_ask,
)
from core.reflex.settings import ReflexSettings
from core.routing.pending import pending_key, read_pending
from shared.streams import DND_STATE_KEY, REFLEX_ASK_OPEN_KEY

if TYPE_CHECKING:
    from datetime import time

    from bus.schemas.events import HeldReason

NEW_YORK = "America/New_York"
LAMP = "light.lamp"
TOOL = "home.light_turn_on"


def at(hour: int, minute: int = 0, *, day: int = 28) -> datetime:
    """An aware moment on a day of October 2026 in New York."""
    return datetime(2026, 10, day, hour, minute, tzinfo=ZoneInfo(NEW_YORK))


NOON = at(12)


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


@pytest.fixture
def dnd(redis: fakeredis.FakeAsyncRedis) -> DNDChecker:
    return DNDChecker(redis, None)


def make_settings(**changes: Any) -> ReflexSettings:
    """Ask mode, the test tool asked about, and no quiet hours unless a test sets them."""
    fields: dict[str, Any] = {
        "mode": "ask",
        "ask_tools": ["home.*"],
        "quiet_hours_start": "00:00",
        "quiet_hours_end": "00:00",
    }
    return ReflexSettings(**{**fields, **changes})


def make_proposal(
    request_id: str = "ask-1", tool: str = TOOL, reason: str | None = "It is getting dark."
) -> ReflexProposal:
    return ReflexProposal(
        decision="act",
        reason=reason,
        action=ActionRequest(
            source="reflex-engine",
            request_id=request_id,
            target_service="home-service",
            tool_name=tool,
            parameters={"target": "Lamp"},
            reason=reason,
        ),
    )


async def run_gate(
    redis: fakeredis.FakeAsyncRedis,
    dnd: DNDChecker,
    settings: ReflexSettings | None = None,
    *,
    now: datetime = NOON,
    outcome: ReflexOutcome | None = None,
    tool: str = TOOL,
    targets: tuple[str, ...] = (LAMP,),
) -> ReflexOutcome:
    return await gate(
        redis,
        settings or make_settings(),
        outcome or ReflexOutcome(kind="shadow"),
        make_proposal(tool=tool),
        targets=targets,
        tz_name=NEW_YORK,
        now=now,
        dnd=dnd,
    )


def an_open_ask(deadline: datetime, ask_id: str = "open-1") -> OpenAsk:
    return OpenAsk(
        ask_id=ask_id,
        tool_name=TOOL,
        targets=[LAMP],
        origin="state_change",
        asked_at=deadline - timedelta(seconds=600),
        deadline=deadline,
        noticed="Lamp turned off",
        script=False,
    )


async def hold_open(redis: fakeredis.FakeAsyncRedis, deadline: datetime) -> None:
    await redis.set(REFLEX_ASK_OPEN_KEY, an_open_ask(deadline).model_dump_json())


async def turn_dnd_on(redis: fakeredis.FakeAsyncRedis) -> None:
    await redis.set(DND_STATE_KEY, json.dumps({"active": True, "source": "manual"}))


# --- the gate: each limit on its own ----------------------------------------------------------


async def test_a_proposal_that_passes_every_limit_is_asked(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    result = await run_gate(redis, dnd)

    assert result == ReflexOutcome(kind="asked")
    assert result.ask_id is None  # the runner sets it once open_ask has run


@pytest.mark.parametrize("kind", ["none", "invalid", "would_reverse", "asked", "held"])
async def test_any_outcome_but_shadow_comes_back_unchanged(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker, kind: OutcomeKind
) -> None:
    outcome = ReflexOutcome(kind=kind)

    result = await run_gate(redis, dnd, outcome=outcome)

    assert result is outcome


async def test_a_tool_no_pattern_matches_is_off(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    result = await run_gate(redis, dnd, tool="media.play_something")

    assert result == ReflexOutcome(kind="held", reason="tool_off")


async def test_patterns_match_the_whole_name_and_case(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    exact = make_settings(ask_tools=[TOOL])
    assert (await run_gate(redis, dnd, exact)).kind == "asked"
    assert (await run_gate(redis, dnd, make_settings(ask_tools=["home.light"]))).reason == (
        "tool_off"
    )
    assert (await run_gate(redis, dnd, make_settings(ask_tools=["HOME.*"]))).reason == "tool_off"
    assert (await run_gate(redis, dnd, make_settings(ask_tools=["media.*", "*light*"]))).kind == (
        "asked"
    )


async def test_an_empty_ask_tools_list_turns_every_tool_off(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    result = await run_gate(redis, dnd, make_settings(ask_tools=[]))

    assert result == ReflexOutcome(kind="held", reason="tool_off")


async def test_quiet_hours_hold_an_ask(redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker) -> None:
    settings = make_settings(quiet_hours_start="23:00", quiet_hours_end="07:00")

    result = await run_gate(redis, dnd, settings, now=at(2))

    assert result == ReflexOutcome(kind="held", reason="quiet_hours")


async def test_do_not_disturb_holds_an_ask(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    await turn_dnd_on(redis)

    result = await run_gate(redis, dnd)

    assert result == ReflexOutcome(kind="held", reason="dnd")


async def test_an_open_ask_holds_the_next(redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker) -> None:
    await hold_open(redis, NOON + timedelta(minutes=5))

    result = await run_gate(redis, dnd)

    assert result == ReflexOutcome(kind="held", reason="ask_open")
    assert await redis.exists(REFLEX_ASK_OPEN_KEY)  # a waiting ask is left alone


async def test_an_expired_ask_is_settled_and_no_longer_holds(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    await hold_open(redis, NOON - timedelta(seconds=1))

    result = await run_gate(redis, dnd)

    assert result == ReflexOutcome(kind="asked")
    assert not await redis.exists(REFLEX_ASK_OPEN_KEY)


async def test_an_ask_whose_deadline_is_now_is_expired(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    await hold_open(redis, NOON)

    assert (await run_gate(redis, dnd)).kind == "asked"
    assert not await redis.exists(REFLEX_ASK_OPEN_KEY)


async def test_an_unreadable_open_ask_is_settled_not_held(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    await redis.set(REFLEX_ASK_OPEN_KEY, "{not json")

    assert (await run_gate(redis, dnd)).kind == "asked"
    assert not await redis.exists(REFLEX_ASK_OPEN_KEY)


async def test_asking_about_the_same_thing_lately_is_held(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    await redis.set(asked_key(TOOL, [LAMP]), "1")

    result = await run_gate(redis, dnd)

    assert result == ReflexOutcome(kind="held", reason="asked_recently")


async def test_the_repeat_key_is_the_tool_and_the_sorted_targets(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    assert asked_key(TOOL, ["b", "a"]) == f"alfred:reflex:asked:{TOOL}:a,b"
    await redis.set(asked_key(TOOL, ["light.b", "light.a"]), "1")

    held = await run_gate(redis, dnd, targets=("light.a", "light.b"))
    other_targets = await run_gate(redis, dnd, targets=("light.a",))
    other_tool = await run_gate(redis, dnd, make_settings(ask_tools=["*"]), tool="home.other")

    assert held.reason == "asked_recently"
    assert other_targets.kind == "asked"
    assert other_tool.kind == "asked"


async def test_the_daily_cap_holds_once_reached(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    settings = make_settings(daily_ask_cap=3)
    day = asks_key(NOON.date())

    await redis.set(day, 2)
    below = await run_gate(redis, dnd, settings)
    await redis.set(day, 3)
    at_cap = await run_gate(redis, dnd, settings)

    assert below.kind == "asked"
    assert at_cap == ReflexOutcome(kind="held", reason="daily_cap")


async def test_a_cap_of_zero_holds_every_ask(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    result = await run_gate(redis, dnd, make_settings(daily_ask_cap=0))

    assert result == ReflexOutcome(kind="held", reason="daily_cap")


async def test_the_daily_count_is_by_local_date(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    settings = make_settings(daily_ask_cap=2)
    evening = at(21)  # 01:00 on the 29th in UTC, still the 28th in New York
    assert evening.astimezone(UTC).day == 29

    await redis.set("alfred:reflex:asks:2026-10-27", 5)  # yesterday's, local
    await redis.set("alfred:reflex:asks:2026-10-29", 5)  # the UTC date's
    assert (await run_gate(redis, dnd, settings, now=evening)).kind == "asked"

    await redis.set("alfred:reflex:asks:2026-10-28", 2)  # today's, local
    assert (await run_gate(redis, dnd, settings, now=evening)).reason == "daily_cap"


async def test_the_gate_writes_nothing(redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker) -> None:
    await run_gate(redis, dnd)
    await run_gate(redis, dnd, make_settings(ask_tools=[]))

    assert await redis.keys("*") == []


# --- the gate: order --------------------------------------------------------------------------


async def test_quiet_hours_are_named_before_an_open_ask(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    settings = make_settings(quiet_hours_start="23:00", quiet_hours_end="07:00")
    await hold_open(redis, at(3) + timedelta(minutes=5))

    result = await run_gate(redis, dnd, settings, now=at(3))

    assert result == ReflexOutcome(kind="held", reason="quiet_hours")


async def test_every_limit_hit_at_once_is_reported_in_the_order_of_the_brief(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    now = at(2)
    settings = make_settings(
        ask_tools=[],
        quiet_hours_start="23:00",
        quiet_hours_end="07:00",
        daily_ask_cap=0,
    )
    await turn_dnd_on(redis)
    await hold_open(redis, now + timedelta(minutes=5))
    await redis.set(asked_key(TOOL, [LAMP]), "1")
    order: list[HeldReason | None] = []

    order.append((await run_gate(redis, dnd, settings, now=now)).reason)  # tool_off
    settings = settings.model_copy(update={"ask_tools": ["home.*"]})
    order.append((await run_gate(redis, dnd, settings, now=now)).reason)  # quiet_hours
    settings = settings.model_copy(
        update={"quiet_hours_start": "00:00", "quiet_hours_end": "00:00"}
    )
    order.append((await run_gate(redis, dnd, settings, now=now)).reason)  # dnd
    await redis.delete(DND_STATE_KEY)
    order.append((await run_gate(redis, dnd, settings, now=now)).reason)  # ask_open
    await redis.delete(REFLEX_ASK_OPEN_KEY)
    order.append((await run_gate(redis, dnd, settings, now=now)).reason)  # asked_recently
    await redis.delete(asked_key(TOOL, [LAMP]))
    order.append((await run_gate(redis, dnd, settings, now=now)).reason)  # daily_cap

    assert order == ["tool_off", "quiet_hours", "dnd", "ask_open", "asked_recently", "daily_cap"]


# --- the gate: shadow mode --------------------------------------------------------------------


async def test_shadow_mode_stays_shadow_and_checks_only_the_first_three(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    settings = make_settings(mode="shadow", daily_ask_cap=0)
    await hold_open(redis, NOON + timedelta(minutes=5))
    await redis.set(asked_key(TOOL, [LAMP]), "1")

    result = await run_gate(redis, dnd, settings)

    assert result == ReflexOutcome(kind="shadow", reason=None)


async def test_shadow_mode_reports_the_first_of_the_three(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    quiet = {"quiet_hours_start": "23:00", "quiet_hours_end": "07:00"}
    shadow = make_settings(mode="shadow")

    tool_off = await run_gate(redis, dnd, shadow, tool="media.play_something")
    in_quiet = await run_gate(redis, dnd, make_settings(mode="shadow", **quiet), now=at(2))
    await turn_dnd_on(redis)
    dnd_on = await run_gate(redis, dnd, shadow)

    assert tool_off == ReflexOutcome(kind="shadow", reason="tool_off")
    assert in_quiet == ReflexOutcome(kind="shadow", reason="quiet_hours")
    assert dnd_on == ReflexOutcome(kind="shadow", reason="dnd")


async def test_shadow_mode_leaves_an_expired_ask_alone(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    await hold_open(redis, NOON - timedelta(minutes=1))

    await run_gate(redis, dnd, make_settings(mode="shadow"))

    assert await redis.exists(REFLEX_ASK_OPEN_KEY)


# --- quiet hours ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hour", "minute", "asks"),
    [(22, 59, True), (23, 0, False), (6, 59, False), (7, 0, True)],
)
async def test_the_default_quiet_hours_in_the_owners_zone(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker, hour: int, minute: int, asks: bool
) -> None:
    settings = ReflexSettings(mode="ask", ask_tools=["home.*"])  # the defaults: 23:00 to 07:00

    result = await run_gate(redis, dnd, settings, now=at(hour, minute))

    assert result == (
        ReflexOutcome(kind="asked") if asks else ReflexOutcome(kind="held", reason="quiet_hours")
    )


async def test_quiet_hours_use_the_local_time_not_utc(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    settings = ReflexSettings(mode="ask", ask_tools=["home.*"])
    now = datetime(2026, 10, 28, 3, 30, tzinfo=UTC)  # 23:30 in New York, outside by UTC

    assert (await run_gate(redis, dnd, settings, now=now)).reason == "quiet_hours"


def clock(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


@pytest.mark.parametrize(
    ("local", "start", "end", "quiet"),
    [
        # A window inside one day: start up to but not including end.
        ("08:59", "09:00", "17:00", False),
        ("09:00", "09:00", "17:00", True),
        ("16:59", "09:00", "17:00", True),
        ("17:00", "09:00", "17:00", False),
        # A window that wraps midnight.
        ("22:59", "23:00", "07:00", False),
        ("23:00", "23:00", "07:00", True),
        ("00:00", "23:00", "07:00", True),
        ("06:59", "23:00", "07:00", True),
        ("07:00", "23:00", "07:00", False),
        ("12:00", "23:00", "07:00", False),
        # Equal start and end: no quiet hours at all.
        ("00:00", "00:00", "00:00", False),
        ("12:00", "12:00", "12:00", False),
        ("23:59", "07:00", "07:00", False),
    ],
)
def test_in_quiet_hours(local: str, start: str, end: str, quiet: bool) -> None:
    assert in_quiet_hours(clock(local), start, end) is quiet


# --- open_ask ---------------------------------------------------------------------------------


def make_notifier() -> Any:
    return create_autospec(NotificationPublisher, instance=True)


async def ask_about(
    redis: fakeredis.FakeAsyncRedis,
    notifier: Any,
    proposal: ReflexProposal | None = None,
    **changes: Any,
) -> OpenAsk | None:
    arguments: dict[str, Any] = {
        "settings": make_settings(),
        "origin": "state_change",
        "noticed": "Lamp turned off",
        "targets": [LAMP],
        "names": {LAMP: "Lamp"},
        "script": False,
        "now": NOON,
        "tz_name": NEW_YORK,
        "service": "turn_on",
    }
    return await open_ask(redis, notifier, proposal or make_proposal(), **{**arguments, **changes})


async def test_open_ask_parks_the_action_for_the_expiry(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    proposal = make_proposal()

    await ask_about(redis, make_notifier(), proposal)

    parked = await read_pending(redis, "ask-1")
    assert parked is not None
    assert parked.record_id == "ask-1"
    assert parked.action_requests() == [proposal.action]
    assert 595 <= await redis.ttl(pending_key("ask-1")) <= 600


async def test_open_ask_takes_its_expiry_from_the_settings(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await ask_about(redis, make_notifier(), settings=make_settings(ask_expiry_seconds=120))

    assert 115 <= await redis.ttl(pending_key("ask-1")) <= 120


async def test_open_ask_writes_the_open_key_the_repeat_key_and_the_count(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    ask = await ask_about(redis, make_notifier())

    assert ask == OpenAsk(
        ask_id="ask-1",
        tool_name=TOOL,
        targets=[LAMP],
        origin="state_change",
        asked_at=NOON,
        deadline=NOON + timedelta(seconds=600),
        noticed="Lamp turned off",
        script=False,
    )
    stored = await redis.get(REFLEX_ASK_OPEN_KEY)
    assert stored is not None
    assert OpenAsk.model_validate_json(stored) == ask
    assert 600 + 86_400 - 5 <= await redis.ttl(REFLEX_ASK_OPEN_KEY) <= 600 + 86_400
    repeat = asked_key(TOOL, [LAMP])
    assert await redis.exists(repeat)
    assert 7_195 <= await redis.ttl(repeat) <= 7_200  # the default two hours
    day = asks_key(NOON.date())
    assert day == "alfred:reflex:asks:2026-10-28"
    assert int(await redis.get(day)) == 1
    assert 2 * 86_400 - 5 <= await redis.ttl(day) <= 2 * 86_400


async def test_the_count_goes_up_by_one_per_ask(redis: fakeredis.FakeAsyncRedis) -> None:
    await redis.set(asks_key(NOON.date()), 2)

    await ask_about(redis, make_notifier())

    assert int(await redis.get(asks_key(NOON.date()))) == 3


async def test_the_count_is_kept_under_the_local_date(redis: fakeredis.FakeAsyncRedis) -> None:
    await ask_about(redis, make_notifier(), now=at(21))  # the 29th in UTC

    assert await redis.exists("alfred:reflex:asks:2026-10-28")
    assert not await redis.exists("alfred:reflex:asks:2026-10-29")


async def test_a_repeat_limit_of_zero_sets_no_repeat_key(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await ask_about(redis, make_notifier(), settings=make_settings(repeat_hours=0))

    assert not await redis.exists(asked_key(TOOL, [LAMP]))
    assert await redis.exists(REFLEX_ASK_OPEN_KEY)


async def test_the_repeat_key_lasts_a_whole_positive_number_of_seconds(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await ask_about(redis, make_notifier(), settings=make_settings(repeat_hours=0.5))
    await ask_about(
        redis,
        make_notifier(),
        make_proposal("ask-2", tool="home.other"),
        settings=make_settings(repeat_hours=0.0001),
    )

    assert 1_795 <= await redis.ttl(asked_key(TOOL, [LAMP])) <= 1_800
    assert await redis.ttl(asked_key("home.other", [LAMP])) == -2  # the first ask holds the claim

    await redis.delete(REFLEX_ASK_OPEN_KEY)
    await ask_about(
        redis,
        make_notifier(),
        make_proposal("ask-3", tool="home.other"),
        settings=make_settings(repeat_hours=0.0001),
    )
    assert 0 < await redis.ttl(asked_key("home.other", [LAMP])) <= 1


async def test_open_ask_publishes_one_important_notification(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    notifier = make_notifier()

    await ask_about(redis, notifier)

    notifier.publish.assert_awaited_once()
    args, kwargs = notifier.publish.await_args
    assert args == (
        "Shall I turn on Lamp?",
        "Lamp turned off. It is getting dark. Reply yes or no.",
    )
    assert kwargs["source"] == "reflex-engine"
    assert kwargs["urgency"] is Urgency.IMPORTANT
    assert kwargs["metadata"] == {
        "pending_action_id": "ask-1",
        "tool_name": TOOL,
        "parameters": {"target": "Lamp"},
        "reason": "It is getting dark.",
        "kind": "reflex_ask",
    }


async def test_open_ask_names_the_targets_by_the_names_it_is_given(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    notifier = make_notifier()

    await ask_about(
        redis,
        notifier,
        targets=["light.b", "light.a", "light.unnamed"],
        names={"light.a": "Hall", "light.b": "Lamp"},
    )

    assert notifier.publish.await_args.args[0] == "Shall I turn on Lamp, Hall and light.unnamed?"


async def test_open_ask_for_a_script_runs_the_script(redis: fakeredis.FakeAsyncRedis) -> None:
    notifier = make_notifier()

    ask = await ask_about(
        redis,
        notifier,
        targets=["script.movie"],
        names={"script.movie": "Movie night"},
        script=True,
    )

    assert ask is not None
    assert ask.script is True
    assert notifier.publish.await_args.args[0] == "Shall I run the script Movie night?"


async def test_open_ask_does_each_step_after_the_one_before(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    notifier = make_notifier()
    seen: dict[str, bool] = {}

    async def publish(*_: Any, **__: Any) -> None:
        seen["open"] = bool(await redis.exists(REFLEX_ASK_OPEN_KEY))
        seen["parked"] = bool(await redis.exists(pending_key("ask-1")))
        seen["repeat"] = bool(await redis.exists(asked_key(TOOL, [LAMP])))
        seen["count"] = bool(await redis.exists(asks_key(NOON.date())))

    notifier.publish.side_effect = publish

    await ask_about(redis, notifier)

    assert seen == {"open": True, "parked": True, "repeat": True, "count": True}


async def test_a_second_ask_while_one_is_open_parks_nothing(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    first = await ask_about(redis, make_notifier())
    notifier = make_notifier()
    second_proposal = make_proposal("ask-2", tool="home.other")

    second = await ask_about(redis, notifier, second_proposal, targets=["light.other"])

    assert first is not None
    assert second is None
    notifier.publish.assert_not_awaited()
    assert not await redis.exists(pending_key("ask-2"))
    assert not await redis.exists(asked_key("home.other", ["light.other"]))
    assert int(await redis.get(asks_key(NOON.date()))) == 1  # only the first ask is counted
    stored = await redis.get(REFLEX_ASK_OPEN_KEY)
    assert stored is not None
    assert OpenAsk.model_validate_json(stored).ask_id == "ask-1"  # the first still holds it


async def test_a_failure_after_the_claim_leaves_the_open_key_behind(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    notifier = make_notifier()
    notifier.publish.side_effect = RuntimeError("the channel is down")

    with pytest.raises(RuntimeError, match="the channel is down"):
        await ask_about(redis, notifier)

    # A redelivery of the event finds the ask open instead of asking twice.
    assert await redis.exists(REFLEX_ASK_OPEN_KEY)
    assert (await run_gate(redis, dnd)).reason == "ask_open"


async def test_open_ask_needs_an_action(redis: fakeredis.FakeAsyncRedis) -> None:
    with pytest.raises(ValueError, match="action"):
        await ask_about(redis, make_notifier(), ReflexProposal(decision="none"))

    assert await redis.keys("*") == []


async def test_an_ask_the_gate_passed_can_then_be_opened_and_the_next_is_held(
    redis: fakeredis.FakeAsyncRedis, dnd: DNDChecker
) -> None:
    assert (await run_gate(redis, dnd)).kind == "asked"
    assert await ask_about(redis, make_notifier()) is not None

    again = await run_gate(redis, dnd, targets=("light.other",))

    assert again == ReflexOutcome(kind="held", reason="ask_open")


# --- ask_text ---------------------------------------------------------------------------------


def test_ask_text_for_a_light() -> None:
    title, body = ask_text(
        "home.light_turn_on",
        {"target": "Lamp"},
        noticed="Lamp turned off",
        reason="It is getting dark.",
        names=["Lamp"],
        script=False,
        service="turn_on",
    )

    assert title == "Shall I turn on Lamp?"
    assert body == "Lamp turned off. It is getting dark. Reply yes or no."


def test_ask_text_for_a_script() -> None:
    title, body = ask_text(
        "home.script_run",
        {"target": "script.movie"},
        noticed="The TV turned on",
        reason="",
        names=["Movie night"],
        script=True,
        service="run",
    )

    assert title == "Shall I run the script Movie night?"
    assert body == "The TV turned on. Reply yes or no."


def test_ask_text_without_a_service_uses_the_end_of_the_tool_name() -> None:
    title, _ = ask_text(
        "media.dim_the_screen",
        {},
        noticed="n",
        reason="r",
        names=["Den"],
        script=False,
    )

    assert title == "Shall I dim the screen Den?"


def test_ask_text_with_a_service_ignores_the_tool_name() -> None:
    title, _ = ask_text(
        "a.b.c_d",
        {},
        noticed="n",
        reason="r",
        names=["Den"],
        script=False,
        service="set_volume",
    )

    assert title == "Shall I set volume Den?"


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["A"], "Shall I turn on A?"),
        (["A", "B"], "Shall I turn on A and B?"),
        (["A", "B", "C"], "Shall I turn on A, B and C?"),
        (["A", "B", "C", "D"], "Shall I turn on A, B, C and D?"),
        ([], "Shall I turn on?"),
    ],
)
def test_ask_text_joins_the_names(names: list[str], expected: str) -> None:
    title, _ = ask_text(
        "home.light_turn_on",
        {},
        noticed="n",
        reason="r",
        names=names,
        script=False,
        service="turn_on",
    )

    assert title == expected


@pytest.mark.parametrize(
    ("noticed", "reason", "body"),
    [
        ("Lamp turned off", "It is dark.", "Lamp turned off. It is dark. Reply yes or no."),
        ("Lamp turned off.", "It is dark.", "Lamp turned off. It is dark. Reply yes or no."),
        ("Lamp turned off", "It is dark", "Lamp turned off. It is dark. Reply yes or no."),
        ("Lamp turned off", "Is it dark?", "Lamp turned off. Is it dark? Reply yes or no."),
        ("Lamp turned off", "", "Lamp turned off. Reply yes or no."),
        ("Lamp turned off", "  ", "Lamp turned off. Reply yes or no."),
        ("", "", "Reply yes or no."),
    ],
)
def test_ask_text_body(noticed: str, reason: str, body: str) -> None:
    _, text = ask_text(
        "home.light_turn_on",
        {},
        noticed=noticed,
        reason=reason,
        names=["Lamp"],
        script=False,
        service="turn_on",
    )

    assert text == body


def test_ask_text_clips_a_long_body_and_keeps_the_last_line_whole() -> None:
    _, body = ask_text(
        "home.light_turn_on",
        {},
        noticed="Lamp turned off after a very long evening " * 4,
        reason="Because the light outside is fading and the room is getting dark. " * 6,
        names=["Lamp"],
        script=False,
        service="turn_on",
    )

    assert len(body) <= 300
    assert body.endswith("… Reply yes or no.")
    assert body.startswith("Lamp turned off after a very long evening")


def test_ask_text_keeps_a_body_of_exactly_300_characters() -> None:
    line = " Reply yes or no."
    noticed = "x" * (300 - len(line) - 1) + "."  # the body is then exactly 300

    _, body = ask_text(
        "home.light_turn_on", {}, noticed=noticed, reason="", names=["Lamp"], script=False
    )

    assert len(body) == 300
    assert "…" not in body
    _, longer = ask_text(
        "home.light_turn_on", {}, noticed=noticed + "x", reason="", names=["Lamp"], script=False
    )
    assert len(longer) == 300
    assert "…" in longer
    assert longer.endswith("Reply yes or no.")
