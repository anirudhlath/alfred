"""NotificationPublisher — sends notifications to the delivery stream."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shared.streams import NOTIFICATIONS_STREAM

if TYPE_CHECKING:
    from shared.types import AioRedis

logger = logging.getLogger(__name__)


class NotificationPublisher:
    """Publishes notification events to the Redis notifications stream.

    Downstream consumers (Signal bridge, web push, etc.) read from this stream.
    """

    def __init__(self, redis: AioRedis) -> None:
        self._redis = redis

    async def publish(
        self,
        channel: str,
        title: str,
        body: str,
        urgency: str = "normal",
    ) -> None:
        """Publish a notification event."""
        event = {
            "channel": channel,
            "title": title,
            "body": body,
            "urgency": urgency,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        await self._redis.xadd(NOTIFICATIONS_STREAM, {"event": json.dumps(event)})
        logger.info("Published notification: %s — %s", channel, title)
