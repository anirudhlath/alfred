"""NotificationDispatcher — deterministic routing with DND awareness.

No LLM calls. Checks DND → defers or routes to channels by urgency.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.notifications.channels import ChannelRegistry
from core.notifications.schema import Notification, Urgency
from shared.streams import DEFERRED_NOTIFICATIONS_KEY

if TYPE_CHECKING:
    from core.notifications.dnd import DNDChecker
    from core.triggers.store import TriggerStore
    from shared.types import AioRedis

logger = logging.getLogger(__name__)


class NotificationDispatcher:
    """Route notifications to channels, respecting DND state."""

    def __init__(
        self,
        redis: AioRedis,
        dnd_checker: DNDChecker,
        trigger_store: TriggerStore | None = None,
    ) -> None:
        self._redis = redis
        self._dnd = dnd_checker
        self._trigger_store = trigger_store

    async def dispatch(self, notification: Notification) -> None:
        """Route a notification to appropriate channels, respecting DND."""
        dnd_status = await self._dnd.is_active()

        if dnd_status.active and notification.urgency != Urgency.URGENT:
            # Defer non-urgent notifications during DND
            await self._redis.rpush(  # type: ignore[misc]
                DEFERRED_NOTIFICATIONS_KEY,
                notification.model_dump_json(),
            )
            logger.info(
                "Deferred notification '%s' (urgency=%s, DND source=%s)",
                notification.title,
                notification.urgency,
                dnd_status.source,
            )
            # Schedule a drain trigger for when DND expires
            if dnd_status.until is not None:
                await self._ensure_drain_trigger(dnd_status.until)
            return

        await self._deliver(notification)

    async def drain_deferred(self) -> None:
        """Drain all deferred notifications through the dispatcher.

        Called when DND expires (via time trigger). Each notification is
        re-dispatched — if DND is still somehow active, it will re-defer.
        """
        count: int = await self._redis.llen(DEFERRED_NOTIFICATIONS_KEY)  # type: ignore[misc]
        if count == 0:
            return

        logger.info("Draining %d deferred notifications", count)
        while True:
            raw: bytes | str | None = await self._redis.lpop(  # type: ignore[misc]
                DEFERRED_NOTIFICATIONS_KEY
            )
            if raw is None:
                break
            notification = Notification.model_validate_json(raw)
            await self._deliver(notification)

    async def _ensure_drain_trigger(self, drain_at: datetime) -> None:
        """Create a one-shot time trigger to drain deferred notifications.

        Idempotent: skips if a drain trigger for this time already exists.
        """
        if self._trigger_store is None:
            logger.debug("No trigger store — skipping drain trigger creation")
            return

        trigger_id = f"drain-deferred-{int(drain_at.timestamp())}"
        existing = await self._trigger_store.get(trigger_id)
        if existing is not None:
            return  # Already scheduled

        from core.triggers.models import ActionPayload
        from core.triggers.types.time import TimeTrigger

        trigger = TimeTrigger(
            trigger_id=trigger_id,
            name=f"Drain deferred notifications at {drain_at.isoformat()}",
            one_shot=True,
            created_by="notification-dispatcher",
            created_at=datetime.now(UTC),
            action=ActionPayload(
                target_service="conscious-engine",
                tool_name="drain_deferred_notifications",
                parameters={},
            ),
            conditions=TimeTrigger.Conditions(run_at=drain_at),
        )
        await self._trigger_store.save(trigger)
        logger.info("Created drain trigger %s for %s", trigger_id, drain_at)

    async def _deliver(self, notification: Notification) -> None:
        """Deliver notification to all matching channel adapters in parallel."""
        adapters = ChannelRegistry.get_adapters_for_urgency(notification.urgency)
        if not adapters:
            logger.warning("No channel adapters registered for urgency=%s", notification.urgency)
            return

        results = await asyncio.gather(
            *(adapter.deliver(notification) for adapter in adapters),
            return_exceptions=True,
        )
        for adapter, result in zip(adapters, results, strict=True):
            if isinstance(result, Exception):
                logger.error(
                    "Channel %s failed to deliver '%s': %s",
                    type(adapter).name,
                    notification.title,
                    result,
                )
            else:
                logger.info("Delivered '%s' via %s", notification.title, type(adapter).name)
