"""Tests for Apple Calendar integration adapter."""

from __future__ import annotations

import pytest

from core.integrations.base import IntegrationRequest
from core.integrations.apple_calendar import AppleCalendarAdapter
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


@pytest.mark.asyncio
async def test_get_capabilities(adapter: AppleCalendarAdapter) -> None:
    caps = await adapter.get_capabilities()
    names = [c.name for c in caps]
    assert "get_today_events" in names


@pytest.mark.asyncio
async def test_health_check_no_connection() -> None:
    adapter = AppleCalendarAdapter(caldav_url="", username="", password="")
    result = await adapter.health_check()
    assert result is False
