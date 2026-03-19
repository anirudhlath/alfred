"""Tests for integration base classes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

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
        return [IntegrationCapability(
            name="get_test_data",
            description="Returns test data",
            params_schema={"type": "object"},
        )]

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
