"""Scratchpad async writer.

Drains observations from a Redis List (alfred:scratchpad:queue) and appends
them to scratchpad.md. This serializes all scratchpad writes through a single
coroutine, preventing concurrent file corruption.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ScratchpadWriter:
    """Serialized writer for the scratchpad file."""

    def __init__(
        self,
        redis: Any,
        queue_key: str = "alfred:scratchpad:queue",
        scratchpad_path: str = "core/memory/scratchpad.md",
    ) -> None:
        self.redis = redis
        self.queue_key = queue_key
        self.scratchpad_path = scratchpad_path

    async def drain_once(self) -> int:
        """Drain all pending entries from the Redis List to the scratchpad file."""
        entries: list[str] = []
        while True:
            entry = await self.redis.lpop(self.queue_key)
            if entry is None:
                break
            if isinstance(entry, bytes):
                entry = entry.decode()
            entries.append(entry)

        if not entries:
            return 0

        with open(self.scratchpad_path, "a") as f:
            for entry in entries:
                f.write(f"\n{entry}")

        logger.info("Drained %d entries to scratchpad", len(entries))
        return len(entries)

    async def run(self, interval_seconds: float = 5.0) -> None:
        """Run the writer loop, draining the queue at regular intervals."""
        logger.info("Scratchpad writer started (interval: %.1fs)", interval_seconds)
        while True:
            try:
                await self.drain_once()
            except Exception as e:
                logger.error("Scratchpad drain error: %s", e)
            await asyncio.sleep(interval_seconds)
