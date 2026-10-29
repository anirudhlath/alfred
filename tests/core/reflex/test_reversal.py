"""Never reverse a person: the record of what they just changed, the hands-off rule that reads
it, and how a proposal's target is resolved.

Ids and names are the eval apartment world's (``evals/harness/worlds/apartment.yaml``). The tool
names are test data; the code under test names no tool.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from loguru import logger
from redis import exceptions as redis_exceptions

from bus.schemas.events import ActionRequest, ChangeContext, StateChangedEvent
from core.attribution.cause import cause_of
from core.reflex.prompt import LiveEntity
from core.reflex.reversal import (
    PersonChange,
    PersonChanges,
    overridden_entities,
    resolve_targets,
    resolved_action,
)
from shared.streams import REFLEX_PERSON_CHANGES_KEY

if TYPE_CHECKING:
    from collections.abc import Iterator

    from shared.types import AioRedis

_NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
_WINDOW = timedelta(minutes=60)

LAMP = "light.living_room_lamp"
CEILING = "light.living_room_ceiling"
BEDROOM_LAMP = "light.bedroom_lamp"
TV = "media_player.living_room_tv"
PENDANTS = "light.kitchen_pendants"
KITCHEN_STRIP = "light.kitchen_strip"  # its friendly name is its room's name

_PERSON = ChangeContext(id="ctx-person", user_id="user-1")
_OUTSIDE = ChangeContext(id="ctx-outside")
_AUTOMATION = ChangeContext(id="ctx-automation", parent_id="ctx-parent")
_ALFRED = ChangeContext(id="ctx-alfred", by_service=True)


def _entity(entity_id: str, name: str, area: str, state: str = "on") -> LiveEntity:
    return LiveEntity(
        entity_id=entity_id,
        domain=entity_id.split(".")[0],
        controllable=True,
        state=state,
        attributes={"friendly_name": name, "area": area},
    )


_SNAPSHOT = {
    e.entity_id: e
    for e in (
        _entity(LAMP, "Living Room Lamp", "Living Room"),
        _entity(CEILING, "Living Room Ceiling", "Living Room", "off"),
        _entity(BEDROOM_LAMP, "Bedroom Lamp", "Bedroom", "off"),
        _entity(TV, "Living Room TV", "Living Room", "off"),
    )
}


_KITCHEN = {
    e.entity_id: e
    for e in (
        _entity(PENDANTS, "Kitchen Pendants", "Kitchen", "off"),
        _entity(KITCHEN_STRIP, "Kitchen", "Kitchen"),
    )
}


def _event(
    entity_id: str,
    old_state: str | None,
    new_state: str,
    *,
    ago: timedelta = timedelta(minutes=10),
    context: ChangeContext | None = _PERSON,
) -> StateChangedEvent:
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id=entity_id,
        old_state=old_state,
        new_state=new_state,
        context=context,
        timestamp=_NOW - ago,
    )


def _action(tool_name: str, target: str, **parameters: object) -> ActionRequest:
    return ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name=tool_name,
        parameters={"target": target, **parameters},
    )


async def _noted(*events: StateChangedEvent) -> dict[str, PersonChange]:
    """What ``recent`` holds after ``note`` has seen the events in order."""
    changes = PersonChanges(fakeredis.FakeAsyncRedis(), clock=lambda: _NOW)
    for event in events:
        await changes.note(event, cause_of(event.context))
    return await changes.recent(_WINDOW)


@pytest.fixture
def warnings() -> Iterator[list[str]]:
    messages: list[str] = []
    sink_id = logger.add(messages.append, level="WARNING", format="{message}")
    yield messages
    logger.remove(sink_id)


# --- the rule: hands off what a person just changed ---------------------------------------------


async def test_turning_on_what_a_person_turned_off_reverses_it() -> None:
    changes = await _noted(_event(LAMP, "on", "off"))

    reversed_ = overridden_entities(_action("home.light_turn_on", LAMP), changes, _SNAPSHOT)

    assert reversed_ == [LAMP]


async def test_a_change_older_than_the_window_is_not_held() -> None:
    changes = await _noted(_event(LAMP, "on", "off", ago=timedelta(minutes=61)))

    assert overridden_entities(_action("home.light_turn_on", LAMP), changes, _SNAPSHOT) == []


async def test_dimming_what_a_person_just_turned_on_is_hands_off() -> None:
    changes = await _noted(_event(LAMP, "off", "on"))

    action = _action("home.light_turn_on", LAMP, brightness_pct=30)

    assert overridden_entities(action, changes, _SNAPSHOT) == [LAMP]


async def test_repeating_what_a_person_just_did_is_hands_off() -> None:
    """Turning on a light a person just turned on changes nothing, and is still not Reflex's to
    do: the entity is theirs for the window, whatever the action."""
    changes = await _noted(_event(LAMP, "off", "on"))

    assert overridden_entities(_action("home.light_turn_on", LAMP), changes, _SNAPSHOT) == [LAMP]


async def test_changing_a_brightness_a_person_just_set_reverses_it() -> None:
    changes = await _noted(_event(LAMP, "on", "on"))

    action = _action("home.light_turn_on", LAMP, brightness_pct=80)

    assert overridden_entities(action, changes, _SNAPSHOT) == [LAMP]


async def test_pausing_what_a_person_started_reverses_it() -> None:
    changes = await _noted(_event(TV, "off", "playing"))

    reversed_ = overridden_entities(
        _action("home.media_player_media_pause", TV), changes, _SNAPSHOT
    )

    assert reversed_ == [TV]


async def test_resuming_what_a_person_paused_reverses_it() -> None:
    changes = await _noted(_event(TV, "playing", "paused"))

    reversed_ = overridden_entities(_action("home.media_player_media_play", TV), changes, _SNAPSHOT)

    assert reversed_ == [TV]


@pytest.mark.parametrize("context", [_OUTSIDE, None], ids=["outside", "no-context"])
async def test_a_change_nobody_can_name_counts_as_a_persons(context: ChangeContext | None) -> None:
    changes = await _noted(_event(LAMP, "on", "off", context=context))

    assert overridden_entities(_action("home.light_turn_on", LAMP), changes, _SNAPSHOT) == [LAMP]


async def test_a_later_automation_change_clears_the_persons() -> None:
    changes = await _noted(
        _event(LAMP, "off", "on", ago=timedelta(minutes=10)),
        _event(LAMP, "on", "off", ago=timedelta(minutes=5), context=_AUTOMATION),
    )

    assert changes == {}
    assert overridden_entities(_action("home.light_turn_off", LAMP), changes, _SNAPSHOT) == []


async def test_a_later_alfred_change_clears_the_persons() -> None:
    changes = await _noted(
        _event(LAMP, "on", "off", ago=timedelta(minutes=10)),
        _event(LAMP, "off", "on", ago=timedelta(minutes=5), context=_ALFRED),
    )

    assert changes == {}


async def test_an_older_person_event_does_not_overwrite_a_newer_record() -> None:
    changes = await _noted(
        _event(LAMP, "off", "on", ago=timedelta(minutes=5)),
        _event(
            LAMP,
            "on",
            "off",
            ago=timedelta(minutes=20),
            context=ChangeContext(id="ctx-replayed", user_id="u"),
        ),
    )

    assert changes[LAMP].new_state == "on"
    assert changes[LAMP].time == _NOW - timedelta(minutes=5)


async def test_an_older_automation_event_does_not_clear_a_newer_record() -> None:
    changes = await _noted(
        _event(LAMP, "on", "off", ago=timedelta(minutes=5)),
        _event(LAMP, "off", "on", ago=timedelta(minutes=20), context=_AUTOMATION),
    )

    assert list(changes) == [LAMP]
    assert changes[LAMP].new_state == "off"


async def test_a_room_target_reverses_only_the_entities_a_person_changed() -> None:
    changes = await _noted(_event(LAMP, "on", "off"))

    reversed_ = overridden_entities(
        _action("home.light_turn_on", "Living room"), changes, _SNAPSHOT
    )

    assert reversed_ == [LAMP]


async def test_a_friendly_name_target_reaches_the_entity() -> None:
    changes = await _noted(_event(LAMP, "on", "off"))

    reversed_ = overridden_entities(
        _action("home.light_turn_on", "Living Room Lamp"), changes, _SNAPSHOT
    )

    assert reversed_ == [LAMP]


async def test_a_light_named_for_its_room_does_not_hide_the_rest_of_the_room() -> None:
    """home-service reads "Kitchen" as the area before the friendly name, so the call turns on
    the pendants too: a person's change to them is reversed."""
    changes = await _noted(_event(PENDANTS, "on", "off"))

    reversed_ = overridden_entities(_action("home.light_turn_on", "Kitchen"), changes, _KITCHEN)

    assert reversed_ == [PENDANTS]


