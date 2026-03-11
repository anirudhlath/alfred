"""Tests for ContextProvider models."""

from __future__ import annotations

import pytest
from alfred_sdk.context import ContextEntry, ContextProvider, ContextSnapshot
from alfred_sdk.feature import BaseFeature


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


class StubFeature(BaseFeature):
    feature_name = "stub"


@pytest.mark.asyncio
async def test_base_feature_default_get_context() -> None:
    feature = StubFeature()
    result = await feature.get_context()
    assert result == ContextSnapshot()
    assert result.controllable == {}
    assert result.sensors == {}


def test_base_feature_satisfies_context_provider_protocol() -> None:
    feature = StubFeature()
    assert isinstance(feature, ContextProvider)
