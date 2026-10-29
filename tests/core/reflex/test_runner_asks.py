"""The runner asks: a standing proposal becomes an ask, or is held for a named reason.

Both origins (a state change and a fired trigger) take the same step between the engine and the
record. The tool names and ids are test data (a generic ``home`` service with a Lamp in the Den,
and a made-up ``media`` one); the code under test names no tool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, create_autospec
from zoneinfo import ZoneInfo

import fakeredis
import pytest

from bus.schemas.events import (
    ActionRequest,
    ReflexOutcome,
    ReflexProposal,
    StateChangedEvent,
    TriggerFired,
)
from core.notifications.dnd import DNDChecker
from core.notifications.publisher import NotificationPublisher
from core.notifications.schema import Urgency
from core.reflex import __main__ as reflex_main
from core.reflex import asks, runner
from core.reflex.__main__ import _handle_trigger_fired
from core.reflex.answers import OpenAsk
from core.reflex.asks import asked_key, asks_key
from core.reflex.decision_log import DecisionLog
from core.reflex.prompt import LiveEntity
from core.reflex.runner import already_decided, decisions_key, process_stream_entry
from core.reflex.settings import ReflexSettings, save_settings
from core.reflex.tests.fakes import decided
from core.routing.pending import read_pending
from shared.streams import (
    DND_STATE_KEY,
    PENDING_PREFIX,
    REFLEX_ASK_OPEN_KEY,
    REFLEX_OBSERVATIONS_STREAM,
    TOOL_REGISTRY_KEY,
    USER_TIMEZONE_KEY,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from bus.schemas.events import HeldReason
    from core.reflex.outcome import Decided

NEW_YORK = "America/New_York"
LAMP = "light.lamp"
TOOL = "home.light_turn_on"
MEDIA_TOOL = "media.play_something"
ENTRY_ID = b"1760000000000-0"


def at(hour: int, minute: int = 0) -> datetime:
    """An aware moment on a day of October 2026 in New York."""
    return datetime(2026, 10, 28, hour, minute, tzinfo=ZoneInfo(NEW_YORK))


NOON = at(12)


def lamp(entity_id: str = LAMP, name: str | None = "Lamp", area: str | None = "Den") -> LiveEntity:
    attributes: dict[str, Any] = {}
    if name is not None:
        attributes["friendly_name"] = name
    if area is not None:
        attributes["area"] = area
    return LiveEntity(
        entity_id=entity_id,
        domain="light",
        controllable=True,
        state="off",
        attributes=attributes,
    )


SNAPSHOT: Mapping[str, LiveEntity] = {LAMP: lamp()}


def state_change() -> StateChangedEvent:
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id=LAMP,
        old_state="on",
        new_state="off",
    )


def trigger() -> TriggerFired:
    return TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")


def proposal(
    decision: str = "act",
    *,
    tool: str = TOOL,
    service: str = "home-service",
    parameters: dict[str, Any] | None = None,
    request_id: str = "ask-1",
) -> ReflexProposal:
    return ReflexProposal.model_validate(
        {
            "decision": decision,
            "reason": "It is getting dark.",
            "action": ActionRequest(
                source="reflex-engine",
                request_id=request_id,
                target_service=service,
                tool_name=tool,
                parameters={"target": "Lamp"} if parameters is None else parameters,
                reason="It is getting dark.",
            ).model_dump(),
        }
    )


class FakeEngine:
    """An engine that decides the same way each time it is asked."""

    def __init__(self, answer: Decided) -> None:
        self.answer = answer
        self.asked = 0

    async def process_event(self, _event: StateChangedEvent) -> Decided:
        self.asked += 1
        return self.answer

    async def process_trigger_fired(self, _event: TriggerFired) -> Decided:
        self.asked += 1
        return self.answer


async def register(redis: fakeredis.FakeAsyncRedis, risk: str | None = "benign") -> None:
    """The home service declares ``TOOL`` with *risk* (undeclared when ``None``)."""
    tools = [] if risk is None else [{"name": TOOL, "risk": risk}]
    manifest = {"features": [{"tools": tools}]}
    await redis.hset(TOOL_REGISTRY_KEY, "home-service", json.dumps(manifest))


async def configure(redis: fakeredis.FakeAsyncRedis, **changes: Any) -> None:
    """Ask mode, the home tools asked about, no quiet hours unless a test sets them."""
    fields: dict[str, Any] = {
        "mode": "ask",
        "ask_tools": ["home.*"],
        "quiet_hours_start": "00:00",
        "quiet_hours_end": "00:00",
    }
    await save_settings(redis, ReflexSettings(**{**fields, **changes}))


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


@pytest.fixture
async def asking(redis: fakeredis.FakeAsyncRedis) -> fakeredis.FakeAsyncRedis:
    """A Reflex in ask mode, in New York, whose home tool is benign."""
    await redis.set(USER_TIMEZONE_KEY, NEW_YORK)
    await register(redis)
    await configure(redis)
    return redis


@dataclass
class Rig:
    """One Reflex process's worth of wiring, and a way to deliver each origin's entry to it."""

    redis: fakeredis.FakeAsyncRedis
    notifier: Any
    dnd: DNDChecker
    engine: FakeEngine
    log: DecisionLog
    now: datetime = NOON
    event: StateChangedEvent = field(default_factory=state_change)  # one event, redelivered
    fired: TriggerFired = field(default_factory=trigger)

    def clock(self) -> datetime:
        return self.now

    async def state_change(self) -> bool:
        return await process_stream_entry(
            entry_id=ENTRY_ID,
            entry_data={b"event": self.event.model_dump_json().encode()},
            engine=self.engine,  # type: ignore[arg-type]
            agent=AsyncMock(),
            redis=self.redis,
            result_stream="alfred:home:action_results",
            observation_stream=REFLEX_OBSERVATIONS_STREAM,
            decision_log=self.log,
            notifier=self.notifier,
            dnd=self.dnd,
            clock=self.clock,
        )

    async def fired_trigger(self) -> None:
        await _handle_trigger_fired(
            {b"event": self.fired.model_dump_json().encode()},
            self.engine,  # type: ignore[arg-type]
            AsyncMock(),
            self.redis,
            self.notifier,
            decision_log=self.log,
            dnd=self.dnd,
            clock=self.clock,
        )

    async def records(self) -> list[Any]:
        return await self.log.read(since=datetime.now(UTC) - timedelta(hours=1))

    async def outcomes(self) -> list[ReflexOutcome | None]:
        return [record.outcome for record in await self.records()]

    def asks_sent(self) -> list[Any]:
        """The calls that were asks: a fired trigger's own notification is not."""
        return [c for c in self.notifier.publish.await_args_list if c.kwargs["source"] == ASK]

    async def counted(self) -> dict[str, int]:
        counts = await self.redis.hgetall(decisions_key(datetime.now(UTC).date()))
        return {k.decode(): int(v) for k, v in counts.items()}

    async def pending(self) -> list[bytes]:
        return await self.redis.keys(f"{PENDING_PREFIX}*")

    async def marked(self) -> bool:
        """Whether either origin's event has been marked decided."""
        return await already_decided(self.redis, self.event.event_id) or await already_decided(
            self.redis, self.fired.event_id
        )