@pytest.mark.parametrize(
    ("tool", "parameters"),
    [
        ("home.light_toggle", {}),
        ("home.light_toggle", {"brightness_pct": 30}),
        ("home.light_turn_off", {}),
    ],
    ids=["unknown-service", "unknown-service-with-brightness", "turn-off"],
)
@pytest.mark.parametrize(
    "change", [("on", "off"), ("on", "on"), ("off", "on")], ids=["off", "brightness", "on"]
)
async def test_any_action_on_what_a_person_just_changed_is_hands_off(
    tool: str, parameters: dict[str, object], change: tuple[str, str]
) -> None:
    changes = await _noted(_event(LAMP, *change))

    assert overridden_entities(_action(tool, LAMP, **parameters), changes, _SNAPSHOT) == [LAMP]


@pytest.mark.parametrize("change", [("playing", "playing"), ("off", "playing")])
async def test_setting_the_volume_of_what_a_person_just_changed_is_hands_off(
    change: tuple[str, str],
) -> None:
    changes = await _noted(_event(TV, *change))

    action = _action("home.media_player_volume_set", TV, volume_level=0.5)

    assert overridden_entities(action, changes, _SNAPSHOT) == [TV]


async def test_an_attribute_report_in_the_same_action_leaves_the_turn_on_as_it_was() -> None:
    changes = await _noted(
        _event(LAMP, "off", "on", ago=timedelta(minutes=10)),
        _event(LAMP, "on", "on", ago=timedelta(minutes=10)),  # the bulb's brightness report
    )

    assert changes[LAMP].attributes_only is False
    assert changes[LAMP].old_state == "off"


