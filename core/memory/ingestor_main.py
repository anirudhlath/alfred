"""Entry point for the Memory Ingestor service.

Usage: python -m core.memory.ingestor_main

Lightweight consumer that bridges Reflex observations into episodic memory.
"""

from __future__ import annotations

import asyncio
import signal

from loguru import logger

from core.memory.episodic.memory import EpisodicMemory
from core.memory.ingestor import run_ingestor
from core.memory.paths import episodic_cold_path
from core.memory.redis_vector_store import RedisVectorStore
from core.memory.significance import SignificanceScorer
from core.memory.sqlite_vec_store import SqliteVecStore
from core.warmup import start_warmup
from shared.config import AlfredConfig
from shared.logging import configure_logging
from shared.redis_streams import create_redis

_shutdown = asyncio.Event()


def _handle_signal() -> None:
    logger.info("Memory Ingestor shutdown signal received")
    _shutdown.set()


async def run(config: AlfredConfig) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    r = create_redis(config.redis_url)

    # The factory imports the concrete backend lazily (torch only on the in-process path).
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

    try:
        await run_ingestor(r, episodic, scorer, shutdown_event=_shutdown)
    finally:
        warmup_task.cancel()
        # No-op for the in-process backend; releases the HTTP backend's pool.
        await embedder.aclose()
        await r.aclose()


def main() -> None:
    configure_logging(service="memory-ingestor")
    config = AlfredConfig.from_env()
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
