"""Notification delivery worker — consumes dispatch stream and delivers locally.

Each process (conscious, channels) runs this with its own consumer group
so every notification is delivered by all processes via their local adapters.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from core.notifications.channels import ChannelRegistry
from core.notifications.schema import Notification
from core.reflex.runner import ensure_consumer_group
from shared.streams import NOTIFICATION_DISPATCH_STREAM, decode_stream_value

if TYPE_CHECKING:
    from shared.types import AioRedis

logger = logging.getLogger(__name__)


async def notification_delivery_worker(
    redis: AioRedis,
    group: str,
    consumer: str = "worker-1",
    shutdown: asyncio.Event | None = None,
) -> None:
    """Read notifications from dispatch stream and deliver via local adapters.

    Args:
        redis: Async Redis connection.
        group: Consumer group name (unique per process, e.g. "conscious-delivery").
        consumer: Consumer name within the group.
        shutdown: Event to signal graceful shutdown.
    """
    stream = NOTIFICATION_DISPATCH_STREAM
    await ensure_consumer_group(redis, stream, group)

    _shutdown = shutdown or asyncio.Event()

    while not _shutdown.is_set():
        try:
            entries: list[
                tuple[
                    bytes | str,
                    list[tuple[bytes | str, dict[bytes | str, bytes | str]]],
                ]
            ] = await redis.xreadgroup(  # type: ignore[misc,unused-ignore]
                group, consumer, {stream: ">"}, count=5, block=5000
            )

            for _stream_key, stream_entries in entries:
                for entry_id, entry_data in stream_entries:
                    raw = entry_data.get("notification") or entry_data.get(b"notification")
                    if raw is None:
                        await redis.xack(stream, group, entry_id)
                        continue

                    notification = Notification.model_validate_json(decode_stream_value(raw))
                    await _deliver_locally(notification)
                    await redis.xack(stream, group, entry_id)

        except Exception as e:
            if not _shutdown.is_set():
                logger.error("Notification delivery worker error: %s", e)
                await asyncio.sleep(1)


async def _deliver_locally(notification: Notification) -> None:
    """Deliver notification to all matching local channel adapters in parallel."""
    adapters = ChannelRegistry.get_adapters_for_urgency(notification.urgency)
    if not adapters:
        return  # No local adapters for this urgency — other processes will handle it

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
