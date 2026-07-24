"""Entry point for the Librarian consolidation agent.

Usage: python -m core.librarian

Runs one consolidation cycle and exits. Intended to be invoked
by a cron job or scheduler, not as a long-running service.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.librarian.consolidator import Librarian
from core.memory.context_index import ContextIndexManager
from core.memory.embedding_provider import SentenceTransformerProvider
from core.memory.episodic.memory import EpisodicMemory
from core.memory.paths import episodic_cold_path, preferences_dir, profile_dir
from core.memory.redis_vector_store import RedisVectorStore
from core.memory.routines.store import RoutineStore
from core.memory.significance import SignificanceScorer
from core.memory.sqlite_vec_store import SqliteVecStore
from shared.config import AlfredConfig
from shared.logging import configure_logging
from shared.redis_streams import create_redis

if TYPE_CHECKING:
    from shared.types import AioRedis


async def run() -> None:
    log = configure_logging(service="librarian")
    config = AlfredConfig.from_env()

    r: AioRedis = create_redis(config.redis_url)

    embedder = None
    context_index = None
    episodic_memory = None
    scorer = None
    try:
        embedder = SentenceTransformerProvider(config.embedding_model)
        hot_store = RedisVectorStore(redis=r, dim=config.embedding_dim)
        cold_store = SqliteVecStore(
            db_path=str(episodic_cold_path()),
            dim=config.embedding_dim,
        )
        episodic_memory = EpisodicMemory(hot=hot_store, cold=cold_store, embedder=embedder)
        context_index = ContextIndexManager(
            store=hot_store,
            embedder=embedder,
            semantic_dirs=[
                preferences_dir(),
                profile_dir(),
            ],
        )
        scorer = SignificanceScorer(redis=r, config=config)
        log.info(
            "Memory system initialized (model=%s, dim=%d)",
            config.embedding_model,
            config.embedding_dim,
        )
    except Exception as exc:
        log.error("Memory system failed to initialize — running without memory: %s", exc)

    try:
        if episodic_memory is None or context_index is None or scorer is None:
            log.warning("Librarian cannot run — memory system unavailable")
            return

        librarian = Librarian(
            redis=r,
            episodic_memory=episodic_memory,
            routine_store=RoutineStore(),
            significance_scorer=scorer,
            context_index=context_index,
            claude_api_key=config.claude_api_key,
            claude_model=config.claude_model,
        )
        result = await librarian.consolidate()
        log.info("Librarian finished: %s", result)
    finally:
        await r.aclose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
