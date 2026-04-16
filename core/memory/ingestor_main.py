"""Entry point for the Memory Ingestor service.

Usage: python -m core.memory.ingestor_main

Lightweight consumer that bridges Reflex observations into episodic memory.
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

import redis.asyncio as aioredis
from loguru import logger

from core.memory.episodic.memory import EpisodicMemory
from core.memory.ingestor import run_ingestor
from core.memory.redis_vector_store import RedisVectorStore
from core.memory.significance import SignificanceScorer
from core.memory.sqlite_vec_store import SqliteVecStore
from shared.config import AlfredConfig
from shared.logging import configure_logging

_shutdown = asyncio.Event()


def _handle_signal() -> None:
    logger.info("Memory Ingestor shutdown signal received")
    _shutdown.set()


async def run(config: AlfredConfig) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    r = aioredis.from_url(config.redis_url)

    # Lazy-load embedding provider
    from core.memory.embedding_provider import SentenceTransformerProvider

    embedder = SentenceTransformerProvider(config.embedding_model)

    memory_dir = Path(__file__).resolve().parent
    hot = RedisVectorStore(redis=r, dim=config.embedding_dim)
    cold = SqliteVecStore(
        db_path=str(memory_dir / "episodic_cold.db"),
        dim=config.embedding_dim,
    )
    episodic = EpisodicMemory(hot=hot, cold=cold, embedder=embedder)
    scorer = SignificanceScorer(redis=r, config=config)

    try:
        await run_ingestor(r, episodic, scorer, shutdown_event=_shutdown)
    finally:
        await r.aclose()


def main() -> None:
    configure_logging(service="memory-ingestor")
    config = AlfredConfig.from_env()
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
