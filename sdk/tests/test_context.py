"""Tests for ContextProvider models."""

from __future__ import annotations

from alfred_sdk.context import ContextEntry, ContextSnapshot


def test_context_entry_defaults() -> None:
    entry = ContextEntry(entity_id="light.living_room", state="on")
    assert entry.entity_id == "light.living_room"
    assert entry.state == "on"
    assert entry.attributes == {}


def test_context_entry_with_attributes() -> None:
    entry = ContextEntry(
        entity_id="light.bedroom",
        state="on",
        attributes={"brightness": 200},
    )
    assert entry.attributes["brightness"] == 200


def test_context_snapshot_defaults() -> None:
    snap = ContextSnapshot()
    assert snap.controllable == {}
    assert snap.sensors == {}


def test_context_snapshot_round_trip() -> None:
    snap = ContextSnapshot(
        controllable={
            "light": [
                ContextEntry(entity_id="light.living_room", state="on"),
            ],
        },
        sensors={
            "sensor": [
                ContextEntry(entity_id="sensor.temperature", state="22.5"),
            ],
        },
    )
    json_str = snap.model_dump_json()
    restored = ContextSnapshot.model_validate_json(json_str)
    assert restored == snap
    assert len(restored.controllable["light"]) == 1
    assert restored.sensors["sensor"][0].state == "22.5"
