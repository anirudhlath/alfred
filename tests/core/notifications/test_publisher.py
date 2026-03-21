"""Tests for NotificationPublisher."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.notifications.publisher import NotificationPublisher
from core.notifications.schema import Notification, Urgency


@pytest.fixture
def dispatcher() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def publisher(dispatcher: AsyncMock) -> NotificationPublisher:
    return NotificationPublisher(dispatcher=dispatcher)


class TestPublish:
    @pytest.mark.asyncio
    async def test_publish_calls_dispatcher(
        self, publisher: NotificationPublisher, dispatcher: AsyncMock
    ) -> None:
        await publisher.publish(
            title="Budget Warning",
            body="80% consumed",
            urgency=Urgency.URGENT,
            source="cost_tracker",
        )
        dispatcher.dispatch.assert_called_once()
        notification = dispatcher.dispatch.call_args[0][0]
        assert isinstance(notification, Notification)
        assert notification.title == "Budget Warning"
        assert notification.urgency is Urgency.URGENT

    @pytest.mark.asyncio
    async def test_publish_default_urgency(
        self, publisher: NotificationPublisher, dispatcher: AsyncMock
    ) -> None:
        await publisher.publish(
            title="Info",
            body="FYI",
            source="test",
        )
        notification = dispatcher.dispatch.call_args[0][0]
        assert notification.urgency is Urgency.INFORMATIONAL
