"""Tests for the context reader — Redis fetch, cache, and Markdown rendering."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from sdk.alfred_sdk.context import ContextEntry, ContextSnapshot
from shared.streams import CONTEXT_KEY_PREFIX


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


def _make_mock_redis(snapshot: ContextSnapshot | None) -> AsyncMock:
    """Create a mock Redis with scan_iter + get configured for a single service."""
    mock_redis = AsyncMock()

    key = f"{CONTEXT_KEY_PREFIX}home-service".encode()

    async def mock_scan_iter(match: str, count: int = 100) -> Any:
        if snapshot is not None:
            yield key

    mock_redis.scan_iter = mock_scan_iter

    if snapshot is not None:
        mock_redis.get = AsyncMock(return_value=snapshot.model_dump_json().encode())
    else:
        mock_redis.get = AsyncMock(return_value=None)

    return mock_redis


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
    mock_redis = _make_mock_redis(snapshot)

    reader = ContextReader(redis=mock_redis)
    result = await reader.get_rendered_context()

    assert "light.living_room" in result
    assert "brightness: 255" in result
    mock_redis.get.assert_called_once_with(f"{CONTEXT_KEY_PREFIX}home-service".encode())


@pytest.mark.asyncio
async def test_context_reader_caches_result() -> None:
    from core.reflex.context_reader import ContextReader

    snapshot = _make_snapshot()
    mock_redis = _make_mock_redis(snapshot)

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

    async def mock_scan_iter(match: str, count: int = 100) -> Any:
        return
        yield  # make it an async generator

    mock_redis.scan_iter = mock_scan_iter

    reader = ContextReader(redis=mock_redis)
    result = await reader.get_rendered_context()

    assert result == ""


@pytest.mark.asyncio
async def test_context_reader_caches_empty_result() -> None:
    """Empty result (no keys) should also be cached — don't re-scan Redis."""
    from core.reflex.context_reader import ContextReader

    scan_call_count = 0

    async def mock_scan_iter(match: str, count: int = 100) -> Any:
        nonlocal scan_call_count
        scan_call_count += 1
        return
        yield  # make it an async generator

    mock_redis = AsyncMock()
    mock_redis.scan_iter = mock_scan_iter

    reader = ContextReader(redis=mock_redis)
    await reader.get_rendered_context()
    await reader.get_rendered_context()

    # scan_iter only called once despite empty result (cached)
    assert scan_call_count == 1
