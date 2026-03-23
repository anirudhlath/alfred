"""Tests for Robinhood integration adapter."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.integrations.base import IntegrationRequest
from core.integrations.registry import IntegrationRegistry
from core.integrations.robinhood import RobinhoodAdapter


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    IntegrationRegistry._instances.pop("robinhood", None)


@pytest.fixture
def adapter() -> RobinhoodAdapter:
    return RobinhoodAdapter(username="test@test.com", password="pass123")


@pytest.fixture
def unconfigured_adapter() -> RobinhoodAdapter:
    return RobinhoodAdapter(username="", password="")


@pytest.mark.asyncio
async def test_get_capabilities(adapter: RobinhoodAdapter) -> None:
    caps = await adapter.get_capabilities()
    names = [c.name for c in caps]
    assert "get_portfolio" in names
    assert "get_positions" in names


@pytest.mark.asyncio
async def test_execute_unconfigured_returns_error(
    unconfigured_adapter: RobinhoodAdapter,
) -> None:
    result = await unconfigured_adapter.execute(
        IntegrationRequest(action="get_portfolio", params={})
    )
    assert result.confidence == 0.0
    assert "not configured" in result.data["error"]


@pytest.mark.asyncio
async def test_execute_login_failed(adapter: RobinhoodAdapter) -> None:
    with patch.object(adapter, "_ensure_login", return_value=False):
        result = await adapter.execute(IntegrationRequest(action="get_portfolio", params={}))

    assert result.confidence == 0.0
    assert "login failed" in result.data["error"]


@pytest.mark.asyncio
async def test_execute_get_portfolio_success(adapter: RobinhoodAdapter) -> None:
    with (
        patch.object(adapter, "_ensure_login", return_value=True),
        patch.object(
            adapter,
            "_fetch_data",
            return_value={"equity": "12345.67", "extended_hours_equity": "12400.00"},
        ),
    ):
        result = await adapter.execute(IntegrationRequest(action="get_portfolio", params={}))

    assert result.confidence == 0.85
    assert "equity" in result.data


@pytest.mark.asyncio
async def test_execute_fetch_error(adapter: RobinhoodAdapter) -> None:
    with (
        patch.object(adapter, "_ensure_login", return_value=True),
        patch.object(adapter, "_fetch_data", side_effect=RuntimeError("API error")),
    ):
        result = await adapter.execute(IntegrationRequest(action="get_portfolio", params={}))

    assert result.confidence == 0.0
    assert "error" in result.data


def test_ensure_login_no_username() -> None:
    adapter = RobinhoodAdapter(username="", password="")
    assert adapter._ensure_login() is False


def test_ensure_login_already_logged_in(adapter: RobinhoodAdapter) -> None:
    adapter._logged_in = True
    assert adapter._ensure_login() is True


@pytest.mark.asyncio
async def test_health_check_unconfigured(unconfigured_adapter: RobinhoodAdapter) -> None:
    assert await unconfigured_adapter.health_check() is False


@pytest.mark.asyncio
async def test_health_check_success(adapter: RobinhoodAdapter) -> None:
    with patch.object(adapter, "_ensure_login", return_value=True):
        assert await adapter.health_check() is True


def test_credentials_schema_declared() -> None:
    """Adapter should declare its credential fields."""
    schema = RobinhoodAdapter.credentials_schema
    assert "username" in schema.fields
    assert "password" in schema.fields
    assert "mfa_code" in schema.fields
    assert schema.fields["password"].field_type == "password"
    assert schema.fields["mfa_code"].required is False
    assert schema.fields["mfa_code"].transient is True
