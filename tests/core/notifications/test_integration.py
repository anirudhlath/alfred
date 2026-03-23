"""Integration tests for the full notification pipeline.

Tests the flow: publish → dispatcher → DND check → stream publish / deferral → drain.
Uses mock Redis but real Dispatcher, DNDChecker, and Publisher wiring.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from core.notifications.dispatcher import NotificationDispatcher
from core.notifications.dnd import DNDChecker
from core.notifications.publisher import NotificationPublisher
from core.notifications.schema import Notification, Urgency
from shared.streams import NOTIFICATION_DISPATCH_STREAM


@pytest.fixture
def redis() -> AsyncMock:
    r = AsyncMock()
    r.get.return_value = None  # No DND by default
    r.llen.return_value = 0
    return r


class TestEndToEndFlow:
    @pytest.mark.asyncio
    async def test_publish_dispatches_to_stream_when_no_dnd(self, redis: AsyncMock) -> None:
        dnd = DNDChecker(redis=redis, calendar_adapter=None)
        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd)
        publisher = NotificationPublisher(dispatcher=dispatcher)

        await publisher.publish(
            title="Weather Alert",
            body="Rain expected at 3pm",
            source="weather",
            urgency=Urgency.INFORMATIONAL,
        )

        redis.xadd.assert_called_once()
        stream, payload = redis.xadd.call_args[0]
        assert stream == NOTIFICATION_DISPATCH_STREAM
        restored = Notification.model_validate_json(payload["notification"])
        assert restored.title == "Weather Alert"

    @pytest.mark.asyncio
    async def test_publish_defers_during_dnd(self, redis: AsyncMock) -> None:
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        redis.get.return_value = json.dumps(
            {
                "active": True,
                "until": future,
                "reason": "Focus time",
                "source": "manual",
            }
        )

        dnd = DNDChecker(redis=redis, calendar_adapter=None)
        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd)
        publisher = NotificationPublisher(dispatcher=dispatcher)

        await publisher.publish(
            title="FYI",
            body="Non-urgent info",
            source="test",
            urgency=Urgency.INFORMATIONAL,
        )

        redis.xadd.assert_not_called()
        redis.rpush.assert_called_once()

    @pytest.mark.asyncio
    async def test_urgent_bypasses_dnd(self, redis: AsyncMock) -> None:
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        redis.get.return_value = json.dumps(
            {
                "active": True,
                "until": future,
                "reason": "Focus time",
                "source": "manual",
            }
        )

        dnd = DNDChecker(redis=redis, calendar_adapter=None)
        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd)
        publisher = NotificationPublisher(dispatcher=dispatcher)

        await publisher.publish(
            title="URGENT",
            body="Fire alarm",
            source="safety",
            urgency=Urgency.URGENT,
        )

        # Urgent bypasses DND — published to stream
        redis.xadd.assert_called_once()
        redis.rpush.assert_not_called()

    @pytest.mark.asyncio
    async def test_drain_publishes_deferred_to_stream(self, redis: AsyncMock) -> None:
        """Simulate: DND was active, deferred 2 notifications. DND expires, drain fires."""
        n1 = Notification(title="N1", body="First", urgency=Urgency.INFORMATIONAL, source="a")
        n2 = Notification(title="N2", body="Second", urgency=Urgency.IMPORTANT, source="b")

        redis.get.return_value = None  # DND now inactive
        redis.llen.return_value = 2
        redis.lpop.side_effect = [
            n1.model_dump_json().encode(),
            n2.model_dump_json().encode(),
            None,
        ]

        dnd = DNDChecker(redis=redis, calendar_adapter=None)
        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd)
        await dispatcher.drain_deferred()

        # Both notifications published to dispatch stream
        assert redis.xadd.call_count == 2
        titles = set()
        for call in redis.xadd.call_args_list:
            payload = call[0][1]
            n = Notification.model_validate_json(payload["notification"])
            titles.add(n.title)
        assert titles == {"N1", "N2"}
