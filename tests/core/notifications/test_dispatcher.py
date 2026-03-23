"""Tests for NotificationDispatcher — routing, deferral, and drain logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from core.notifications.dispatcher import NotificationDispatcher
from core.notifications.dnd import DNDChecker
from core.notifications.schema import DNDStatus, Notification, Urgency
from shared.streams import DEFERRED_NOTIFICATIONS_KEY, NOTIFICATION_DISPATCH_STREAM


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
    async def test_no_dnd_publishes_to_dispatch_stream(
        self, redis: AsyncMock, dnd_inactive: DNDChecker
    ) -> None:
        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_inactive)
        await dispatcher.dispatch(_make_notification(Urgency.INFORMATIONAL))

        redis.xadd.assert_called_once()
        call_args = redis.xadd.call_args[0]
        assert call_args[0] == NOTIFICATION_DISPATCH_STREAM
        assert "notification" in call_args[1]

    @pytest.mark.asyncio
    async def test_notification_json_in_stream(
        self, redis: AsyncMock, dnd_inactive: DNDChecker
    ) -> None:
        notification = _make_notification(Urgency.IMPORTANT)
        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_inactive)
        await dispatcher.dispatch(notification)

        payload = redis.xadd.call_args[0][1]
        restored = Notification.model_validate_json(payload["notification"])
        assert restored.title == notification.title
        assert restored.urgency is Urgency.IMPORTANT


class TestDNDDeferral:
    @pytest.mark.asyncio
    async def test_dnd_active_defers_informational(
        self, redis: AsyncMock, dnd_active: DNDChecker
    ) -> None:
        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_active)
        await dispatcher.dispatch(_make_notification(Urgency.INFORMATIONAL))

        # Should defer, not publish to dispatch stream
        redis.rpush.assert_called_once()
        call_args = redis.rpush.call_args[0]
        assert call_args[0] == DEFERRED_NOTIFICATIONS_KEY
        redis.xadd.assert_not_called()

    @pytest.mark.asyncio
    async def test_dnd_active_delivers_urgent(
        self, redis: AsyncMock, dnd_active: DNDChecker
    ) -> None:
        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_active)
        await dispatcher.dispatch(_make_notification(Urgency.URGENT))

        # Urgent bypasses DND — published to stream
        redis.xadd.assert_called_once()
        redis.rpush.assert_not_called()


class TestDrainDeferred:
    @pytest.mark.asyncio
    async def test_drain_publishes_to_stream(
        self, redis: AsyncMock, dnd_inactive: DNDChecker
    ) -> None:
        notification = _make_notification(Urgency.INFORMATIONAL)
        serialized = notification.model_dump_json()

        redis.llen.return_value = 1
        redis.lpop.side_effect = [serialized.encode(), None]

        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_inactive)
        await dispatcher.drain_deferred()

        redis.xadd.assert_called_once()
        call_args = redis.xadd.call_args[0]
        assert call_args[0] == NOTIFICATION_DISPATCH_STREAM

    @pytest.mark.asyncio
    async def test_drain_empty_queue_is_noop(
        self, redis: AsyncMock, dnd_inactive: DNDChecker
    ) -> None:
        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=dnd_inactive)
        redis.llen.return_value = 0
        await dispatcher.drain_deferred()
        redis.lpop.assert_not_called()


class TestEnsureDrainTrigger:
    @pytest.mark.asyncio
    async def test_creates_one_shot_time_trigger(self, redis: AsyncMock) -> None:
        """Verify drain trigger has correct ID, one_shot, target_service, tool_name."""
        dnd_until = datetime.now(UTC) + timedelta(hours=1)
        checker = AsyncMock(spec=DNDChecker)
        checker.is_active.return_value = DNDStatus(
            active=True, source="manual", reason="Hold", until=dnd_until
        )

        trigger_store = AsyncMock()
        trigger_store.get.return_value = None

        dispatcher = NotificationDispatcher(
            redis=redis, dnd_checker=checker, trigger_store=trigger_store
        )
        await dispatcher.dispatch(_make_notification(Urgency.INFORMATIONAL))

        trigger_store.save.assert_called_once()
        saved_trigger = trigger_store.save.call_args[0][0]

        expected_id = f"drain-deferred-{int(dnd_until.timestamp())}"
        assert saved_trigger.trigger_id == expected_id
        assert saved_trigger.one_shot is True
        assert saved_trigger.action is not None
        assert saved_trigger.action.target_service == "conscious-engine"
        assert saved_trigger.action.tool_name == "drain_deferred_notifications"

    @pytest.mark.asyncio
    async def test_skips_if_trigger_already_exists(self, redis: AsyncMock) -> None:
        dnd_until = datetime.now(UTC) + timedelta(hours=1)
        checker = AsyncMock(spec=DNDChecker)
        checker.is_active.return_value = DNDStatus(
            active=True, source="manual", reason="Hold", until=dnd_until
        )

        trigger_store = AsyncMock()
        trigger_store.get.return_value = "existing"

        dispatcher = NotificationDispatcher(
            redis=redis, dnd_checker=checker, trigger_store=trigger_store
        )
        await dispatcher.dispatch(_make_notification(Urgency.INFORMATIONAL))

        trigger_store.save.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_trigger_without_store(self, redis: AsyncMock) -> None:
        dnd_until = datetime.now(UTC) + timedelta(hours=1)
        checker = AsyncMock(spec=DNDChecker)
        checker.is_active.return_value = DNDStatus(
            active=True, source="manual", reason="Hold", until=dnd_until
        )

        dispatcher = NotificationDispatcher(redis=redis, dnd_checker=checker)
        await dispatcher.dispatch(_make_notification(Urgency.INFORMATIONAL))

    @pytest.mark.asyncio
    async def test_no_trigger_for_indefinite_dnd(self, redis: AsyncMock) -> None:
        checker = AsyncMock(spec=DNDChecker)
        checker.is_active.return_value = DNDStatus(active=True, source="manual", reason="Hold")

        trigger_store = AsyncMock()

        dispatcher = NotificationDispatcher(
            redis=redis, dnd_checker=checker, trigger_store=trigger_store
        )
        await dispatcher.dispatch(_make_notification(Urgency.INFORMATIONAL))

        trigger_store.get.assert_not_called()
        trigger_store.save.assert_not_called()
