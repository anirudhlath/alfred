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

_DRAIN_BATCH_SIZE = 1000


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
        """Drain pending entries from the Redis List to the scratchpad file.

        Drains up to _DRAIN_BATCH_SIZE entries per call to bound memory usage.
        """
        # Try batch LPOP first (Redis 6.2+), fall back to single pops
        raw_entries: list[bytes | str] = []
        try:
            batch = await self.redis.lpop(self.queue_key, _DRAIN_BATCH_SIZE)
            if batch is not None:
                raw_entries = batch if isinstance(batch, list) else [batch]
        except TypeError:
            # Redis client doesn't support count arg — fall back to single pops
            while len(raw_entries) < _DRAIN_BATCH_SIZE:
                entry = await self.redis.lpop(self.queue_key)
                if entry is None:
                    break
                raw_entries.append(entry)

        if not raw_entries:
            return 0

        entries = [e.decode() if isinstance(e, bytes) else e for e in raw_entries]

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
