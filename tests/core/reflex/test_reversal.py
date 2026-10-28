"""Never reverse a person: the record of what they just changed and the rule that reads it.

Ids and names are the eval apartment world's (``evals/harness/worlds/apartment.yaml``). The tool
names are test data; the code under test names no tool.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
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
    resolve_targets,
    reversed_entities,
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


# --- the rule: each case of the table ---------------------------------------------------------


async def test_turning_on_what_a_person_turned_off_reverses_it() -> None:
    changes = await _noted(_event(LAMP, "on", "off"))

    reversed_ = reversed_entities(_action("home.light_turn_on", LAMP), changes, _SNAPSHOT)

    assert reversed_ == [LAMP]


async def test_a_change_older_than_the_window_is_not_held() -> None:
    changes = await _noted(_event(LAMP, "on", "off", ago=timedelta(minutes=61)))

    assert reversed_entities(_action("home.light_turn_on", LAMP), changes, _SNAPSHOT) == []


async def test_dimming_what_a_person_turned_on_is_not_a_reversal() -> None:
    changes = await _noted(_event(LAMP, "off", "on"))

    action = _action("home.light_turn_on", LAMP, brightness_pct=30)

    assert reversed_entities(action, changes, _SNAPSHOT) == []


async def test_changing_a_brightness_a_person_just_set_reverses_it() -> None:
    changes = await _noted(_event(LAMP, "on", "on"))

    action = _action("home.light_turn_on", LAMP, brightness_pct=80)

    assert reversed_entities(action, changes, _SNAPSHOT) == [LAMP]


async def test_pausing_what_a_person_started_reverses_it() -> None:
    changes = await _noted(_event(TV, "off", "playing"))

    reversed_ = reversed_entities(_action("home.media_player_media_pause", TV), changes, _SNAPSHOT)

    assert reversed_ == [TV]


async def test_resuming_what_a_person_paused_reverses_it() -> None:
    changes = await _noted(_event(TV, "playing", "paused"))

    reversed_ = reversed_entities(_action("home.media_player_media_play", TV), changes, _SNAPSHOT)

    assert reversed_ == [TV]


@pytest.mark.parametrize("context", [_OUTSIDE, None], ids=["outside", "no-context"])
async def test_a_change_nobody_can_name_counts_as_a_persons(context: ChangeContext | None) -> None:
    changes = await _noted(_event(LAMP, "on", "off", context=context))

    assert reversed_entities(_action("home.light_turn_on", LAMP), changes, _SNAPSHOT) == [LAMP]


async def test_a_later_automation_change_clears_the_persons() -> None:
    changes = await _noted(
        _event(LAMP, "off", "on", ago=timedelta(minutes=10)),
        _event(LAMP, "on", "off", ago=timedelta(minutes=5), context=_AUTOMATION),
    )

    assert changes == {}
    assert reversed_entities(_action("home.light_turn_off", LAMP), changes, _SNAPSHOT) == []


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

    reversed_ = reversed_entities(_action("home.light_turn_on", "Living room"), changes, _SNAPSHOT)

    assert reversed_ == [LAMP]


async def test_a_friendly_name_target_reaches_the_entity() -> None:
    changes = await _noted(_event(LAMP, "on", "off"))

    reversed_ = reversed_entities(
        _action("home.light_turn_on", "Living Room Lamp"), changes, _SNAPSHOT
    )

    assert reversed_ == [LAMP]


async def test_a_service_it_does_not_know_is_a_reversal_of_a_state_change() -> None:
    changes = await _noted(_event(LAMP, "on", "off"))

    assert reversed_entities(_action("home.light_toggle", LAMP), changes, _SNAPSHOT) == [LAMP]


async def test_a_service_it_does_not_know_is_a_reversal_of_a_brightness_change() -> None:
    changes = await _noted(_event(LAMP, "on", "on"))

    assert reversed_entities(_action("home.light_toggle", LAMP), changes, _SNAPSHOT) == [LAMP]


async def test_an_unknown_service_that_sets_a_brightness_is_allowed_after_a_state_change() -> None:
    changes = await _noted(_event(LAMP, "off", "on"))

    action = _action("home.light_toggle", LAMP, brightness_pct=30)

    assert reversed_entities(action, changes, _SNAPSHOT) == []


async def test_setting_the_volume_of_what_a_person_just_set_the_volume_of_reverses_it() -> None:
    changes = await _noted(_event(TV, "playing", "playing"))

    action = _action("home.media_player_volume_set", TV, volume_level=0.5)

    assert reversed_entities(action, changes, _SNAPSHOT) == [TV]


async def test_setting_the_volume_of_what_a_person_started_is_not_a_reversal() -> None:
    """The person started the TV; they did not set its volume. ``volume_set`` is an attribute
    setter, so against a state change it is neither a known state nor an unknown kind."""
    changes = await _noted(_event(TV, "off", "playing"))

    action = _action("home.media_player_volume_set", TV, volume_level=0.5)

    assert reversed_entities(action, changes, _SNAPSHOT) == []


async def test_an_attribute_report_in_the_same_action_leaves_the_turn_on_as_it_was() -> None:
    changes = await _noted(
        _event(LAMP, "off", "on", ago=timedelta(minutes=10)),
        _event(LAMP, "on", "on", ago=timedelta(minutes=10)),  # the bulb's brightness report
    )

    action = _action("home.light_turn_on", LAMP, brightness_pct=30)

    assert reversed_entities(action, changes, _SNAPSHOT) == []


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
    assert reversed_entities(action, changes, _SNAPSHOT) == [LAMP]


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

    assert reversed_entities(_action("home.light_turn_on", LAMP), changes, _SNAPSHOT) == []
    assert reversed_entities(_action("home.light_turn_off", LAMP), changes, _SNAPSHOT) == [LAMP]


async def test_an_action_on_something_nobody_changed_reverses_nothing() -> None:
    changes = await _noted(_event(LAMP, "on", "off"))

    action = _action("home.light_turn_on", BEDROOM_LAMP)

    assert reversed_entities(action, changes, _SNAPSHOT) == []


# --- resolve_targets ---------------------------------------------------------------------------


def test_a_target_resolves_as_an_entity_id() -> None:
    assert resolve_targets("home.light_turn_on", LAMP, _SNAPSHOT) == [LAMP]


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

    assert reversed_entities(action, {LAMP: change}, _SNAPSHOT) == []


# --- PersonChanges: what the hash holds --------------------------------------------------------


async def test_recent_deletes_the_fields_older_than_the_window() -> None:
    redis = fakeredis.FakeAsyncRedis()
    changes = PersonChanges(redis, clock=lambda: _NOW)
    await changes.note(_event(LAMP, "on", "off", ago=timedelta(minutes=61)), "person")
    await changes.note(_event(TV, "off", "playing", ago=timedelta(minutes=59)), "person")

    kept = await changes.recent(_WINDOW)

    assert list(kept) == [TV]
    assert await redis.hkeys(REFLEX_PERSON_CHANGES_KEY) == [TV.encode()]


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


async def test_recent_lets_a_redis_error_propagate() -> None:
    """Fail closed: the event stays pending and is retried, not decided blind to a change."""
    changes = PersonChanges(_broken_redis(), clock=lambda: _NOW)

    with pytest.raises(redis_exceptions.ConnectionError):
        await changes.recent(_WINDOW)
