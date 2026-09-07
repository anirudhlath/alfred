"""Tests for CostTracker."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from core.conscious.cost import CostState, CostTracker

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


@pytest.mark.asyncio
async def test_day_rollover_resets_spend(mock_redis: AsyncMock) -> None:
    """Stale date in Redis resets to fresh state for today."""
    stale = CostState(date="2025-01-01", spend_usd=10.0, cap_usd=5.0, request_count=9)
    mock_redis.get.return_value = stale.model_dump_json().encode()
    tracker = CostTracker(redis=mock_redis, daily_cap_usd=5.0)

    exceeded = await tracker.is_budget_exceeded()
    assert exceeded is False  # fresh day, spend_usd=0.0

    state = await tracker.record_spend(
        prompt_tokens=100, completion_tokens=50, model="claude-opus-4-6"
    )
    assert state.request_count == 1  # yesterday's count does not carry over


@pytest.mark.asyncio
async def test_mark_alert_sent(mock_redis: AsyncMock) -> None:
    """After marking alert sent, should_send_alert returns False."""
    existing = CostState(date=_TODAY, spend_usd=4.5, cap_usd=5.0, alert_sent=False)
    mock_redis.get.return_value = existing.model_dump_json().encode()
    tracker = CostTracker(redis=mock_redis, daily_cap_usd=5.0)

    # Should alert initially
    should = await tracker.should_send_alert()
    assert should is True

    # Mark alert sent — update the mock to return the new state
    await tracker.mark_alert_sent()

    # Capture what was persisted to Redis and use it for the next read
    saved_json = mock_redis.set.call_args_list[-1][0][1]
    mock_redis.get.return_value = saved_json.encode() if isinstance(saved_json, str) else saved_json

    should_after = await tracker.should_send_alert()
    assert should_after is False


@pytest.mark.asyncio
async def test_send_alert_if_needed_publishes_with_urgency(mock_redis: AsyncMock) -> None:
    """send_alert_if_needed uses Urgency.URGENT when publishing."""
    from core.notifications.schema import Urgency

    existing = CostState(date=_TODAY, spend_usd=4.5, cap_usd=5.0, alert_sent=False, request_count=7)
    mock_redis.get.return_value = existing.model_dump_json().encode()

    notifier = AsyncMock()
    tracker = CostTracker(redis=mock_redis, daily_cap_usd=5.0, notifier=notifier)
    result = await tracker.send_alert_if_needed()
    assert result is True
    notifier.publish.assert_called_once()
    call_kwargs = notifier.publish.call_args[1]
    assert call_kwargs["urgency"] is Urgency.URGENT
    assert call_kwargs["source"] == "cost_tracker"

    saved = CostState.model_validate_json(mock_redis.set.call_args[0][1])
    assert saved.request_count == 7  # the alert flag must not drop the count
    assert saved.alert_sent is True


@pytest.mark.asyncio
async def test_record_spend_counts_requests(mock_redis: AsyncMock) -> None:
    existing = CostState(date=_TODAY, spend_usd=1.0, cap_usd=5.0, request_count=3)
    mock_redis.get.return_value = existing.model_dump_json().encode()
    tracker = CostTracker(redis=mock_redis, daily_cap_usd=5.0)

    state = await tracker.record_spend(
        prompt_tokens=1000, completion_tokens=500, model="claude-opus-4-6"
    )

    assert state.request_count == 4
    assert state.spend_usd > 1.0


def test_avg_usd_is_spend_over_requests() -> None:
    assert CostState(date=_TODAY, spend_usd=0.0, cap_usd=5.0).avg_usd == 0.0
    state = CostState(date=_TODAY, spend_usd=1.5, cap_usd=5.0, request_count=4)
    assert state.avg_usd == 0.375
    assert "avg_usd" in state.model_dump()
    # a non-terminating quotient is rounded to 6dp
    assert CostState(date=_TODAY, spend_usd=1.0, cap_usd=5.0, request_count=3).avg_usd == 0.333333


def test_stored_json_without_new_fields_still_loads() -> None:
    """Data written before this change (no request_count/avg_usd) and data written
    after it (avg_usd present in JSON) both validate."""
    old = CostState.model_validate_json('{"date": "2026-09-01", "spend_usd": 0.5, "cap_usd": 5.0}')
    assert old.request_count == 0
    assert old.avg_usd == 0.0

    new = CostState(date=_TODAY, spend_usd=2.0, cap_usd=5.0, request_count=2)
    reloaded = CostState.model_validate_json(new.model_dump_json())
    assert reloaded.request_count == 2
    assert reloaded.avg_usd == 1.0


@pytest.mark.asyncio
async def test_mark_alert_sent_keeps_request_count(mock_redis: AsyncMock) -> None:
    existing = CostState(date=_TODAY, spend_usd=4.5, cap_usd=5.0, request_count=7)
    mock_redis.get.return_value = existing.model_dump_json().encode()
    tracker = CostTracker(redis=mock_redis, daily_cap_usd=5.0)

    await tracker.mark_alert_sent()

    saved = CostState.model_validate_json(mock_redis.set.call_args[0][1])
    assert saved.alert_sent is True
    assert saved.request_count == 7
