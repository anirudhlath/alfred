"""Tests for Apple Health integration adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.integrations.apple_health import AppleHealthAdapter
from core.integrations.base import IntegrationRequest
from core.integrations.registry import IntegrationRegistry


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    IntegrationRegistry._instances.pop("apple_health", None)


@pytest.fixture
def adapter() -> AppleHealthAdapter:
    return AppleHealthAdapter(endpoint="http://localhost:9000")


@pytest.fixture
def unconfigured_adapter() -> AppleHealthAdapter:
    return AppleHealthAdapter(endpoint="")


@pytest.mark.asyncio
async def test_get_capabilities(adapter: AppleHealthAdapter) -> None:
    caps = await adapter.get_capabilities()
    names = [c.name for c in caps]
    assert "get_sleep" in names
    assert "get_activity" in names


@pytest.mark.asyncio
async def test_execute_unconfigured_returns_error(
    unconfigured_adapter: AppleHealthAdapter,
) -> None:
    result = await unconfigured_adapter.execute(IntegrationRequest(action="get_sleep", params={}))
    assert result.confidence == 0.0
    assert "error" in result.data
    assert "not configured" in result.data["error"]


@pytest.mark.asyncio
async def test_execute_success(adapter: AppleHealthAdapter) -> None:
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json.return_value = {"sleep_hours": 7.5}
        mock_get.return_value = mock_resp

        result = await adapter.execute(IntegrationRequest(action="get_sleep", params={}))

    assert result.confidence == 0.8
    assert "sleep_hours" in result.data


@pytest.mark.asyncio
async def test_execute_http_error_returns_error(adapter: AppleHealthAdapter) -> None:
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = Exception("connection refused")

        result = await adapter.execute(IntegrationRequest(action="get_sleep", params={}))

    assert result.confidence == 0.0
    assert "error" in result.data


@pytest.mark.asyncio
async def test_health_check_returns_false_when_unconfigured(
    unconfigured_adapter: AppleHealthAdapter,
) -> None:
    assert await unconfigured_adapter.health_check() is False


@pytest.mark.asyncio
async def test_health_check_success(adapter: AppleHealthAdapter) -> None:
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        assert await adapter.health_check() is True


@pytest.mark.asyncio
async def test_health_check_failure(adapter: AppleHealthAdapter) -> None:
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = Exception("timeout")
        assert await adapter.health_check() is False
