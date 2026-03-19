"""Entry point for the Librarian consolidation agent.

Usage: python -m core.librarian

Runs one consolidation cycle and exits. Intended to be invoked
by a cron job or scheduler, not as a long-running service.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import redis.asyncio as aioredis

from core.librarian.consolidator import Librarian
from core.memory.episodic.store import EpisodicStore
from core.memory.routines.store import RoutineStore
from shared.config import AlfredConfig
from shared.logging import configure_logging

if TYPE_CHECKING:
    from core.reflex.runner import AioRedis


async def run() -> None:
    log = configure_logging(service="librarian")
    config = AlfredConfig.from_env()

    r: AioRedis = aioredis.from_url(config.redis_url)

    librarian = Librarian(
        redis=r,
        episodic_store=EpisodicStore(redis=r),
        routine_store=RoutineStore(),
        claude_api_key=config.claude_api_key,
        claude_model=config.claude_model,
    )

    try:
        result = await librarian.consolidate()
        log.info("Librarian finished: %s", result)
    finally:
        await r.aclose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