ASK = "reflex-engine"


def rig_for(redis: fakeredis.FakeAsyncRedis, answer: Decided | None = None) -> Rig:
    return Rig(
        redis=redis,
        notifier=create_autospec(NotificationPublisher, instance=True),
        dnd=DNDChecker(redis, None),
        engine=FakeEngine(answer or decided(proposal(), snapshot=SNAPSHOT)),
        log=DecisionLog(redis),
    )


@pytest.fixture
def rig(asking: fakeredis.FakeAsyncRedis) -> Rig:
    return rig_for(asking)


# Each origin, delivered the same way: the state change's entry and the fired trigger's.
@dataclass(frozen=True)
class Origin:
    name: str
    deliver: Callable[[Rig], Any]
    record: tuple[Any, str]  # the module and the name of the record_decision it calls
    noticed: str


ORIGINS = {
    "state_change": Origin(
        "state_change",
        lambda r: r.state_change(),
        (runner, "record_decision"),
        "Lamp (Den): on → off",
    ),
    "trigger_fired": Origin(
        "trigger_fired", lambda r: r.fired_trigger(), (reflex_main, "record_decision"), "bedtime"
    ),
}


@pytest.fixture(params=sorted(ORIGINS))
def origin(request: pytest.FixtureRequest) -> Origin:
    return ORIGINS[request.param]


