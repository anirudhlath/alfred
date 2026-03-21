"""Tests for NotificationDispatcher — routing, deferral, and drain logic."""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import AsyncMock

import pytest

from core.notifications.channels import ChannelAdapter, ChannelRegistry
from core.notifications.dispatcher import NotificationDispatcher
from core.notifications.dnd import DNDChecker
from core.notifications.schema import DNDStatus, Notification, Urgency
from shared.streams import DEFERRED_NOTIFICATIONS_KEY


class FakeSignal(ChannelAdapter):
    name: ClassVar[str] = "fake_signal"
    supported_urgencies: ClassVar[set[Urgency]] = {
        Urgency.INFORMATIONAL,
        Urgency.IMPORTANT,
        Urgency.URGENT,
    }

    def __init__(self) -> None:
        self.delivered: list[Notification] = []

    async def deliver(self, notification: Notification) -> None:
        self.delivered.append(notification)


class FakeWS(ChannelAdapter):
    name: ClassVar[str] = "fake_ws"
    supported_urgencies: ClassVar[set[Urgency]] = {Urgency.IMPORTANT, Urgency.URGENT}

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
    return AsyncMock()


@pytest.fixture
def dnd_inactive() -> DNDChecker:
    checker = AsyncMock(spec=DNDChecker)
    checker.is_active.return_value = DNDStatus(active=False)
    return checker


@pytest.fixture
def dnd_active() -> DNDChecker:
    checker = AsyncMock(spec=DNDChecker)
    checker.is_active.return_value = DNDStatus(
        active=True, source="manual", reason="User requested"
    )
    return checker


def _make_notification(urgency: Urgency = Urgency.INFORMATIONAL) -> Notification:
    return Notification(title="Test", body="Hello", urgency=urgency, source="test")


class TestDispatchRouting:
    @pytest.mark.asyncio
    async def test_informational_routes_to_signal_only(
        self, redis: AsyncMock, dnd_inactive: DNDChecker
    ) -> None:
        signal = FakeSignal()
        ws = FakeWS()
        ChannelRegistry._registry["fake_signal"] = FakeSignal
        ChannelRegistry._instances["fake_signal"] = signal
        ChannelRegistry._registry["fake_ws"] = FakeWS
        ChannelRegistry._instances["fake_ws"] = ws

        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_inactive)
        await dispatcher.dispatch(_make_notification(Urgency.INFORMATIONAL))

        assert len(signal.delivered) == 1
        assert len(ws.delivered) == 0

    @pytest.mark.asyncio
    async def test_important_routes_to_signal_and_ws(
        self, redis: AsyncMock, dnd_inactive: DNDChecker
    ) -> None:
        signal = FakeSignal()
        ws = FakeWS()
        ChannelRegistry._registry["fake_signal"] = FakeSignal
        ChannelRegistry._instances["fake_signal"] = signal
        ChannelRegistry._registry["fake_ws"] = FakeWS
        ChannelRegistry._instances["fake_ws"] = ws

        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_inactive)
        await dispatcher.dispatch(_make_notification(Urgency.IMPORTANT))

        assert len(signal.delivered) == 1
        assert len(ws.delivered) == 1


class TestDNDDeferral:
    @pytest.mark.asyncio
    async def test_dnd_active_defers_informational(
        self, redis: AsyncMock, dnd_active: DNDChecker
    ) -> None:
        signal = FakeSignal()
        ChannelRegistry._registry["fake_signal"] = FakeSignal
        ChannelRegistry._instances["fake_signal"] = signal

        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_active)
        await dispatcher.dispatch(_make_notification(Urgency.INFORMATIONAL))

        assert len(signal.delivered) == 0
        redis.rpush.assert_called_once()
        call_args = redis.rpush.call_args[0]
        assert call_args[0] == DEFERRED_NOTIFICATIONS_KEY

    @pytest.mark.asyncio
    async def test_dnd_active_delivers_urgent(
        self, redis: AsyncMock, dnd_active: DNDChecker
    ) -> None:
        signal = FakeSignal()
        ChannelRegistry._registry["fake_signal"] = FakeSignal
        ChannelRegistry._instances["fake_signal"] = signal

        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_active)
        await dispatcher.dispatch(_make_notification(Urgency.URGENT))

        assert len(signal.delivered) == 1
        redis.rpush.assert_not_called()


class TestDrainDeferred:
    @pytest.mark.asyncio
    async def test_drain_resubmits_through_dispatch(
        self, redis: AsyncMock, dnd_inactive: DNDChecker
    ) -> None:
        signal = FakeSignal()
        ChannelRegistry._registry["fake_signal"] = FakeSignal
        ChannelRegistry._instances["fake_signal"] = signal

        notification = _make_notification(Urgency.INFORMATIONAL)
        serialized = notification.model_dump_json()

        redis.llen.return_value = 1
        redis.lpop.side_effect = [serialized.encode(), None]

        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_inactive)
        await dispatcher.drain_deferred()

        assert len(signal.delivered) == 1

    @pytest.mark.asyncio
    async def test_drain_empty_queue_is_noop(
        self, redis: AsyncMock, dnd_inactive: DNDChecker
    ) -> None:
        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_inactive)
        redis.llen.return_value = 0
        await dispatcher.drain_deferred()
        redis.lpop.assert_not_called()
