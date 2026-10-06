"""Tests for the context reader — fresh reads of live state, and Markdown rendering."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from core.reflex.context_reader import LIVE_STATE_UNAVAILABLE, ContextReader, render_snapshot
from sdk.alfred_sdk.context import ContextEntry, ContextSnapshot

_READ = "core.reflex.context_reader.read_live_state"


def _make_snapshot() -> ContextSnapshot:
    return ContextSnapshot(
        controllable={
            "light": [
                ContextEntry(
                    entity_id="light.living_room",
                    state="on",
                    attributes={"brightness": 255},
                ),
                ContextEntry(entity_id="light.bedroom", state="off"),
            ],
            "scene": [
                ContextEntry(entity_id="scene.movie_night", state="scening"),
            ],
        },
        sensors={
            "sensor": [
                ContextEntry(entity_id="sensor.temperature", state="22.5"),
            ],
        },
    )


def test_render_snapshot_produces_markdown() -> None:
    result = render_snapshot(_make_snapshot())

    assert "### Lights" in result
    assert "- light.living_room: on (brightness: 255)" in result
    assert "- light.bedroom: off" in result
    assert "### Scenes" in result
    assert "- scene.movie_night: scening" in result
    assert "### Sensors" in result
    assert "- sensor.temperature: 22.5" in result


def test_render_empty_snapshot() -> None:
    assert render_snapshot(ContextSnapshot()) == ""


async def test_rendered_context_reads_live_state() -> None:
    redis = AsyncMock()
    with patch(_READ, AsyncMock(return_value=_make_snapshot())) as read:
        rendered = await ContextReader(redis=redis).get_rendered_context()

    read.assert_awaited_once_with(redis)
    assert "- light.living_room: on (brightness: 255)" in rendered


async def test_a_change_between_reads_shows_on_the_next_read() -> None:
    before = ContextSnapshot(
        controllable={"light": [ContextEntry(entity_id="light.lamp", state="on")]}
    )
    after = ContextSnapshot(
        controllable={"light": [ContextEntry(entity_id="light.lamp", state="off")]}
    )
    reader = ContextReader(redis=AsyncMock())

    with patch(_READ, AsyncMock(side_effect=[before, after])):
        first = await reader.get_rendered_context()
        second = await reader.get_rendered_context()

    assert "- light.lamp: on" in first
    assert "- light.lamp: off" in second


async def test_no_live_state_is_said_not_implied() -> None:
    with patch(_READ, AsyncMock(return_value=None)):
        rendered = await ContextReader(redis=AsyncMock()).get_rendered_context()

    assert rendered == LIVE_STATE_UNAVAILABLE == "Live home state unavailable."


async def test_entity_states_filter_by_glob() -> None:
    with patch(_READ, AsyncMock(return_value=_make_snapshot())):
        states = await ContextReader(redis=AsyncMock()).get_entity_states(patterns=["light.*"])

    assert states == [
        {"entity_id": "light.living_room", "state": "on", "attributes": {"brightness": 255}},
        {"entity_id": "light.bedroom", "state": "off"},
    ]


async def test_entity_states_are_none_without_live_state() -> None:
    with patch(_READ, AsyncMock(return_value=None)):
        assert await ContextReader(redis=AsyncMock()).get_entity_states() is None
