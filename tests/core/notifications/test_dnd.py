"""Tests for DNDChecker — manual DND + calendar-based DND."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.notifications.dnd import DNDChecker
from shared.streams import DND_STATE_KEY


@pytest.fixture
def redis() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def checker(redis: AsyncMock) -> DNDChecker:
    return DNDChecker(redis=redis, calendar_adapter=None)


class TestManualDND:
    @pytest.mark.asyncio
    async def test_no_dnd_state_returns_inactive(self, checker: DNDChecker, redis: AsyncMock) -> None:
        redis.get.return_value = None
        status = await checker.is_active()
        assert not status.active

    @pytest.mark.asyncio
    async def test_active_dnd_returns_active(self, checker: DNDChecker, redis: AsyncMock) -> None:
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        redis.get.return_value = json.dumps({
            "active": True,
            "until": future,
            "reason": "User requested",
            "source": "manual",
        })
        status = await checker.is_active()
        assert status.active
        assert status.source == "manual"

    @pytest.mark.asyncio
    async def test_expired_dnd_returns_inactive_and_cleans_up(
        self, checker: DNDChecker, redis: AsyncMock
    ) -> None:
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        redis.get.return_value = json.dumps({
            "active": True,
            "until": past,
            "reason": "User requested",
            "source": "manual",
        })
        status = await checker.is_active()
        assert not status.active
        redis.delete.assert_called_once_with(DND_STATE_KEY)

    @pytest.mark.asyncio
    async def test_active_dnd_no_expiry(self, checker: DNDChecker, redis: AsyncMock) -> None:
        """DND with no 'until' field stays active indefinitely."""
        redis.get.return_value = json.dumps({
            "active": True,
            "reason": "Hold my calls",
            "source": "manual",
        })
        status = await checker.is_active()
        assert status.active


class TestCalendarDND:
    @pytest.mark.asyncio
    async def test_calendar_meeting_active(self, redis: AsyncMock) -> None:
        now = datetime.now(UTC)
        calendar = MagicMock()
        calendar.execute = AsyncMock(return_value=MagicMock(data={
            "events": [
                {
                    "summary": "Team standup",
                    "start": (now - timedelta(minutes=10)).isoformat(),
                    "end": (now + timedelta(minutes=20)).isoformat(),
                }
            ]
        }))
        checker = DNDChecker(redis=redis, calendar_adapter=calendar)
        redis.get.return_value = None  # No manual DND

        status = await checker.is_active()
        assert status.active
        assert status.source == "calendar"

    @pytest.mark.asyncio
    async def test_no_active_meeting(self, redis: AsyncMock) -> None:
        now = datetime.now(UTC)
        calendar = MagicMock()
        calendar.execute = AsyncMock(return_value=MagicMock(data={
            "events": [
                {
                    "summary": "Past meeting",
                    "start": (now - timedelta(hours=2)).isoformat(),
                    "end": (now - timedelta(hours=1)).isoformat(),
                }
            ]
        }))
        checker = DNDChecker(redis=redis, calendar_adapter=calendar)
        redis.get.return_value = None

        status = await checker.is_active()
        assert not status.active

    @pytest.mark.asyncio
    async def test_calendar_error_falls_through(self, redis: AsyncMock) -> None:
        """Calendar failure should not block notifications."""
        calendar = MagicMock()
        calendar.execute = AsyncMock(side_effect=Exception("CalDAV down"))
        checker = DNDChecker(redis=redis, calendar_adapter=calendar)
        redis.get.return_value = None

        status = await checker.is_active()
        assert not status.active

    @pytest.mark.asyncio
    async def test_manual_dnd_takes_priority_over_calendar(self, redis: AsyncMock) -> None:
        """Manual DND is checked first — calendar is skipped if manual is active."""
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        redis.get.return_value = json.dumps({
            "active": True,
            "until": future,
            "reason": "User requested",
            "source": "manual",
        })
        calendar = MagicMock()
        calendar.execute = AsyncMock()
        checker = DNDChecker(redis=redis, calendar_adapter=calendar)

        status = await checker.is_active()
        assert status.active
        assert status.source == "manual"
        # Calendar should NOT have been called
        calendar.execute.assert_not_called()
