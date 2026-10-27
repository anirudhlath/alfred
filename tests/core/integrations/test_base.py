"""Tests for integration base classes."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.integrations.base import (
    Integration,
    IntegrationCapability,
    IntegrationRequest,
    IntegrationResult,
)


class FakeIntegration(Integration):
    name = "fake"
    category = "testing"

    async def get_capabilities(self) -> list[IntegrationCapability]:
        return [
            IntegrationCapability(
                name="get_test_data",
                description="Returns test data",
                params_schema={"type": "object"},
            )
        ]

    async def execute(self, request: IntegrationRequest) -> IntegrationResult:
        return IntegrationResult(
            data={"test": True},
            freshness=datetime.now(UTC),
            confidence=1.0,
        )

    async def health_check(self) -> bool:
        return True


def test_integration_result_schema() -> None:
    result = IntegrationResult(
        data={"temperature": 72},
        freshness=datetime.now(UTC),
        confidence=0.95,
    )
    assert result.confidence == 0.95


@pytest.mark.asyncio
async def test_fake_integration_execute() -> None:
    integration = FakeIntegration()
    result = await integration.execute(IntegrationRequest(action="get_test_data", params={}))
    assert result.data["test"] is True


@pytest.mark.asyncio
async def test_fake_integration_health() -> None:
    assert await FakeIntegration().health_check() is True


def test_a_capability_is_a_side_effect_unless_marked_a_read() -> None:
    """Safe by default: an unmarked capability is recorded in the turn's ledger (#304)."""
    cap = IntegrationCapability(name="do_thing", description="x", params_schema={})
    assert cap.side_effect is True


async def _capabilities() -> dict[str, bool]:
    from core.integrations.apple_calendar import AppleCalendarAdapter
    from core.integrations.apple_health import AppleHealthAdapter
    from core.integrations.robinhood import RobinhoodAdapter
    from core.integrations.weather import WeatherAdapter

    marks: dict[str, bool] = {}
    for adapter in (
        AppleCalendarAdapter(),
        AppleHealthAdapter(),
        RobinhoodAdapter(),
        WeatherAdapter(),
    ):
        for cap in await adapter.get_capabilities():
            marks[f"{type(adapter).__name__}.{cap.name}"] = cap.side_effect
    return marks


@pytest.mark.asyncio
async def test_every_adapter_marks_its_reads_and_only_its_reads() -> None:
    assert await _capabilities() == {
        "AppleCalendarAdapter.list_calendars": False,
        "AppleCalendarAdapter.get_today_events": False,
        "AppleCalendarAdapter.get_upcoming": False,
        "AppleCalendarAdapter.create_event": True,
        "AppleCalendarAdapter.delete_event": True,
        "AppleHealthAdapter.get_sleep": False,
        "AppleHealthAdapter.get_activity": False,
        "RobinhoodAdapter.get_portfolio": False,
        "RobinhoodAdapter.get_positions": False,
        "WeatherAdapter.get_current": False,
        "WeatherAdapter.get_forecast": False,
    }
