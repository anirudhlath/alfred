from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from evals.harness.world import load_world

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


def test_entities_in_area_by_domain() -> None:
    world = load_world("apartment")
    assert world.entity_ids_in_area("living_room", "light") == [
        "light.living_room_ceiling",
        "light.living_room_lamp",
    ]


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
