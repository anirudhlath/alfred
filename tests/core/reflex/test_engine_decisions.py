"""ReflexEngine — one event in, a ReflexProposal out (#285)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import fakeredis
import pytest
from redis import exceptions as redis_exceptions

from bus.schemas.events import ChangeContext, ReflexOutcome, StateChangedEvent, TriggerFired
from core.reflex.context_reader import LIVE_STATE_UNAVAILABLE
from core.reflex.engine import ReflexEngine
from core.reflex.reversal import PersonChanges
from core.reflex.settings import ReflexSettings, save_settings
from core.reflex.tool_registry import ToolInfo
from sdk.alfred_sdk.context import ContextEntry, ContextSnapshot
from tests.core.reflex.fakes import reflex_agent

if TYPE_CHECKING:
    from collections.abc import Mapping

    from shared.types import AioRedis
    from tests.llm_fakes import Reply, Script

# 03:30 UTC on Thu 8 Oct 2026 is 22:30 on Wed 7 Oct in Chicago (CDT, UTC-5).
NIGHT_UTC = datetime(2026, 10, 8, 3, 30, tzinfo=UTC)

TOOLS = [
    ToolInfo(
        name="home.light_turn_on",
        description="light.turn_on",
        feature_name="home",
        feature_description="",
        target_service="home-service",
        audience="reflex",
        input_schema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Available light entities: Lamp A, Lamp B",
                },
                "brightness_pct": {"type": "number"},
            },
            "required": ["target"],
        },
    ),
    ToolInfo(
        name="home.lock_unlock",
        description="lock.unlock",
        feature_name="home",
        feature_description="",
        target_service="home-service",
        audience="conscious",
        input_schema={
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
    ),
]

TV_PLAYS = StateChangedEvent(
    source="home-service",
    domain="home",
    entity_id="media_player.living_room_tv",
    old_state="paused",
    new_state="playing",
    attributes={"friendly_name": "Living Room TV", "media_title": "A Film"},
)

ACT = {
    "decision": "act",
    "reason": "Film at night",
    "tool_name": "home.light_turn_on",
    "target_service": "home-service",
    "parameters": {"target": "Living Room", "brightness_pct": 30},
}


def _snapshot() -> ContextSnapshot:
    return ContextSnapshot(
        controllable={
            "light": [
                ContextEntry(
                    entity_id="light.tv_lamp",
                    state="on",
                    attributes={
                        "friendly_name": "TV Lamp",
                        "area": "Living Room",
                        "brightness": 255,
                    },
                )
            ],
            "media_player": [
                ContextEntry(
                    entity_id="media_player.living_room_tv",
                    state="playing",
                    attributes={"friendly_name": "Living Room TV", "area": "Living Room"},
                )
            ],
            "person": [
                ContextEntry(
                    entity_id="person.a", state="home", attributes={"friendly_name": "Person A"}
                )
            ],
        },
        sensors={"sun": [ContextEntry(entity_id="sun.sun", state="below_horizon")]},
    )


def _engine(
    snapshot: ContextSnapshot | None = None,
    *replies: Reply,
    live: bool = True,
    redis: AioRedis | AsyncMock | None = None,
    person_changes: PersonChanges | AsyncMock | None = None,
    registry: AsyncMock | None = None,
) -> tuple[ReflexEngine, Script]:
    """An engine over a scripted model that answers with *replies*, in order."""
    if registry is None:
        registry = AsyncMock()
        registry.get_tools = AsyncMock(return_value=TOOLS)
    reader = None
    if live:
        reader = AsyncMock()
        reader.get_snapshot = AsyncMock(return_value=snapshot)
        reader.get_user_timezone = AsyncMock(return_value="America/Chicago")
    agent, script = reflex_agent(*replies)
    engine = ReflexEngine(
        preferences_dir="/unused",
        tool_registry=registry,
        context_reader=reader,
        agent=agent,
        clock=lambda: NIGHT_UTC,
        redis=redis,  # type: ignore[arg-type]
        person_changes=person_changes,  # type: ignore[arg-type]
    )
    engine._cached_preferences = "- Prefers dim light for films"
    return engine, script


def _reply(payload: Mapping[str, object] | str) -> str:
    return payload if isinstance(payload, str) else json.dumps(payload)


NONE = _reply({"decision": "none"})


async def test_an_act_reply_becomes_a_proposal_with_its_action() -> None:
    engine, _ = _engine(_snapshot(), _reply(ACT))

    decided = await engine.process_event(TV_PLAYS)

    proposal = decided.proposal
    assert proposal.decision == "act"
    assert proposal.action is not None
    assert proposal.action.tool_name == "home.light_turn_on"
    assert proposal.action.reason == "Film at night"
    assert decided.outcome == ReflexOutcome(kind="shadow")
    assert decided.recent == {}


async def test_the_prompt_shows_now_the_house_and_the_change() -> None:
    engine, script = _engine(_snapshot(), NONE)

    await engine.process_event(TV_PLAYS)

    prompt = script.prompt()
    assert "## Preferences\n- Prefers dim light for films" in prompt
    assert "## Now\nWed 7 Oct, 22:30 (night) · sun down\nPeople: Person A home" in prompt
    assert "## House\nLiving Room: Living Room TV playing · TV Lamp on 100%" in prompt
    assert '## What changed\nLiving Room TV (Living Room): paused → playing · "A Film"' in prompt


async def test_only_reflex_tools_reach_the_prompt_without_their_entity_lists() -> None:
    engine, script = _engine(_snapshot(), NONE)

    await engine.process_event(TV_PLAYS)

    prompt = script.prompt()
    assert "- home.light_turn_on(target, brightness_pct) [home-service]: light.turn_on" in prompt
    assert "home.lock_unlock" not in prompt
    assert "Available light entities" not in prompt


async def test_a_proposal_naming_a_conscious_tool_is_invalid() -> None:
    engine, _ = _engine(_snapshot(), _reply({**ACT, "tool_name": "home.lock_unlock"}))

    decided = await engine.process_event(TV_PLAYS)

    assert decided.proposal.decision == "invalid"
    assert decided.outcome == ReflexOutcome(kind="invalid")


async def test_no_live_state_still_decides() -> None:
    engine, script = _engine(None, NONE)

    decided = await engine.process_event(TV_PLAYS)

    prompt = script.prompt()
    assert f"## Now\nWed 7 Oct, 22:30 (night)\n\n## House\n{LIVE_STATE_UNAVAILABLE}" in prompt
    assert decided.proposal.decision == "none"
    assert decided.outcome == ReflexOutcome(kind="none")


async def test_without_a_context_reader_the_clock_is_utc() -> None:
    engine, script = _engine(None, NONE, live=False)

    await engine.process_event(TV_PLAYS)

    assert "## Now\nThu 8 Oct, 03:30 (night)" in script.prompt()


async def test_tools_are_cached_between_events_until_reloaded() -> None:
    registry = AsyncMock()
    registry.get_tools = AsyncMock(return_value=TOOLS)
    engine, _ = _engine(_snapshot(), NONE, NONE, NONE, registry=registry)

    await engine.process_event(TV_PLAYS)
    await engine.process_event(TV_PLAYS)
    assert registry.get_tools.await_count == 1
    await engine.reload_tools()
    await engine.process_event(TV_PLAYS)

    assert registry.get_tools.await_count == 2


async def test_a_model_failure_propagates() -> None:
    engine, _ = _engine(_snapshot(), ConnectionError("down"))

    with pytest.raises(ConnectionError):
        await engine.process_event(TV_PLAYS)


async def test_an_unparseable_reply_is_an_invalid_proposal() -> None:
    engine, script = _engine(_snapshot(), "Sure! Dimming.")

    decided = await engine.process_event(TV_PLAYS)

    assert decided.proposal.decision == "invalid"
    assert decided.proposal.raw == "Sure! Dimming."
    assert script.call_count == 1


async def test_a_trigger_gets_now_and_the_house_and_names_itself() -> None:
    engine, script = _engine(_snapshot(), NONE)
    trigger = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")

    decided = await engine.process_trigger_fired(trigger)

    prompt = script.prompt()
    assert "already being notified" in prompt
    assert "## Trigger fired\nbedtime (time)" in prompt
    assert "## House\nLiving Room:" in prompt
    assert "## What changed" not in prompt
    assert decided.proposal.decision == "none"


async def test_the_decision_carries_the_prompt_the_model_saw() -> None:
    engine, script = _engine(_snapshot(), _reply(ACT), _reply(ACT))

    from_event = await engine.process_event(TV_PLAYS)
    trigger = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")
    from_trigger = await engine.process_trigger_fired(trigger)

    assert from_event.prompt == script.prompt(0)
    assert from_trigger.prompt == script.prompt(1)


# --- what a person just changed ---------------------------------------------------------------

LAMP = "light.tv_lamp"
TV = "media_player.living_room_tv"


def _person_turned_lamp_off(*, ago: timedelta = timedelta(minutes=5)) -> StateChangedEvent:
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id=LAMP,
        old_state="on",
        new_state="off",
        context=ChangeContext(id="ctx-1", user_id="user-1"),
        timestamp=NIGHT_UTC - ago,
    )


def _person_paused_tv() -> StateChangedEvent:
    return TV_PLAYS.model_copy(
        update={
            "old_state": "playing",
            "new_state": "paused",
            "context": ChangeContext(id="ctx-2", user_id="user-1"),
            "timestamp": NIGHT_UTC - timedelta(minutes=5),
        }
    )


def _with_changes(
    *replies: Reply, snapshot: ContextSnapshot | None = None, live: bool = True
) -> tuple[ReflexEngine, PersonChanges, fakeredis.FakeAsyncRedis, Script]:
    redis = fakeredis.FakeAsyncRedis()
    changes = PersonChanges(redis, clock=lambda: NIGHT_UTC)
    engine, script = _engine(snapshot, *replies, live=live, redis=redis, person_changes=changes)
    return engine, changes, redis, script


async def test_the_prompt_lists_what_to_leave_alone() -> None:
    engine, changes, _, script = _with_changes(NONE, NONE, snapshot=_snapshot())

    await engine.process_event(TV_PLAYS)
    await changes.note(_person_turned_lamp_off(), "person")
    decided = await engine.process_event(TV_PLAYS)

    first, second = script.prompt(0), script.prompt(1)
    assert "Leave alone" not in first
    assert (
        "People: Person A home\nLeave alone, a person just changed: TV Lamp (Living Room)" in second
    )
    assert list(decided.recent) == [LAMP]
    assert decided.recent[LAMP].new_state == "off"


async def test_the_trigger_prompt_lists_what_to_leave_alone_too() -> None:
    engine, changes, _, script = _with_changes(NONE, snapshot=_snapshot())
    await changes.note(_person_turned_lamp_off(), "person")
    trigger = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")

    decided = await engine.process_trigger_fired(trigger)

    assert "Leave alone, a person just changed: TV Lamp (Living Room)" in script.prompt()
    assert list(decided.recent) == [LAMP]


async def test_a_proposal_that_reverses_a_persons_change_is_would_reverse() -> None:
    proposal = {**ACT, "parameters": {"target": LAMP}}
    engine, changes, _, _ = _with_changes(_reply(proposal), snapshot=_snapshot())
    await changes.note(_person_turned_lamp_off(), "person")

    decided = await engine.process_event(TV_PLAYS)

    assert decided.proposal.decision == "act"  # what the model said is kept
    assert decided.outcome == ReflexOutcome(kind="would_reverse", reversed=[LAMP])


async def test_a_trigger_proposal_that_reverses_a_persons_change_is_would_reverse() -> None:
    proposal = {**ACT, "parameters": {"target": LAMP}}
    engine, changes, _, _ = _with_changes(_reply(proposal), snapshot=_snapshot())
    await changes.note(_person_turned_lamp_off(), "person")
    trigger = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")

    decided = await engine.process_trigger_fired(trigger)

    assert decided.outcome == ReflexOutcome(kind="would_reverse", reversed=[LAMP])


async def test_a_proposal_for_something_nobody_changed_is_a_shadow() -> None:
    engine, changes, _, _ = _with_changes(_reply(ACT), snapshot=_snapshot())
    # ACT turns on the Living Room's lights; a person only paused the TV there.
    await changes.note(_person_paused_tv(), "person")

    decided = await engine.process_event(TV_PLAYS)

    assert decided.outcome == ReflexOutcome(kind="shadow")
    assert list(decided.recent) == [TV]  # still kept: the record shows what Reflex knew


async def test_the_window_is_read_from_the_settings_for_every_decision() -> None:
    engine, changes, redis, script = _with_changes(NONE, NONE, NONE, snapshot=_snapshot())
    await changes.note(_person_turned_lamp_off(ago=timedelta(minutes=30)), "person")

    default = await engine.process_event(TV_PLAYS)  # 60 minutes
    await save_settings(redis, ReflexSettings(reverse_window_minutes=15))
    narrowed = await engine.process_event(TV_PLAYS)
    await save_settings(redis, ReflexSettings(reverse_window_minutes=45))
    # The narrow window pruned the record; a person has to change the lamp again.
    assert narrowed.recent == {}
    await changes.note(_person_turned_lamp_off(ago=timedelta(minutes=30)), "person")
    widened = await engine.process_event(TV_PLAYS)

    assert list(default.recent) == [LAMP]
    assert list(widened.recent) == [LAMP]
    assert "Leave alone" in script.prompt(0)
    assert "Leave alone" not in script.prompt(1)
    assert "Leave alone" in script.prompt(2)


async def test_a_bad_settings_document_reads_as_the_default_window() -> None:
    engine, changes, redis, _ = _with_changes(NONE, snapshot=_snapshot())
    await redis.set("alfred:reflex:settings", b"{not json")
    await changes.note(_person_turned_lamp_off(ago=timedelta(minutes=30)), "person")

    decided = await engine.process_event(TV_PLAYS)

    assert list(decided.recent) == [LAMP]


async def test_with_no_live_state_nothing_is_reversed_and_the_entity_is_named_by_id() -> None:
    proposal = {**ACT, "parameters": {"target": LAMP}}
    engine, changes, _, script = _with_changes(_reply(proposal), snapshot=None)
    await changes.note(_person_turned_lamp_off(), "person")

    decided = await engine.process_event(TV_PLAYS)

    assert decided.outcome == ReflexOutcome(kind="shadow")
    assert list(decided.recent) == [LAMP]
    assert f"Leave alone, a person just changed: {LAMP}" in script.prompt()


async def test_an_entity_missing_from_the_snapshot_is_named_by_id() -> None:
    engine, changes, _, script = _with_changes(NONE, snapshot=_snapshot())
    gone = _person_turned_lamp_off().model_copy(update={"entity_id": "light.gone"})
    await changes.note(gone, "person")

    await engine.process_event(TV_PLAYS)

    assert "Leave alone, a person just changed: light.gone" in script.prompt()


async def test_without_redis_or_person_changes_there_are_no_changes() -> None:
    engine, script = _engine(_snapshot(), NONE)

    decided = await engine.process_event(TV_PLAYS)

    assert decided.recent == {}
    assert "Leave alone" not in script.prompt()


async def test_a_redis_error_reading_the_changes_propagates_before_the_model_is_asked() -> None:
    changes = AsyncMock()
    changes.recent = AsyncMock(side_effect=redis_exceptions.ConnectionError("down"))
    engine, script = _engine(_snapshot(), NONE, person_changes=changes)

    with pytest.raises(redis_exceptions.ConnectionError):
        await engine.process_event(TV_PLAYS)
    with pytest.raises(redis_exceptions.ConnectionError):
        await engine.process_trigger_fired(
            TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")
        )

    assert script.call_count == 0


async def test_a_redis_error_reading_the_settings_propagates() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=redis_exceptions.ConnectionError("down"))
    changes = AsyncMock()
    changes.recent = AsyncMock(return_value={})
    engine, script = _engine(_snapshot(), NONE, redis=redis, person_changes=changes)

    with pytest.raises(redis_exceptions.ConnectionError):
        await engine.process_event(TV_PLAYS)

    assert script.call_count == 0
    changes.recent.assert_not_awaited()
