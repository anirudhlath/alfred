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
from core.memory.embedding_backend import build_embedding_provider
from core.memory.episodic.memory import EpisodicMemory
from core.memory.paths import episodic_cold_path, preferences_dir, profile_dir
from core.memory.redis_vector_store import RedisVectorStore
from core.memory.routines.store import RoutineStore
from core.memory.significance import SignificanceScorer
from core.memory.sqlite_vec_store import SqliteVecStore
from core.shutdown import teardown
from shared.config import AlfredConfig
from shared.logging import configure_logging
from shared.otel import init_tracing
from shared.redis_streams import create_redis

if TYPE_CHECKING:
    from shared.types import AioRedis


async def run() -> None:
    log = configure_logging(service="librarian")
    config = AlfredConfig.from_env()
    init_tracing(
        service_name="librarian",
        endpoint=config.otel_endpoint if config.signoz_enabled else None,
    )

    r: AioRedis = create_redis(config.redis_url)

    embedder = None
    context_index = None
    episodic_memory = None
    scorer = None
    try:
        embedder = build_embedding_provider(config)
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
            conflict_min_observations=config.conflict_min_observations,
            conflict_min_days=config.conflict_min_days,
            decay_migration_threshold=config.decay_migration_threshold,
            pattern_min_occurrences=config.pattern_min_occurrences,
            pattern_min_days=config.pattern_min_days,
            pattern_confidence_threshold=config.pattern_confidence_threshold,
            routine_decay_per_cycle=config.routine_decay_per_cycle,
            routine_archive_threshold=config.routine_archive_threshold,
            routine_suggestion_cooldown_hours=config.routine_suggestion_cooldown_hours,
        )
        result = await librarian.consolidate()
        log.info("Librarian finished: %s", result)
    finally:
        # This process runs one cycle and exits, so a pool left open here is leaked on
        # every single invocation.
        await teardown(
            closers={
                "embedding provider": embedder.aclose if embedder is not None else None,
                "redis": r.aclose,
            }
        )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
