"""Tests for multi-service ContextReader."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.reflex.context_reader import ContextReader
from sdk.alfred_sdk.context import ContextEntry, ContextSnapshot
from shared.streams import CONTEXT_KEY_PREFIX


@pytest.fixture
def mock_redis() -> AsyncMock:
    return AsyncMock()


@pytest.mark.asyncio
async def test_multi_service_scan(mock_redis: AsyncMock) -> None:
    """ContextReader scans all alfred:context:* keys, not just home-service."""
    snap_home = ContextSnapshot(
        controllable={"light": [ContextEntry(entity_id="light.living", state="on")]},
    )
    snap_weather = ContextSnapshot(
        sensors={"weather": [ContextEntry(entity_id="weather.home", state="sunny")]},
    )

    # Mock SCAN to return two keys
    async def mock_scan_iter(match: str, count: int = 100) -> Any:
        for key in [f"{CONTEXT_KEY_PREFIX}home-service", f"{CONTEXT_KEY_PREFIX}weather-service"]:
            yield key.encode()

    mock_redis.scan_iter = mock_scan_iter

    async def mock_get(key: str | bytes) -> bytes | None:
        k = key.decode() if isinstance(key, bytes) else key
        if k.endswith("home-service"):
            return snap_home.model_dump_json().encode()
        if k.endswith("weather-service"):
            return snap_weather.model_dump_json().encode()
        return None

    mock_redis.get = AsyncMock(side_effect=mock_get)

    reader = ContextReader(redis=mock_redis)
    rendered = await reader.get_rendered_context()

    assert "light.living" in rendered
    assert "weather.home" in rendered


@pytest.mark.asyncio
async def test_empty_scan_returns_empty(mock_redis: AsyncMock) -> None:
    """ContextReader returns empty string when no context keys exist."""

    async def mock_scan_iter(match: str, count: int = 100) -> Any:
        return
        yield  # make it an async generator

    mock_redis.scan_iter = mock_scan_iter

    reader = ContextReader(redis=mock_redis)
    rendered = await reader.get_rendered_context()
    assert rendered == ""
