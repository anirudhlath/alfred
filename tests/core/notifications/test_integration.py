"""Integration tests for the full notification pipeline.

Tests the flow: publish → dispatcher → DND check → channel delivery / deferral → drain.
Uses mock Redis but real Dispatcher, DNDChecker, and ChannelRegistry wiring.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest

from core.notifications.channels import ChannelAdapter, ChannelRegistry
from core.notifications.dispatcher import NotificationDispatcher
from core.notifications.dnd import DNDChecker
from core.notifications.publisher import NotificationPublisher
from core.notifications.schema import Notification, Urgency


class RecordingAdapter(ChannelAdapter):
    """Test adapter that records all deliveries."""

    name: ClassVar[str] = "recording"
    supported_urgencies: ClassVar[set[Urgency]] = {
        Urgency.INFORMATIONAL,
        Urgency.IMPORTANT,
        Urgency.URGENT,
    }

    def __init__(self) -> None:
        self.delivered: list[Notification] = []

    async def deliver(self, notification: Notification) -> None:
        self.delivered.append(notification)


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    ChannelRegistry._registry.clear()
    ChannelRegistry._instances.clear()


@pytest.fixture
def redis() -> AsyncMock:
    r = AsyncMock()
    r.get.return_value = None  # No DND by default
    r.llen.return_value = 0
    return r


@pytest.fixture
def adapter() -> RecordingAdapter:
    a = RecordingAdapter()
    ChannelRegistry._registry["recording"] = RecordingAdapter
    ChannelRegistry._instances["recording"] = a
    return a


class TestEndToEndFlow:
    @pytest.mark.asyncio
    async def test_publish_delivers_when_no_dnd(
        self, redis: AsyncMock, adapter: RecordingAdapter
    ) -> None:
        dnd = DNDChecker(redis=redis, calendar_adapter=None)
        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd)
        publisher = NotificationPublisher(dispatcher=dispatcher)

        await publisher.publish(
            title="Weather Alert",
            body="Rain expected at 3pm",
            source="weather",
            urgency=Urgency.INFORMATIONAL,
        )

        assert len(adapter.delivered) == 1
        assert adapter.delivered[0].title == "Weather Alert"

    @pytest.mark.asyncio
    async def test_publish_defers_during_dnd(
        self, redis: AsyncMock, adapter: RecordingAdapter
    ) -> None:
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

        assert len(adapter.delivered) == 0
        redis.rpush.assert_called_once()

    @pytest.mark.asyncio
    async def test_urgent_bypasses_dnd(self, redis: AsyncMock, adapter: RecordingAdapter) -> None:
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

        assert len(adapter.delivered) == 1

    @pytest.mark.asyncio
    async def test_drain_delivers_deferred(
        self, redis: AsyncMock, adapter: RecordingAdapter
    ) -> None:
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

        assert len(adapter.delivered) == 2
        assert adapter.delivered[0].title == "N1"
        assert adapter.delivered[1].title == "N2"