async def turn_dnd_on(redis: fakeredis.FakeAsyncRedis) -> None:
    await redis.set(DND_STATE_KEY, json.dumps({"active": True, "source": "manual"}))


# --- asking -------------------------------------------------------------------------------------


@pytest.mark.parametrize("decision", ["act", "ask"])
async def test_a_standing_proposal_in_ask_mode_is_asked(
    rig: Rig, origin: Origin, decision: str
) -> None:
    rig.engine.answer = decided(proposal(decision), snapshot=SNAPSHOT)

    await origin.deliver(rig)

    pending = await read_pending(rig.redis, "ask-1")
    assert pending is not None
    [parked] = pending.action_requests()
    assert parked.source == "reflex-engine"
    assert parked.tool_name == TOOL
    assert len(await rig.pending()) == 1
    assert 595 <= await rig.redis.ttl(f"{PENDING_PREFIX}ask-1") <= 600
    [sent] = rig.asks_sent()
    assert sent.args == (
        "Shall I turn on Lamp?",
        f"{origin.noticed}. It is getting dark. Reply yes or no.",
    )
    assert sent.kwargs["urgency"] is Urgency.IMPORTANT
    assert sent.kwargs["metadata"]["pending_action_id"] == "ask-1"
    assert await rig.outcomes() == [ReflexOutcome(kind="asked", ask_id="ask-1")]
    counted = await rig.counted()
    assert counted["outcome:asked"] == 1
    assert counted[decision] == 1  # the model's own answer is still counted as it said it
    assert await rig.marked()


async def test_the_ask_is_the_open_one_and_carries_what_was_noticed(
    rig: Rig, origin: Origin
) -> None:
    await origin.deliver(rig)

    stored = await rig.redis.get(REFLEX_ASK_OPEN_KEY)
    assert stored is not None
    ask = OpenAsk.model_validate_json(stored)
    assert (ask.ask_id, ask.tool_name, ask.targets) == ("ask-1", TOOL, [LAMP])
    assert ask.origin == origin.name
    assert ask.noticed == origin.noticed
    assert ask.script is False
    assert ask.asked_at == NOON
    assert ask.deadline == NOON + timedelta(seconds=600)
    assert await rig.redis.exists(asked_key(TOOL, [LAMP]))
    assert int(await rig.redis.get(asks_key(NOON.date())) or 0) == 1


async def test_the_proposal_observation_carries_the_outcome(rig: Rig) -> None:
    await rig.state_change()

    [(_, fields)] = await rig.redis.xrange(REFLEX_OBSERVATIONS_STREAM)
    observed = json.loads(fields[b"event"])
    assert observed["outcome"]["kind"] == "asked"
    assert observed["outcome"]["ask_id"] == "ask-1"


# --- held ---------------------------------------------------------------------------------------


async def _tool_off(r: Rig) -> None:
    await configure(r.redis, ask_tools=[])


async def _quiet_hours(r: Rig) -> None:
    # 12:00 in New York is 16:00 UTC: only the owner's own zone puts it in this window.
    await configure(r.redis, quiet_hours_start="11:30", quiet_hours_end="12:30")


async def _dnd(r: Rig) -> None:
    await turn_dnd_on(r.redis)


