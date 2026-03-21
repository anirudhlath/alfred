"""DND (Do Not Disturb) state checker.

Checks manual DND (Redis key) first, then calendar meetings.
First match wins. Calendar errors are non-fatal.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from core.notifications.schema import DNDStatus
from shared.streams import DND_STATE_KEY

if TYPE_CHECKING:
    from core.integrations.base import Integration
    from shared.types import AioRedis

logger = logging.getLogger(__name__)


class DNDChecker:
    """Check Do-Not-Disturb state from manual override and calendar."""

    def __init__(
        self,
        redis: AioRedis,
        calendar_adapter: Integration | None,
    ) -> None:
        self._redis = redis
        self._calendar = calendar_adapter

    async def is_active(self) -> DNDStatus:
        """Check DND state. Manual override first, then calendar. First match wins."""
        # 1. Check manual DND
        manual = await self._check_manual()
        if manual.active:
            return manual

        # 2. Check calendar
        if self._calendar is not None:
            calendar = await self._check_calendar()
            if calendar.active:
                return calendar

        return DNDStatus(active=False)

    async def _check_manual(self) -> DNDStatus:
        """Check manual DND state in Redis."""
        raw: bytes | str | None = await self._redis.get(DND_STATE_KEY)
        if raw is None:
            return DNDStatus(active=False)

        data: dict[str, Any] = json.loads(raw)
        if not data.get("active", False):
            return DNDStatus(active=False)

        # Check expiry
        until_str = data.get("until")
        if until_str is not None:
            until = datetime.fromisoformat(until_str)
            if until.tzinfo is None:
                until = until.replace(tzinfo=UTC)
            if datetime.now(UTC) >= until:
                # Expired — clean up
                await self._redis.delete(DND_STATE_KEY)
                logger.info("DND expired, cleaned up state")
                return DNDStatus(active=False)
        else:
            until = None

        return DNDStatus(
            active=True,
            reason=data.get("reason"),
            source=data.get("source", "manual"),
            until=until,
        )

    async def _check_calendar(self) -> DNDStatus:
        """Check if user is in an active calendar meeting."""
        assert self._calendar is not None
        try:
            from core.integrations.base import IntegrationRequest

            result = await self._calendar.execute(
                IntegrationRequest(action="get_today_events", params={})
            )
            events: list[dict[str, Any]] = result.data.get("events", [])
            now = datetime.now(UTC)

            for event in events:
                start = datetime.fromisoformat(event["start"])
                end = datetime.fromisoformat(event["end"])
                if start.tzinfo is None:
                    start = start.replace(tzinfo=UTC)
                if end.tzinfo is None:
                    end = end.replace(tzinfo=UTC)
                if start <= now < end:
                    return DNDStatus(
                        active=True,
                        reason=f"In meeting: {event.get('summary', 'Unknown')}",
                        source="calendar",
                        until=end,
                    )
        except Exception as exc:
            logger.warning("Calendar DND check failed (non-fatal): %s", exc)

        return DNDStatus(active=False)
