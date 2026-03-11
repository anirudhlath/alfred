"""Tests for the context reader — Redis fetch, cache, and Markdown rendering."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from sdk.alfred_sdk.context import ContextEntry, ContextSnapshot


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
    from core.reflex.context_reader import render_snapshot

    snapshot = _make_snapshot()
    result = render_snapshot(snapshot)

    assert "### Lights" in result
    assert "- light.living_room: on (brightness: 255)" in result
    assert "- light.bedroom: off" in result
    assert "### Scenes" in result
    assert "- scene.movie_night: scening" in result
    assert "### Sensors" in result
    assert "- sensor.temperature: 22.5" in result


def test_render_empty_snapshot() -> None:
    from core.reflex.context_reader import render_snapshot

    result = render_snapshot(ContextSnapshot())
    assert result == ""


@pytest.mark.asyncio
async def test_context_reader_fetches_from_redis() -> None:
    from core.reflex.context_reader import ContextReader

    snapshot = _make_snapshot()

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=snapshot.model_dump_json().encode())

    reader = ContextReader(redis=mock_redis)
    result = await reader.get_rendered_context()

    assert "light.living_room" in result
    assert "brightness: 255" in result
    mock_redis.get.assert_called_once_with("alfred:context:home-service")


@pytest.mark.asyncio
async def test_context_reader_caches_result() -> None:
    from core.reflex.context_reader import ContextReader

    snapshot = _make_snapshot()

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=snapshot.model_dump_json().encode())

    reader = ContextReader(redis=mock_redis)
    result1 = await reader.get_rendered_context()
    result2 = await reader.get_rendered_context()

    assert result1 == result2
    # Redis only queried once (cached)
    mock_redis.get.assert_called_once()


@pytest.mark.asyncio
async def test_context_reader_returns_empty_when_key_missing() -> None:
    from core.reflex.context_reader import ContextReader

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)

    reader = ContextReader(redis=mock_redis)
    result = await reader.get_rendered_context()

    assert result == ""


@pytest.mark.asyncio
async def test_context_reader_caches_empty_result() -> None:
    """Empty result (key missing) should also be cached — don't re-query Redis."""
    from core.reflex.context_reader import ContextReader

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)

    reader = ContextReader(redis=mock_redis)
    await reader.get_rendered_context()
    await reader.get_rendered_context()

    # Redis only queried once despite empty result
    mock_redis.get.assert_called_once()
