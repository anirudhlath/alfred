"""Periodic scheduler for Librarian consolidation cycles."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.librarian.consolidator import Librarian

logger = logging.getLogger(__name__)


class LibrarianScheduler:
    """Runs Librarian.consolidate() on a periodic interval."""

    def __init__(
        self,
        librarian: Librarian,
        interval_seconds: float = 3600.0,
    ) -> None:
        self._librarian = librarian
        self._interval = interval_seconds

    async def run(self) -> None:
        """Run consolidation cycles forever until cancelled."""
        logger.info("Librarian scheduler started (interval=%ds)", int(self._interval))
        while True:
            try:
                result = await self._librarian.consolidate()
                logger.info("Librarian cycle complete: %s", result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Librarian consolidation failed: %s", exc)

            await asyncio.sleep(self._interval)