async def _ask_open(r: Rig) -> None:
    other = OpenAsk(
        ask_id="open-1",
        tool_name=TOOL,
        targets=["light.other"],
        origin="state_change",
        asked_at=NOON,
        deadline=NOON + timedelta(seconds=600),
        noticed="Other turned off",
        script=False,
    )
    await r.redis.set(REFLEX_ASK_OPEN_KEY, other.model_dump_json())


async def _asked_recently(r: Rig) -> None:
    await r.redis.set(asked_key(TOOL, [LAMP]), "1")


async def _daily_cap(r: Rig) -> None:
    await configure(r.redis, daily_ask_cap=0)


HELD: dict[HeldReason, Callable[[Rig], Any]] = {
    "tool_off": _tool_off,
    "quiet_hours": _quiet_hours,
    "dnd": _dnd,
    "ask_open": _ask_open,
    "asked_recently": _asked_recently,
    "daily_cap": _daily_cap,
}


@pytest.mark.parametrize("reason", sorted(HELD))
async def test_each_limit_is_recorded_as_held_and_counted_under_its_reason(
    rig: Rig, origin: Origin, reason: HeldReason
) -> None:
    await HELD[reason](rig)

    await origin.deliver(rig)

    assert await rig.outcomes() == [ReflexOutcome(kind="held", reason=reason)]
    counted = await rig.counted()
    assert counted["outcome:held"] == 1
    assert counted[f"held:{reason}"] == 1
    assert rig.asks_sent() == []
    assert await rig.pending() == []


async def test_a_held_proposal_opens_nothing(rig: Rig) -> None:
    await _dnd(rig)

    await rig.state_change()

    assert not await rig.redis.exists(REFLEX_ASK_OPEN_KEY)
    assert not await rig.redis.exists(asked_key(TOOL, [LAMP]))
    assert not await rig.redis.exists(asks_key(NOON.date()))


async def test_the_second_ask_in_a_row_is_held_for_the_first(rig: Rig) -> None:
    await rig.state_change()
    rig.engine.answer = decided(proposal(request_id="ask-2", tool=TOOL), snapshot=SNAPSHOT)

    await rig.fired_trigger()

    assert await rig.outcomes() == [
        ReflexOutcome(kind="asked", ask_id="ask-1"),
        ReflexOutcome(kind="held", reason="ask_open"),
    ]
    assert len(rig.asks_sent()) == 1