async def test_an_attribute_change_from_another_action_replaces_the_record() -> None:
    changes = await _noted(
        _event(LAMP, "off", "on", ago=timedelta(minutes=10)),
        _event(
            LAMP,
            "on",
            "on",
            ago=timedelta(minutes=5),
            context=ChangeContext(id="ctx-slider", user_id="user-1"),
        ),
    )

    action = _action("home.light_turn_on", LAMP, brightness_pct=30)

    assert changes[LAMP].attributes_only is True
    assert overridden_entities(action, changes, _SNAPSHOT) == [LAMP]


async def test_a_device_report_after_a_turn_on_leaves_the_record_as_it_was() -> None:
    """A bulb's poll, reported as an attributes-only change by no user, is not a person's
    change: it must not replace the turn-on or start its window again."""
    changes = await _noted(
        _event(LAMP, "off", "on", ago=timedelta(minutes=10)),
        _event(
            LAMP,
            "on",
            "on",
            ago=timedelta(minutes=5),
            context=ChangeContext(id="ctx-poll"),
        ),
    )

    assert changes[LAMP].time == _NOW - timedelta(minutes=10)
    assert changes[LAMP].attributes_only is False
    assert changes[LAMP].new_state == "on"


async def test_an_outside_attribute_report_on_an_entity_with_no_record_writes_nothing() -> None:
    changes = await _noted(_event(TV, "playing", "playing", context=_OUTSIDE))

    assert changes == {}


async def test_an_attribute_report_with_no_context_writes_nothing() -> None:
    changes = await _noted(_event(TV, "playing", "playing", context=None))

    assert changes == {}


async def test_a_non_person_attribute_report_does_not_refresh_the_time_of_a_record() -> None:
    changes = await _noted(
        _event(TV, "off", "playing", ago=timedelta(minutes=50)),
        _event(TV, "playing", "playing", ago=timedelta(minutes=1), context=_OUTSIDE),
    )

    assert changes[TV].time == _NOW - timedelta(minutes=50)


@pytest.mark.parametrize("context", [_AUTOMATION, _ALFRED], ids=["automation", "alfred"])
async def test_an_attribute_change_by_alfred_or_an_automation_still_clears_the_record(
    context: ChangeContext,
) -> None:
    changes = await _noted(
        _event(LAMP, "off", "on", ago=timedelta(minutes=10)),
        _event(LAMP, "on", "on", ago=timedelta(minutes=5), context=context),
    )

    assert changes == {}


async def test_attribute_changes_without_a_context_id_are_never_one_action() -> None:
    changes = await _noted(
        _event(LAMP, "off", "on", ago=timedelta(minutes=10), context=ChangeContext(user_id="u")),
        _event(LAMP, "on", "on", ago=timedelta(minutes=5), context=ChangeContext(user_id="u")),
    )

    assert changes[LAMP].attributes_only is True


