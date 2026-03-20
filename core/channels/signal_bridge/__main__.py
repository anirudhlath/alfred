"""Entry point for the Signal bridge service.

Usage: python -m core.channels.signal_bridge
"""

from __future__ import annotations

import asyncio
import signal
from typing import TYPE_CHECKING

import redis.asyncio as aioredis

from core.channels.signal_bridge.bridge import SignalBridge
from shared.config import AlfredConfig
from shared.logging import configure_logging

if TYPE_CHECKING:
    from shared.types import AioRedis

_shutdown = asyncio.Event()


def _handle_signal() -> None:
    _shutdown.set()


async def run(config: AlfredConfig) -> None:
    log = configure_logging(service="signal-bridge")

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    r: AioRedis = aioredis.from_url(config.redis_url)

    bridge = SignalBridge(redis=r, phone_number=config.signal_phone_number)
    await bridge.ensure_consumer_group()

    log.info("Signal bridge started")

    response_last_id = "$"

    try:
        while not _shutdown.is_set():
            # Poll notifications (cost alerts, proactive) and responses in parallel
            response_last_id = await bridge.poll_responses(last_id=response_last_id)
            await bridge.poll_notifications()
    finally:
        log.info("Shutting down Signal bridge...")
        await r.aclose()


def main() -> None:
    config = AlfredConfig.from_env()
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
