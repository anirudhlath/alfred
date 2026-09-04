"""Entry point for the Memory Ingestor service.

Usage: python -m core.memory.ingestor_main

Lightweight consumer that bridges Reflex observations into episodic memory.
"""

from __future__ import annotations

import asyncio
import signal
from typing import TYPE_CHECKING

from loguru import logger

from core.memory.episodic.memory import EpisodicMemory
from core.memory.ingestor import run_ingestor
from core.memory.paths import episodic_cold_path
from core.memory.redis_vector_store import RedisVectorStore
from core.memory.significance import SignificanceScorer
from core.memory.sqlite_vec_store import SqliteVecStore
from core.shutdown import close_all, drain_tasks
from core.warmup import start_warmup
from shared.config import AlfredConfig
from shared.logging import configure_logging
from shared.redis_streams import create_redis

if TYPE_CHECKING:
    from core.memory.embedding_provider import EmbeddingProvider

_shutdown = asyncio.Event()


def _handle_signal() -> None:
    logger.info("Memory Ingestor shutdown signal received")
    _shutdown.set()


async def run(config: AlfredConfig) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    r = create_redis(config.redis_url)
    # Construction lives inside the try because it can now fail — a rejected
    # EMBEDDING_BACKEND, an unreachable cold-store path — and until it did, such an
    # exception escaped this function with the Redis client above still open.
    embedder: EmbeddingProvider | None = None
    warmup_task: asyncio.Task[None] | None = None
    try:
        # The factory imports the concrete backend lazily (torch only in-process).
        from core.memory.embedding_backend import build_embedding_provider

        embedder = build_embedding_provider(config)

        hot = RedisVectorStore(redis=r, dim=config.embedding_dim)
        cold = SqliteVecStore(
            db_path=str(episodic_cold_path()),
            dim=config.embedding_dim,
        )
        episodic = EpisodicMemory(hot=hot, cold=cold, embedder=embedder)
        scorer = SignificanceScorer(redis=r, config=config)

        # Load memory components in the background — the first observation then
        # skips the embedding-model lazy-load hit.
        warmup_task = start_warmup(
            "memory-ingestor",
            {
                "embedding model": embedder.warmup,
                "redis vector index": hot.ensure_index,
                "sqlite cold store": cold._get_db,
            },
        )

        await run_ingestor(r, episodic, scorer, shutdown_event=_shutdown)
    finally:
        # Drained before closing: the warmup task holds the provider, so cancelling
        # without waiting can close the pool out from under an in-flight embed.
        await drain_tasks(warmup_task)
        await close_all(
            {
                "embedding provider": embedder.aclose if embedder is not None else None,
                "redis": r.aclose,
            }
        )


def main() -> None:
    configure_logging(service="memory-ingestor")
    config = AlfredConfig.from_env()
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
