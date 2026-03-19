"""Tests for CostTracker."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from core.conscious.cost import CostTracker
from core.memory.schemas import CostState

_TODAY = datetime.now(UTC).strftime("%Y-%m-%d")


@pytest.fixture
def mock_redis() -> AsyncMock:
    return AsyncMock()


@pytest.mark.asyncio
async def test_record_spend(mock_redis: AsyncMock) -> None:
    mock_redis.get.return_value = None  # no existing state
    tracker = CostTracker(redis=mock_redis, daily_cap_usd=5.0)
    state = await tracker.record_spend(
        prompt_tokens=1000, completion_tokens=500, model="claude-opus-4-6"
    )
    assert state.spend_usd > 0
    assert state.date  # should be today


@pytest.mark.asyncio
async def test_budget_exceeded(mock_redis: AsyncMock) -> None:
    existing = CostState(date=_TODAY, spend_usd=5.01, cap_usd=5.0)
    mock_redis.get.return_value = existing.model_dump_json().encode()
    tracker = CostTracker(redis=mock_redis, daily_cap_usd=5.0)
    exceeded = await tracker.is_budget_exceeded()
    assert exceeded is True


@pytest.mark.asyncio
async def test_budget_not_exceeded(mock_redis: AsyncMock) -> None:
    existing = CostState(date=_TODAY, spend_usd=1.0, cap_usd=5.0)
    mock_redis.get.return_value = existing.model_dump_json().encode()
    tracker = CostTracker(redis=mock_redis, daily_cap_usd=5.0)
    exceeded = await tracker.is_budget_exceeded()
    assert exceeded is False


@pytest.mark.asyncio
async def test_alert_threshold(mock_redis: AsyncMock) -> None:
    existing = CostState(date=_TODAY, spend_usd=4.05, cap_usd=5.0)
    mock_redis.get.return_value = existing.model_dump_json().encode()
    tracker = CostTracker(redis=mock_redis, daily_cap_usd=5.0)
    should_alert = await tracker.should_send_alert()
    assert should_alert is True
