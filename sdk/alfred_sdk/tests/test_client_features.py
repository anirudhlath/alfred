"""Tests for AlfredClient feature discovery and dispatch."""

from __future__ import annotations

import pytest

from alfred_sdk.client import AlfredClient
from alfred_sdk.feature import BaseFeature, tool


class _StubContext:
    def __init__(self) -> None:
        self.call_log: list[str] = []


class _AlphaFeature(BaseFeature):
    """Alpha feature for testing."""

    feature_name = "alpha"

    def __init__(self, ctx: _StubContext) -> None:
        super().__init__()
        self.ctx = ctx

    @tool
    def do_alpha(self, x: int) -> dict:
        """Do alpha thing.

        Args:
            x: The input value.
        """
        self.ctx.call_log.append(f"alpha:{x}")
        return {"x": x}


class _BetaFeature(BaseFeature):
    """Beta feature for testing."""

    feature_name = "beta"

    def __init__(self, ctx: _StubContext) -> None:
        super().__init__()
        self.ctx = ctx

    @tool
    def do_beta(self, y: str) -> dict:
        """Do beta thing.

        Args:
            y: The input string.
        """
        self.ctx.call_log.append(f"beta:{y}")
        return {"y": y}


def test_discover_features_from_classes() -> None:
    client = AlfredClient(service_name="test-svc")
    ctx = _StubContext()
    features = client.discover_features_from_classes(
        [_AlphaFeature, _BetaFeature], ctx=ctx
    )
    assert len(features) == 2
    # Tools are registered in dispatch table
    assert "alpha.do_alpha" in client._tool_fns
    assert "beta.do_beta" in client._tool_fns


def test_discover_features_dispatch() -> None:
    client = AlfredClient(service_name="test-svc")
    ctx = _StubContext()
    client.discover_features_from_classes([_AlphaFeature], ctx=ctx)

    result = client.dispatch_sync("alpha.do_alpha", {"x": 42})
    assert result == {"x": 42}
    assert ctx.call_log == ["alpha:42"]


def test_discover_features_builds_manifests() -> None:
    client = AlfredClient(service_name="test-svc")
    ctx = _StubContext()
    client.discover_features_from_classes([_AlphaFeature], ctx=ctx)

    manifest = client.get_registration_manifest()
    assert len(manifest["features"]) == 1
    assert manifest["features"][0]["name"] == "alpha"
    assert len(manifest["features"][0]["tools"]) == 1


def test_discover_features_name_collision_warns(caplog: pytest.LogCaptureFixture) -> None:
    """Name collision between features logs a warning."""

    class _DuplicateFeature(BaseFeature):
        feature_name = "alpha"  # Same as _AlphaFeature

        def __init__(self, ctx: _StubContext) -> None:
            super().__init__()

        @tool
        def do_alpha(self, x: int) -> dict:
            """Duplicate."""
            return {"x": x}

    client = AlfredClient(service_name="test-svc")
    ctx = _StubContext()
    client.discover_features_from_classes([_AlphaFeature, _DuplicateFeature], ctx=ctx)
    assert "collision" in caplog.text.lower() or "alpha.do_alpha" in caplog.text


@pytest.mark.asyncio
async def test_register_includes_features_in_manifest() -> None:
    from unittest.mock import AsyncMock, patch

    client = AlfredClient(service_name="test-svc", redis_url="redis://fake:6379")
    ctx = _StubContext()
    client.discover_features_from_classes([_AlphaFeature], ctx=ctx)

    mock_redis = AsyncMock()
    with patch("redis.asyncio.from_url", return_value=mock_redis):
        await client.register()

    # Verify HSET was called with a manifest containing features
    call_args = mock_redis.hset.call_args
    import json

    manifest = json.loads(call_args[0][2])
    assert len(manifest["features"]) == 1
    assert manifest["features"][0]["name"] == "alpha"
    assert len(manifest["features"][0]["tools"]) == 1


@pytest.mark.asyncio
async def test_unregister_calls_hdel() -> None:
    from unittest.mock import AsyncMock, patch

    client = AlfredClient(service_name="test-svc", redis_url="redis://fake:6379")

    mock_redis = AsyncMock()
    with patch("redis.asyncio.from_url", return_value=mock_redis):
        await client.unregister()

    mock_redis.hdel.assert_called_once_with("alfred:tool_registry", "test-svc")
    mock_redis.aclose.assert_called_once()
