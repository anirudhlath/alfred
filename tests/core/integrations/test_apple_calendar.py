"""Tests for Apple Calendar integration adapter."""

from __future__ import annotations

import pytest

from core.integrations.apple_calendar import AppleCalendarAdapter
from core.integrations.base import IntegrationRequest
from core.integrations.registry import IntegrationRegistry


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """Clear cached instances so each test gets a fresh adapter."""
    IntegrationRegistry._instances.pop("apple_calendar", None)


@pytest.fixture
def adapter() -> AppleCalendarAdapter:
    return AppleCalendarAdapter(
        caldav_url="https://caldav.icloud.com",
        username="test@icloud.com",
        password="app-specific-password",
    )


@pytest.fixture
def unconfigured_adapter() -> AppleCalendarAdapter:
    return AppleCalendarAdapter(caldav_url="", username="", password="")


@pytest.mark.asyncio
async def test_get_capabilities(adapter: AppleCalendarAdapter) -> None:
    caps = await adapter.get_capabilities()
    names = [c.name for c in caps]
    assert "get_today_events" in names
    assert "get_upcoming" in names


@pytest.mark.asyncio
async def test_health_check_no_connection(unconfigured_adapter: AppleCalendarAdapter) -> None:
    result = await unconfigured_adapter.health_check()
    assert result is False


@pytest.mark.asyncio
async def test_execute_unconfigured_returns_error(
    unconfigured_adapter: AppleCalendarAdapter,
) -> None:
    """execute() with empty URL returns error result, not an exception."""
    result = await unconfigured_adapter.execute(
        IntegrationRequest(action="get_today_events", params={})
    )
    assert result.confidence == 0.0
    assert "error" in result.data
    assert "not configured" in result.data["error"]


@pytest.mark.asyncio
async def test_execute_caldav_not_installed(adapter: AppleCalendarAdapter) -> None:
    """If caldav package is missing, returns a graceful error."""
    # caldav may or may not be installed in test env — test the ImportError path
    # by monkeypatching importlib.util.find_spec
    import importlib.util
    from unittest.mock import patch

    with patch.object(importlib.util, "find_spec", return_value=None):
        result = await adapter.execute(IntegrationRequest(action="get_today_events", params={}))
    assert result.confidence == 0.0
    assert "not installed" in result.data["error"]


def test_credentials_schema_declared() -> None:
    """Adapter should declare its credential fields."""
    schema = AppleCalendarAdapter.credentials_schema
    assert "caldav_url" in schema.fields
    assert "username" in schema.fields
    assert "password" in schema.fields
    assert schema.fields["password"].field_type == "password"
    assert schema.fields["caldav_url"].field_type == "url"
