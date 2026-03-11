"""Tests for AlfredClient context collection during register()."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from alfred_sdk.client import AlfredClient
from alfred_sdk.context import ContextEntry, ContextSnapshot
from alfred_sdk.feature import BaseFeature, tool


class FakeFeature(BaseFeature):
    """Test feature that returns context."""

    feature_name = "fake"

    def __init__(self) -> None:
        super().__init__()

    async def get_context(self) -> ContextSnapshot:
        return ContextSnapshot(
            controllable={
                "light": [
                    ContextEntry(entity_id="light.kitchen", state="on"),
                ],
            },
        )

    @tool
    def do_thing(self, x: str) -> dict[str, Any]:
        """Do a thing.

        Args:
            x: The thing to do.
        """
        return {"x": x}


class EmptyFeature(BaseFeature):
    """Feature with no context."""

    feature_name = "empty"

    def __init__(self) -> None:
        super().__init__()

    @tool
    def noop(self) -> dict[str, Any]:
        """Do nothing."""
        return {}


@pytest.mark.asyncio
async def test_register_writes_context_to_redis() -> None:
    mock_redis = AsyncMock()
    mock_redis.hset = AsyncMock()
    mock_redis.set = AsyncMock()
    mock_redis.aclose = AsyncMock()

    client = AlfredClient(service_name="test-service")
    client.discover_features_from_classes([FakeFeature])

    with patch("redis.asyncio.from_url", return_value=mock_redis):
        await client.register()

    # Tool manifest written
    mock_redis.hset.assert_called_once()

    # Context written with TTL
    mock_redis.set.assert_called_once()
    call_args = mock_redis.set.call_args
    assert call_args[0][0] == "alfred:context:test-service"
    assert call_args[1]["ex"] == 600

    # Verify the written snapshot
    written_json = call_args[0][1]
    snapshot = ContextSnapshot.model_validate_json(written_json)
    assert "light" in snapshot.controllable
    assert snapshot.controllable["light"][0].entity_id == "light.kitchen"


@pytest.mark.asyncio
async def test_register_skips_context_when_empty() -> None:
    mock_redis = AsyncMock()
    mock_redis.hset = AsyncMock()
    mock_redis.set = AsyncMock()
    mock_redis.aclose = AsyncMock()

    client = AlfredClient(service_name="test-service")
    client.discover_features_from_classes([EmptyFeature])

    with patch("redis.asyncio.from_url", return_value=mock_redis):
        await client.register()

    # Tool manifest written, but no context
    mock_redis.hset.assert_called_once()
    mock_redis.set.assert_not_called()


@pytest.mark.asyncio
async def test_collect_context_merges_multiple_features() -> None:
    client = AlfredClient(service_name="test-service")
    client.discover_features_from_classes([FakeFeature, EmptyFeature])

    snapshot = await client._collect_context()
    assert "light" in snapshot.controllable
    assert len(snapshot.controllable["light"]) == 1