async def test_an_ask_that_lost_the_claim_is_held_for_the_ask_that_won_it(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate passed, and then another ask took the open key before this one could."""
    gate = asks.gate

    async def gate_then_lose_the_race(*args: Any, **kwargs: Any) -> ReflexOutcome:
        outcome = await gate(*args, **kwargs)
        await _ask_open(rig)
        return outcome

    monkeypatch.setattr(runner, "gate", gate_then_lose_the_race)

    await rig.state_change()

    assert await rig.outcomes() == [ReflexOutcome(kind="held", reason="ask_open")]
    assert (await rig.counted())["held:ask_open"] == 1
    assert rig.asks_sent() == []
    assert await rig.pending() == []


# --- shadow mode and everything but a standing proposal ----------------------------------------


async def test_shadow_mode_parks_nothing_and_the_outcome_is_shadow(
    rig: Rig, origin: Origin
) -> None:
    await configure(rig.redis, mode="shadow")

    await origin.deliver(rig)

    assert await rig.outcomes() == [ReflexOutcome(kind="shadow")]
    assert rig.asks_sent() == []
    assert await rig.pending() == []
    assert not await rig.redis.exists(REFLEX_ASK_OPEN_KEY)
    assert (await rig.counted())["outcome:shadow"] == 1


async def test_without_settings_the_default_mode_is_shadow(rig: Rig, origin: Origin) -> None:
    await rig.redis.delete("alfred:reflex:settings")

    await origin.deliver(rig)

    assert await rig.outcomes() == [ReflexOutcome(kind="shadow", reason="tool_off")]
    assert rig.asks_sent() == []
    assert (await rig.counted())["shadow:tool_off"] == 1


async def test_shadow_mode_still_says_why_an_ask_would_not_have_gone_out(rig: Rig) -> None:
    await configure(rig.redis, mode="shadow")
    await turn_dnd_on(rig.redis)

    await rig.state_change()

    assert await rig.outcomes() == [ReflexOutcome(kind="shadow", reason="dnd")]
    assert (await rig.counted())["shadow:dnd"] == 1


@pytest.mark.parametrize(
    "outcome",
    [
        ReflexOutcome(kind="none"),
        ReflexOutcome(kind="invalid"),
        ReflexOutcome(kind="would_reverse", reversed=[LAMP]),
    ],
    ids=lambda outcome: outcome.kind,
)
async def test_an_outcome_that_is_not_shadow_is_never_asked_about(
    rig: Rig, origin: Origin, outcome: ReflexOutcome, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing is resolved or read for it: not the settings, not the risk, not the clock."""
    boom = AsyncMock(side_effect=AssertionError("read for an outcome that is not shadow"))
    for name in ("load_settings", "tool_risk", "get_user_timezone", "gate", "open_ask"):
        monkeypatch.setattr(runner, name, boom)
    rig.engine.answer = decided(proposal(), outcome=outcome, snapshot=SNAPSHOT)

    await origin.deliver(rig)

    assert await rig.outcomes() == [outcome]
    assert rig.asks_sent() == []
    assert await rig.pending() == []
    assert not await rig.redis.exists(REFLEX_ASK_OPEN_KEY)
    assert (await rig.counted())[f"outcome:{outcome.kind}"] == 1


@pytest.mark.parametrize("wired", ["no notifier", "no dnd", "neither"])
async def test_without_a_notifier_and_a_dnd_checker_a_shadow_outcome_stays_shadow(
    rig: Rig, wired: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old behaviour, for callers that wire no asks: nothing is read, nothing is asked."""
    boom = AsyncMock(side_effect=AssertionError("read without the wiring for asks"))
    for name in ("load_settings", "tool_risk", "get_user_timezone", "gate", "open_ask"):
        monkeypatch.setattr(runner, name, boom)
    notifier = None if wired in ("no notifier", "neither") else rig.notifier
    dnd = None if wired in ("no dnd", "neither") else rig.dnd

    await process_stream_entry(
        entry_id=ENTRY_ID,
        entry_data={b"event": rig.event.model_dump_json().encode()},
        engine=rig.engine,  # type: ignore[arg-type]
        agent=AsyncMock(),
        redis=rig.redis,
        result_stream="alfred:home:action_results",
        observation_stream=REFLEX_OBSERVATIONS_STREAM,
        decision_log=rig.log,
        notifier=notifier,
        dnd=dnd,
    )

    assert await rig.outcomes() == [ReflexOutcome(kind="shadow")]
    assert await rig.pending() == []
    rig.notifier.publish.assert_not_awaited()


async def test_a_trigger_handled_without_a_dnd_checker_stays_shadow(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    boom = AsyncMock(side_effect=AssertionError("read without the wiring for asks"))
    for name in ("load_settings", "tool_risk", "get_user_timezone", "gate", "open_ask"):
        monkeypatch.setattr(runner, name, boom)

    await _handle_trigger_fired(
        {b"event": rig.fired.model_dump_json().encode()},
        rig.engine,  # type: ignore[arg-type]
        AsyncMock(),
        rig.redis,
        rig.notifier,
        decision_log=rig.log,
    )

    assert await rig.outcomes() == [ReflexOutcome(kind="shadow")]
    assert rig.asks_sent() == []


# --- what is asked about, and how it is worded --------------------------------------------------


@pytest.mark.parametrize("risk", ["critical", "unknown", None, "surprising"])
async def test_a_tool_the_router_would_refuse_even_confirmed_is_never_asked(
    rig: Rig, risk: str | None
) -> None:
    await register(rig.redis, risk)

    await rig.state_change()

    assert await rig.outcomes() == [ReflexOutcome(kind="held", reason="tool_off")]
    assert rig.asks_sent() == []


@pytest.mark.parametrize("risk", ["benign", "elevated"])
async def test_a_benign_or_elevated_tool_is_asked(rig: Rig, risk: str) -> None:
    await register(rig.redis, risk)

    await rig.state_change()

    [outcome] = await rig.outcomes()
    assert outcome is not None
    assert outcome.kind == "asked"


async def test_an_elevated_tool_asks_to_run_a_script(rig: Rig) -> None:
    await register(rig.redis, "elevated")

    await rig.state_change()

    [sent] = rig.asks_sent()
    assert sent.args[0] == "Shall I run the script Lamp?"
    stored = await rig.redis.get(REFLEX_ASK_OPEN_KEY)
    assert stored is not None
    assert OpenAsk.model_validate_json(stored).script is True


async def test_a_benign_tool_does_not_ask_to_run_a_script(rig: Rig) -> None:
    await rig.state_change()

    stored = await rig.redis.get(REFLEX_ASK_OPEN_KEY)
    assert stored is not None
    assert OpenAsk.model_validate_json(stored).script is False


async def test_a_target_naming_a_room_asks_about_every_entity_in_it(rig: Rig) -> None:
    snapshot = {
        LAMP: lamp(),
        "light.reading": lamp("light.reading", "Reading light"),
        "light.hall": lamp("light.hall", "Hall light", area="Hall"),
    }
    rig.engine.answer = decided(proposal(parameters={"target": "Den"}), snapshot=snapshot)

    await rig.state_change()

    [sent] = rig.asks_sent()
    assert sent.args[0] == "Shall I turn on Lamp and Reading light?"
    stored = await rig.redis.get(REFLEX_ASK_OPEN_KEY)
    assert stored is not None
    assert OpenAsk.model_validate_json(stored).targets == [LAMP, "light.reading"]


async def test_an_entity_with_no_friendly_name_is_named_by_its_id(rig: Rig) -> None:
    snapshot = {LAMP: lamp(name=None)}
    rig.engine.answer = decided(proposal(parameters={"target": LAMP}), snapshot=snapshot)

    await rig.state_change()

    assert rig.asks_sent()[0].args[0] == "Shall I turn on light.lamp?"


async def test_a_tool_of_another_service_asks_about_the_target_as_written(rig: Rig) -> None:
    """Nothing in the snapshot names that service's targets: the target stands for itself."""
    await rig.redis.hset(
        TOOL_REGISTRY_KEY,
        "media-service",
        json.dumps({"features": [{"tools": [{"name": MEDIA_TOOL, "risk": "benign"}]}]}),
    )
    await configure(rig.redis, ask_tools=["media.*"])
    rig.engine.answer = decided(
        proposal(tool=MEDIA_TOOL, service="media-service", parameters={"target": "Den"}),
        snapshot=SNAPSHOT,
    )

    await rig.state_change()

    [sent] = rig.asks_sent()
    assert sent.args[0] == "Shall I play something Den?"
    stored = await rig.redis.get(REFLEX_ASK_OPEN_KEY)
    assert stored is not None
    assert OpenAsk.model_validate_json(stored).targets == ["Den"]
    assert await rig.redis.exists(asked_key(MEDIA_TOOL, ["Den"]))


@pytest.mark.parametrize("parameters", [{}, {"target": ""}, {"target": 3}, {"target": ["Den"]}])
async def test_a_proposal_with_no_usable_target_asks_about_none(
    rig: Rig, parameters: dict[str, Any]
) -> None:
    await rig.redis.hset(
        TOOL_REGISTRY_KEY,
        "media-service",
        json.dumps({"features": [{"tools": [{"name": MEDIA_TOOL, "risk": "benign"}]}]}),
    )
    await configure(rig.redis, ask_tools=["media.*"])
    rig.engine.answer = decided(
        proposal(tool=MEDIA_TOOL, service="media-service", parameters=parameters),
        snapshot=SNAPSHOT,
    )

    await rig.state_change()

    [sent] = rig.asks_sent()
    assert sent.args[0] == "Shall I play something?"
    stored = await rig.redis.get(REFLEX_ASK_OPEN_KEY)
    assert stored is not None
    assert OpenAsk.model_validate_json(stored).targets == []


async def test_a_proposal_decided_without_live_state_asks_about_its_target_as_written(
    rig: Rig,
) -> None:
    rig.engine.answer = decided(proposal())

    await rig.state_change()

    [sent] = rig.asks_sent()
    assert sent.args[0] == "Shall I light turn on Lamp?"
    stored = await rig.redis.get(REFLEX_ASK_OPEN_KEY)
    assert stored is not None
    assert OpenAsk.model_validate_json(stored).targets == ["Lamp"]


# --- failures and redelivery --------------------------------------------------------------------


@pytest.mark.parametrize("step", ["load_settings", "tool_risk", "get_user_timezone", "gate"])
async def test_a_failure_before_the_ask_leaves_the_event_pending(
    rig: Rig, origin: Origin, step: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed, as with the person changes: nothing is recorded, marked or sent."""
    monkeypatch.setattr(runner, step, AsyncMock(side_effect=ConnectionError("redis is down")))

    with pytest.raises(ConnectionError):
        await origin.deliver(rig)

    assert await rig.records() == []
    assert await rig.counted() == {}
    assert rig.asks_sent() == []
    assert not await rig.marked()


def raising_once(real: Callable[..., Any], *, after: bool) -> Callable[..., Any]:
    """*real*, wrapped to raise on its first call: before it runs, or *after* it has."""
    calls = 0

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1 and not after:
            raise RuntimeError("a crash")
        result = await real(*args, **kwargs)
        if calls == 1:
            raise RuntimeError("a crash")
        return result

    return wrapper


async def test_a_crash_just_after_parking_sends_no_notification_and_asks_once(
    rig: Rig, origin: Origin, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``open_ask`` notifies last, so a crash in between sends nothing: at most one in all."""
    monkeypatch.setattr(
        asks, "store_pending_action", raising_once(asks.store_pending_action, after=True)
    )

    with pytest.raises(RuntimeError, match="a crash"):
        await origin.deliver(rig)

    assert len(await rig.pending()) == 1
    assert rig.asks_sent() == []
    assert await rig.records() == []

    await origin.deliver(rig)  # the redelivered entry

    assert await rig.outcomes() == [ReflexOutcome(kind="held", reason="ask_open")]
    assert (await rig.counted())["held:ask_open"] == 1
    assert rig.asks_sent() == []
    assert len(await rig.pending()) == 1  # still the one action, never a second


async def test_a_crash_after_the_ask_went_out_sends_one_notification_in_all(
    rig: Rig, origin: Origin, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, name = origin.record
    monkeypatch.setattr(module, name, raising_once(getattr(module, name), after=False))

    with pytest.raises(RuntimeError, match="a crash"):
        await origin.deliver(rig)

    assert len(rig.asks_sent()) == 1
    assert await rig.records() == []

    await origin.deliver(rig)  # the redelivered entry

    assert await rig.outcomes() == [ReflexOutcome(kind="held", reason="ask_open")]
    assert (await rig.counted())["held:ask_open"] == 1
    assert len(rig.asks_sent()) == 1
    assert len(await rig.pending()) == 1


async def test_a_redelivery_after_the_decision_was_marked_does_nothing(rig: Rig) -> None:
    await rig.state_change()

    await rig.state_change()

    assert rig.engine.asked == 1
    assert len(rig.asks_sent()) == 1
    assert len(await rig.outcomes()) == 1