async def test_a_later_person_change_replaces_the_earlier_one() -> None:
    changes = await _noted(
        _event(LAMP, "on", "off", ago=timedelta(minutes=20)),
        _event(
            LAMP,
            "off",
            "on",
            ago=timedelta(minutes=5),
            context=ChangeContext(id="ctx-2", user_id="u"),
        ),
    )

    assert changes[LAMP].new_state == "on"
    assert changes[LAMP].time == _NOW - timedelta(minutes=5)


async def test_an_action_on_something_nobody_changed_reverses_nothing() -> None:
    changes = await _noted(_event(LAMP, "on", "off"))

    action = _action("home.light_turn_on", BEDROOM_LAMP)

    assert overridden_entities(action, changes, _SNAPSHOT) == []


# --- resolve_targets ---------------------------------------------------------------------------


def test_a_target_resolves_as_an_entity_id() -> None:
    assert resolve_targets("home.light_turn_on", LAMP, _SNAPSHOT) == [LAMP]


def test_an_entity_id_matches_ignoring_case() -> None:
    assert resolve_targets("home.light_turn_on", " Light.Living_Room_Lamp ", _SNAPSHOT) == [LAMP]


def test_an_entity_id_in_another_domain_names_nothing() -> None:
    assert resolve_targets("home.light_turn_on", TV, _SNAPSHOT) == []


def test_an_area_wins_over_a_friendly_name_equal_to_it() -> None:
    assert resolve_targets("home.light_turn_on", "Kitchen", _KITCHEN) == [PENDANTS, KITCHEN_STRIP]


def test_a_target_resolves_as_a_friendly_name_ignoring_case() -> None:
    assert resolve_targets("home.light_turn_on", "living room lamp", _SNAPSHOT) == [LAMP]


def test_a_friendly_name_is_looked_up_among_the_tools_domain_only() -> None:
    assert resolve_targets("home.light_turn_on", "Living Room TV", _SNAPSHOT) == []


def test_a_target_resolves_as_an_area_to_that_domains_entities() -> None:
    assert resolve_targets("home.light_turn_on", "LIVING ROOM", _SNAPSHOT) == [CEILING, LAMP]
    assert resolve_targets("home.media_player_media_pause", "Living Room", _SNAPSHOT) == [TV]


def test_a_target_that_names_nothing_resolves_to_nothing() -> None:
    assert resolve_targets("home.light_turn_on", "the garage", _SNAPSHOT) == []


def test_a_tool_outside_every_snapshot_domain_resolves_to_nothing() -> None:
    assert resolve_targets("home.vacuum_start", "Living Room", _SNAPSHOT) == []


def test_the_longest_matching_domain_names_the_tool() -> None:
    snapshot = {
        **_SNAPSHOT,
        "media.stream": LiveEntity(
            "media.stream", "media", True, "on", {"friendly_name": "Stream"}
        ),
    }

    assert resolve_targets("home.media_player_media_pause", "Living Room", snapshot) == [TV]


def test_the_prompts_name_for_an_entity_is_not_a_home_service_target() -> None:
    assert resolve_targets("home.light_turn_on", "Living Room Lamp (Living Room)", _SNAPSHOT) == []


# --- resolved_action: what a proposal acts on, or nothing it can be checked against -------------


@pytest.mark.parametrize(
    "target", [LAMP, "Living Room", "living room lamp"], ids=["entity-id", "area", "name"]
)
def test_a_target_home_service_resolves_stands_as_written(target: str) -> None:
    action = _action("home.light_turn_on", target, brightness_pct=30)

    assert resolved_action(action, _SNAPSHOT) == action


@pytest.mark.parametrize(
    "target",
    ["Living Room Lamp (Living Room)", "  living room lamp (LIVING ROOM) "],
    ids=["as-written", "case-and-space"],
)
def test_the_prompts_name_for_one_entity_becomes_its_id(target: str) -> None:
    """The House, What changed and Leave alone lines name an entity ``Name (Room)``; the model
    copies that, and home-service does not read it. It is sent as the entity's ID."""
    resolved = resolved_action(_action("home.light_turn_on", target, brightness_pct=30), _SNAPSHOT)

    assert resolved is not None
    assert resolved.parameters == {"target": LAMP, "brightness_pct": 30}
    assert resolved.tool_name == "home.light_turn_on"


def test_the_prompts_name_resolves_within_the_tools_domain() -> None:
    action = _action("home.media_player_media_pause", "Living Room TV (Living Room)")

    resolved = resolved_action(action, _SNAPSHOT)

    assert resolved is not None
    assert resolved.parameters["target"] == TV


