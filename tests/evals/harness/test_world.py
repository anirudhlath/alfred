from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from evals.harness.world import World, load_world

if TYPE_CHECKING:
    from pathlib import Path


# What home-service's EntityIndex.rebuild() reads from each registry.
ENTITY_FIELDS = {"entity_id", "area_id", "device_id", "name", "original_name", "disabled_by"}


def test_apartment_registries_have_the_fields_home_service_requires() -> None:
    world = load_world("apartment")
    for e in world.entity_registry():
        assert set(e) >= ENTITY_FIELDS
    for d in world.device_registry():
        assert {"id", "area_id", "name", "name_by_user"} <= set(d)
    for a in world.area_registry():
        assert {"area_id", "name"} <= set(a)


def test_disabled_entities_are_registered_but_have_no_state() -> None:
    world = load_world("apartment")
    reg = {e["entity_id"]: e for e in world.entity_registry()}
    assert reg["light.hallway_old"]["disabled_by"] == "user"
    assert "light.hallway_old" not in world.initial_states()


def test_states_carry_friendly_names() -> None:
    states = load_world("apartment").initial_states()
    assert states["light.bedroom_lamp"].attributes["friendly_name"] == "Bedroom Lamp"
    assert states["light.bedroom_lamp"].state == "off"


def test_an_unknown_world_names_the_known_ones() -> None:
    with pytest.raises(FileNotFoundError, match="apartment"):
        load_world("castle")


def test_an_entity_in_an_unknown_area_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "broken.yaml").write_text(
        "name: broken\n"
        "areas: [{area_id: kitchen, name: Kitchen}]\n"
        "entities: [{entity_id: light.attic, name: Attic, area_id: attic, state: 'off'}]\n"
        "services: {}\n"
    )
    with pytest.raises(ValidationError, match="unknown area attic"):
        load_world("broken", root=tmp_path)


def test_a_device_in_an_unknown_area_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "broken.yaml").write_text(
        "name: broken\n"
        "areas: [{area_id: kitchen, name: Kitchen}]\n"
        "devices: [{id: d1, name: Attic Lamp, area_id: attic}]\n"
        "entities: [{entity_id: light.attic, name: Attic, device_id: d1, state: 'off'}]\n"
        "services: {}\n"
    )
    with pytest.raises(ValidationError, match="d1: unknown area attic"):
        load_world("broken", root=tmp_path)


@pytest.mark.parametrize(
    ("fields", "where"),
    [
        ("{brightness_pct: {selector: {number: {min: 0, max: high}}}}", "number.max"),
        ("{brightness_pct: {selector: {number: {min: true}}}}", "number.min"),
        ("{brightness_pct: {selector: {number: [0, 100]}}}", "selector.number"),
        ("{brightness_pct: {selector: number}}", "brightness_pct.selector"),
        ("{brightness_pct: 5}", "fields.brightness_pct"),
        ("[brightness_pct]", "light.turn_on.fields"),
    ],
)
def test_a_malformed_number_selector_fails_at_load_not_at_call_time(
    tmp_path: Path, fields: str, where: str
) -> None:
    (tmp_path / "broken.yaml").write_text(
        "name: broken\n"
        "areas: []\n"
        "entities: []\n"
        f"services: {{light: {{turn_on: {{fields: {fields}}}}}}}\n"
    )
    with pytest.raises(ValidationError, match=where):
        load_world("broken", root=tmp_path)


def test_apartment_has_two_people_home_and_the_sun_up() -> None:
    states = load_world("apartment").initial_states()
    assert states["person.alex"].state == "home" and states["person.sam"].state == "home"
    assert states["sun.sun"].state == "above_horizon"
    assert "entity_picture" in states["person.alex"].attributes
    assert "supported_features" in states["light.living_room_lamp"].attributes
    assert "button.router_restart" in states


def test_area_of_uses_the_entity_then_its_device() -> None:
    world = load_world("apartment")
    assert world.area_of("light.bedroom_lamp") == "bedroom"
    assert world.area_of("media_player.living_room_tv") == "living_room"
    assert world.area_of("person.alex") is None
    assert world.area_of("light.not_there") is None
    by_device = World.model_validate(
        {
            "name": "t",
            "areas": [{"area_id": "den", "name": "Den"}, {"area_id": "kitchen", "name": "Kitchen"}],
            "devices": [{"id": "d1", "name": "Lamp", "area_id": "den"}],
            "entities": [
                {"entity_id": "light.den", "name": "Den", "device_id": "d1", "state": "off"},
                {
                    "entity_id": "light.kitchen",
                    "name": "Kitchen",
                    "area_id": "kitchen",
                    "device_id": "d1",
                    "state": "off",
                },
            ],
            "services": {"light": {}},
        }
    )
    assert by_device.area_of("light.den") == "den"
    # The entity's own area wins over its device's.
    assert by_device.area_of("light.kitchen") == "kitchen"
    assert by_device.entities_in("den") == ["light.den"]


def test_entities_in_an_area_by_domain_skip_disabled_ones() -> None:
    world = load_world("apartment")
    assert world.entities_in("living_room", "light") == [
        "light.living_room_lamp",
        "light.living_room_ceiling",
    ]
    assert "switch.coffee_maker" in world.entities_in("kitchen")
    assert "light.hallway_old" not in world.entities_in("entryway")  # disabled
