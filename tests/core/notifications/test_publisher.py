"""Tests for NotificationPublisher."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.notifications.publisher import NotificationPublisher
from shared.streams import NOTIFICATIONS_STREAM


@pytest.mark.asyncio
async def test_publish_sends_to_stream() -> None:
    redis = AsyncMock()
    pub = NotificationPublisher(redis=redis)
    await pub.publish(
        channel="cost_alert",
        title="Budget Warning",
        body="Daily budget 80% consumed",
        urgency="high",
    )
    redis.xadd.assert_called_once()
    call_args = redis.xadd.call_args
    assert call_args[0][0] == NOTIFICATIONS_STREAM


@pytest.mark.asyncio
async def test_publish_includes_metadata() -> None:
    redis = AsyncMock()
    pub = NotificationPublisher(redis=redis)
    await pub.publish(
        channel="cost_alert",
        title="Budget exceeded",
        body="$5.00 daily cap reached",
        urgency="critical",
    )
    payload = redis.xadd.call_args[0][1]
    event_str = payload["event"]
    assert "cost_alert" in event_str
    assert "Budget exceeded" in event_str