@pytest.mark.parametrize(
    "target",
    [
        "the garage",
        "Living Room Lamp (Bedroom)",  # the name, in the wrong room
        "Living Room TV",  # another domain's entity
        TV,
        "",
        "   ",
    ],
)
def test_a_target_that_names_nothing_of_the_tools_domain_resolves_to_none(target: str) -> None:
    assert resolved_action(_action("home.light_turn_on", target), _SNAPSHOT) is None


def test_the_prompts_name_shared_by_two_entities_resolves_to_none() -> None:
    """Two lamps of one name in one room: the name cannot say which, and no one ID names both."""
    snapshot = {
        **_SNAPSHOT,
        "light.bedroom_lamp_2": _entity("light.bedroom_lamp_2", "Bedroom Lamp", "Bedroom"),
    }

    assert (
        resolved_action(_action("home.light_turn_on", "Bedroom Lamp (Bedroom)"), snapshot) is None
    )


def test_a_tool_outside_every_snapshot_domain_resolves_to_none() -> None:
    assert resolved_action(_action("home.vacuum_start", "Living Room"), _SNAPSHOT) is None


def test_nothing_resolves_without_live_state() -> None:
    assert resolved_action(_action("home.light_turn_on", LAMP), {}) is None


@pytest.mark.parametrize("parameters", [{}, {"target": 3}, {"target": [LAMP]}])
def test_an_action_without_a_string_target_resolves_to_none(parameters: dict[str, Any]) -> None:
    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="home.light_turn_on",
        parameters=parameters,
    )

    assert resolved_action(action, _SNAPSHOT) is None


def test_an_action_without_a_string_target_reverses_nothing() -> None:
    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="home.light_turn_on",
        parameters={"brightness_pct": 30},
    )
    change = PersonChange(
        time=_NOW,
        cause="person",
        old_state="on",
        new_state="off",
        context_id=None,
        attributes_only=False,
    )

    assert overridden_entities(action, {LAMP: change}, _SNAPSHOT) == []


# --- PersonChanges: what the hash holds --------------------------------------------------------


async def test_recent_deletes_the_fields_older_than_the_window() -> None:
    redis = fakeredis.FakeAsyncRedis()
    changes = PersonChanges(redis, clock=lambda: _NOW)
    await changes.note(_event(LAMP, "on", "off", ago=timedelta(minutes=61)), "person")
    await changes.note(_event(TV, "off", "playing", ago=timedelta(minutes=59)), "person")

    kept = await changes.recent(_WINDOW)

    assert list(kept) == [TV]
    assert await redis.hkeys(REFLEX_PERSON_CHANGES_KEY) == [TV.encode()]


async def test_recent_keeps_a_record_a_newer_change_replaced_after_it_was_read() -> None:
    """The prune deletes only the value it read. Two loops share one event loop: a fresh change
    can land between ``recent`` reading an expired record and deleting it, and must survive."""
    redis = fakeredis.FakeAsyncRedis()
    changes = PersonChanges(redis, clock=lambda: _NOW)
    await changes.note(_event(LAMP, "on", "off", ago=timedelta(minutes=61)), "person")
    fresh = _event(LAMP, "off", "on", ago=timedelta(minutes=1))
    read = redis.hgetall

    async def read_then_a_person_changes_the_lamp(*args: Any, **kwargs: Any) -> Any:
        stored = await read(*args, **kwargs)
        await changes.note(fresh, "person")  # the state loop, between the read and the delete
        return stored

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(redis, "hgetall", read_then_a_person_changes_the_lamp)
        assert await changes.recent(_WINDOW) == {}  # what it read was expired

    kept = await changes.recent(_WINDOW)
    assert list(kept) == [LAMP]
    assert kept[LAMP].new_state == "on"


async def test_recent_keeps_an_unreadable_record_that_was_replaced_after_it_was_read(
    warnings: list[str],
) -> None:
    redis = fakeredis.FakeAsyncRedis()
    changes = PersonChanges(redis, clock=lambda: _NOW)
    await redis.hset(REFLEX_PERSON_CHANGES_KEY, LAMP, "{not json")
    read = redis.hgetall

    async def read_then_a_person_changes_the_lamp(*args: Any, **kwargs: Any) -> Any:
        stored = await read(*args, **kwargs)
        await changes.note(_event(LAMP, "off", "on", ago=timedelta(minutes=1)), "person")
        return stored

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(redis, "hgetall", read_then_a_person_changes_the_lamp)
        assert await changes.recent(_WINDOW) == {}

    assert list(await changes.recent(_WINDOW)) == [LAMP]
    assert len(warnings) == 1


