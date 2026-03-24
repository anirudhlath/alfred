"""Entry point for the Librarian consolidation agent.

Usage: python -m core.librarian

Runs one consolidation cycle and exits. Intended to be invoked
by a cron job or scheduler, not as a long-running service.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import redis.asyncio as aioredis

from core.librarian.consolidator import Librarian
from core.memory.context_index import ContextIndexManager
from core.memory.embedding_provider import SentenceTransformerProvider
from core.memory.episodic.memory import EpisodicMemory
from core.memory.redis_vector_store import RedisVectorStore
from core.memory.routines.store import RoutineStore
from core.memory.significance import SignificanceScorer
from core.memory.sqlite_vec_store import SqliteVecStore
from shared.config import AlfredConfig
from shared.logging import configure_logging

if TYPE_CHECKING:
    from shared.types import AioRedis

_MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"


async def run() -> None:
    log = configure_logging(service="librarian")
    config = AlfredConfig.from_env()

    r: AioRedis = aioredis.from_url(config.redis_url)

    embedder = SentenceTransformerProvider(config.embedding_model)
    hot_store = RedisVectorStore(redis=r, dim=config.embedding_dim)
    cold_store = SqliteVecStore(
        db_path=str(_MEMORY_DIR / "episodic_cold.db"),
        dim=config.embedding_dim,
    )
    episodic_memory = EpisodicMemory(hot=hot_store, cold=cold_store, embedder=embedder)
    context_index = ContextIndexManager(
        store=hot_store,
        embedder=embedder,
        semantic_dirs=[
            _MEMORY_DIR / "preferences",
            _MEMORY_DIR / "profile",
        ],
    )
    scorer = SignificanceScorer(redis=r, config=config)

    librarian = Librarian(
        redis=r,
        episodic_memory=episodic_memory,
        routine_store=RoutineStore(),
        significance_scorer=scorer,
        context_index=context_index,
        preferences_dir=str(_MEMORY_DIR / "preferences"),
        profile_dir=str(_MEMORY_DIR / "profile"),
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
