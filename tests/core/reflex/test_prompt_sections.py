"""Reflex prompt sections — Now, House, What changed, Trigger fired (#285)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bus.schemas.events import StateChangedEvent, TriggerFired
from core.reflex.context_reader import LIVE_STATE_UNAVAILABLE
from core.reflex.prompt import (
    MAX_DETAIL_CHARS,
    LiveEntity,
    index_snapshot,
    render_event,
    render_house,
    render_now,
    render_trigger,
    time_of_day,
)
from sdk.alfred_sdk.context import ContextEntry, ContextSnapshot

# 03:30 UTC on Thu 8 Oct 2026 is 22:30 on Wed 7 Oct in Chicago (CDT, UTC-5).
NIGHT_UTC = datetime(2026, 10, 8, 3, 30, tzinfo=UTC)


def _e(entity_id: str, state: str, controllable: bool = True, **attributes: object) -> LiveEntity:
    return LiveEntity(
        entity_id=entity_id,
        domain=entity_id.split(".", 1)[0],
        controllable=controllable,
        state=state,
        attributes=attributes,
    )


def _index(*entities: LiveEntity) -> dict[str, LiveEntity]:
    return {e.entity_id: e for e in entities}


def _change(entity_id: str, old: str | None, new: str, **attributes: object) -> StateChangedEvent:
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id=entity_id,
        old_state=old,
        new_state=new,
        attributes=attributes,
    )


# --- index_snapshot ------------------------------------------------------------


def test_index_snapshot_flattens_both_buckets() -> None:
    snapshot = ContextSnapshot(
        controllable={
            "light": [
                ContextEntry(entity_id="light.tv_lamp", state="on", attributes={"area": "Den"})
            ]
        },
        sensors={"sun": [ContextEntry(entity_id="sun.sun", state="below_horizon")]},
    )

    index = index_snapshot(snapshot)

    assert index["light.tv_lamp"] == LiveEntity(
        "light.tv_lamp", "light", True, "on", {"area": "Den"}
    )
    assert index["sun.sun"] == LiveEntity("sun.sun", "sun", False, "below_horizon", {})


# --- Now -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hour", "label"),
    [
        (0, "night"),
        (4, "night"),
        (5, "morning"),
        (11, "morning"),
        (12, "afternoon"),
        (16, "afternoon"),
        (17, "evening"),
        (20, "evening"),
        (21, "night"),
        (23, "night"),
    ],
)
def test_time_of_day_boundaries(hour: int, label: str) -> None:
    assert time_of_day(hour) == label


def test_now_is_local_with_sun_and_people_sorted_by_name() -> None:
    entities = _index(
        _e("sun.sun", "below_horizon", controllable=False),
        _e("person.b", "Work", friendly_name="Person B"),
        _e("person.a", "home", friendly_name="Person A"),
    )

    assert render_now(NIGHT_UTC, "America/Chicago", entities) == (
        "Wed 7 Oct, 22:30 (night) · sun down\nPeople: Person A home · Person B Work"
    )


def test_now_without_live_state_is_just_the_clock() -> None:
    assert render_now(NIGHT_UTC, "UTC", None) == "Thu 8 Oct, 03:30 (night)"


def test_now_with_no_people_or_sun_omits_them() -> None:
    assert render_now(NIGHT_UTC, "UTC", {}) == "Thu 8 Oct, 03:30 (night)"


# Review Focus 5
@pytest.mark.parametrize("tz_name", ["", "Not/AZone", "../etc/passwd"])
def test_a_bad_timezone_falls_back_to_utc(tz_name: str) -> None:
    assert render_now(NIGHT_UTC, tz_name, None) == "Thu 8 Oct, 03:30 (night)"


# --- House ---------------------------------------------------------------------


def test_house_groups_by_room_sorted_with_other_last() -> None:
    entities = _index(
        _e("light.desk_left", "on", friendly_name="Desk Left", area="Office", brightness=178),
        _e("light.tv_lamp", "off", friendly_name="TV Lamp", area="Living Room"),
        _e(
            "media_player.living_room_tv",
            "playing",
            friendly_name="Living Room TV",
            area="Living Room",
            media_title="A Film",
        ),
        _e(
            "climate.thermostat",
            "heat",
            friendly_name="Thermostat",
            temperature=68,
            current_temperature=66.5,
        ),
        _e("vacuum.robot", "docked", friendly_name="Robot"),
    )

    assert render_house(entities) == (
        'Living Room: Living Room TV playing "A Film" · TV Lamp off\n'
        "Office: Desk Left on 70%\n"
        "Other: Robot docked · Thermostat heat 68° (now 66.5°)"
    )


def test_house_leaves_out_sensors_people_and_noise_domains() -> None:
    entities = _index(
        _e("light.tv_lamp", "off", friendly_name="TV Lamp", area="Living Room"),
        _e("button.identify", "unknown", friendly_name="Identify"),
        _e("notify.phone", "unknown", friendly_name="Phone"),
        _e("sensor.power", "12", controllable=False, friendly_name="Power"),
        _e("person.a", "home", friendly_name="Person A"),
        _e("light.reported_as_sensor", "on", controllable=False, friendly_name="Ghost"),
    )

    assert render_house(entities) == "Living Room: TV Lamp off"


def test_house_without_live_state_says_so() -> None:
    assert render_house(None) == LIVE_STATE_UNAVAILABLE


def test_house_with_nothing_to_show_says_so() -> None:
    assert render_house({}) == "No devices to show."


# Review Focus 3
def test_house_tolerates_odd_attribute_values() -> None:
    entities = _index(
        _e("light.a", "on", friendly_name="Lamp A", area="", brightness="bright"),
        _e("light.b", "on", area=None, brightness=None),
        _e("light.c", "on", friendly_name=42, area=7, brightness=True),
        _e(
            "climate.t",
            "heat",
            friendly_name="Thermostat",
            temperature=None,
            current_temperature="warm",
        ),
        _e("media_player.tv", "playing", friendly_name="TV", media_title=None),
    )

    assert render_house(entities) == (
        "Other: Lamp A on · light.b on · light.c on · Thermostat heat · TV playing"
    )


# Review Focus 4
def test_long_titles_are_clipped() -> None:
    entities = _index(
        _e("media_player.tv", "playing", friendly_name="TV", area="Den", media_title="x" * 500)
    )

    line = render_house(entities)

    assert len(line) < 2 * MAX_DETAIL_CHARS
    assert line.endswith('…"')


# --- What changed --------------------------------------------------------------


def test_event_line_uses_live_name_and_room_and_drops_raw_attributes() -> None:
    event = _change(
        "media_player.living_room_tv",
        "paused",
        "playing",
        friendly_name="Old Name",
        media_title="A Film",
        app_name="Streamer",
        entity_picture="/api/media_player_proxy/x?token=abc",
        supported_features=450487,
    )
    entities = _index(
        _e(
            "media_player.living_room_tv",
            "playing",
            friendly_name="Living Room TV",
            area="Living Room",
        )
    )

    assert render_event(event, entities) == (
        'Living Room TV (Living Room): paused → playing · "A Film" · Streamer'
    )


def test_event_details_are_capped_at_three() -> None:
    event = _change(
        "media_player.kitchen_speaker",
        "idle",
        "playing",
        friendly_name="Kitchen Speaker",
        media_title="Song",
        media_artist="Band",
        app_name="Radio",
        brightness=100,
    )

    assert render_event(event, None) == 'Kitchen Speaker: idle → playing · "Song" · Band · Radio'


def test_climate_event_details() -> None:
    event = _change(
        "climate.thermostat",
        "off",
        "heat",
        friendly_name="Thermostat",
        temperature=68,
        current_temperature=66,
        hvac_action="heating",
    )

    assert render_event(event, None) == "Thermostat: off → heat · set 68° · now 66° · heating"


def test_an_entity_missing_from_live_state_uses_the_event() -> None:
    event = _change("binary_sensor.front_door", "off", "on", device_class="door")

    assert render_event(event, {}) == "binary_sensor.front_door: off → on"


def test_a_missing_old_state_is_shown_as_none() -> None:
    event = _change("person.a", None, "home", friendly_name="Person A")

    assert render_event(event, None) == "Person A: (none) → home"


# --- Trigger fired -------------------------------------------------------------


def test_trigger_line_with_context() -> None:
    event = TriggerFired(
        trigger_id="t-1",
        trigger_name="bedtime",
        trigger_type="time",
        context={"evaluated_at": "22:30", "b": 1},
    )

    assert render_trigger(event) == 'bedtime (time)\nContext: {"b": 1, "evaluated_at": "22:30"}'


def test_trigger_without_context_is_one_line() -> None:
    event = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")

    assert render_trigger(event) == "bedtime (time)"


# Review Focus 4
def test_trigger_context_that_is_not_json_or_is_huge_still_renders() -> None:
    event = TriggerFired(
        trigger_id="t-1",
        trigger_name="bedtime",
        trigger_type="time",
        context={"at": NIGHT_UTC, "blob": "y" * 5000},
    )

    text = render_trigger(event)

    assert text.startswith('bedtime (time)\nContext: {"at": "2026-10-08 03:30:00+00:00"')
    assert len(text) < 700