async def test_a_change_exactly_a_window_old_is_still_held() -> None:
    kept = await _noted(_event(LAMP, "on", "off", ago=_WINDOW))

    assert list(kept) == [LAMP]


async def test_recent_drops_a_field_it_cannot_parse(warnings: list[str]) -> None:
    redis = fakeredis.FakeAsyncRedis()
    changes = PersonChanges(redis, clock=lambda: _NOW)
    await changes.note(_event(LAMP, "on", "off"), "person")
    await redis.hset(REFLEX_PERSON_CHANGES_KEY, CEILING, "{not json")

    kept = await changes.recent(_WINDOW)

    assert list(kept) == [LAMP]
    assert await redis.hkeys(REFLEX_PERSON_CHANGES_KEY) == [LAMP.encode()]
    assert len(warnings) == 1


async def test_a_clock_that_does_not_match_the_records_raises_and_deletes_nothing() -> None:
    """Only an unreadable record is deleted for it; a naive clock is the caller's mistake."""
    redis = fakeredis.FakeAsyncRedis()
    await PersonChanges(redis, clock=lambda: _NOW).note(_event(LAMP, "on", "off"), "person")
    naive = PersonChanges(redis, clock=lambda: _NOW.replace(tzinfo=None))

    with pytest.raises(TypeError):
        await naive.recent(_WINDOW)

    assert await redis.hkeys(REFLEX_PERSON_CHANGES_KEY) == [LAMP.encode()]


async def test_a_stored_time_with_no_offset_is_read_as_utc() -> None:
    """A record whose JSON time has no offset still ages normally: recent() does not raise."""
    redis = fakeredis.FakeAsyncRedis()
    changes = PersonChanges(redis, clock=lambda: _NOW)

    def record(ago: timedelta) -> str:
        naive = (_NOW - ago).replace(tzinfo=None).isoformat()
        return (
            f'{{"time": "{naive}", "cause": "person", "old_state": "on", "new_state": "off", '
            '"context_id": null, "attributes_only": false}'
        )

    await redis.hset(REFLEX_PERSON_CHANGES_KEY, LAMP, record(timedelta(minutes=10)))
    await redis.hset(REFLEX_PERSON_CHANGES_KEY, TV, record(timedelta(minutes=61)))

    kept = await changes.recent(_WINDOW)

    assert list(kept) == [LAMP]
    assert kept[LAMP].time == _NOW - timedelta(minutes=10)
    assert await redis.hkeys(REFLEX_PERSON_CHANGES_KEY) == [LAMP.encode()]


async def test_recent_holds_a_change_as_it_was_noted() -> None:
    event = _event(LAMP, "on", "off")

    kept = await _noted(event)

    assert kept == {
        LAMP: PersonChange(
            time=event.timestamp,
            cause="person",
            old_state="on",
            new_state="off",
            context_id="ctx-person",
            attributes_only=False,
        )
    }


def _broken_redis() -> AioRedis:
    refusal = redis_exceptions.ConnectionError("connection reset")
    redis = MagicMock()
    for command in ("hget", "hset", "hdel", "hgetall"):
        setattr(redis, command, AsyncMock(side_effect=refusal))
    return redis


async def test_note_never_raises_when_redis_refuses(warnings: list[str]) -> None:
    changes = PersonChanges(_broken_redis(), clock=lambda: _NOW)

    await changes.note(_event(LAMP, "on", "off"), "person")
    await changes.note(_event(LAMP, "off", "on", context=_AUTOMATION), "automation")

    assert len(warnings) == 2


async def test_recent_lets_a_failed_prune_propagate() -> None:
    """The prune is part of the read: its failure fails the decision too (fail closed)."""
    redis = fakeredis.FakeAsyncRedis()
    changes = PersonChanges(redis, clock=lambda: _NOW)
    await changes.note(_event(LAMP, "on", "off", ago=timedelta(minutes=61)), "person")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            redis, "eval", AsyncMock(side_effect=redis_exceptions.ConnectionError("reset"))
        )
        with pytest.raises(redis_exceptions.ConnectionError):
            await changes.recent(_WINDOW)


async def test_recent_lets_a_redis_error_propagate() -> None:
    """Fail closed: the event stays pending and is retried, not decided blind to a change."""
    changes = PersonChanges(_broken_redis(), clock=lambda: _NOW)

    with pytest.raises(redis_exceptions.ConnectionError):
        await changes.recent(_WINDOW)
