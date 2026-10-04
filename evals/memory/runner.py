"""Replays the dataset hour by hour against each policy, measuring as it goes."""

from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from core.memory.ingestor import ingest_observation
from core.memory.schemas import EpisodicEntry, SignificanceScore
from evals.memory.env import CountingEmbedder, open_env, scan_hot
from evals.memory.metrics import EligibilityTracker, checkpoint, day_snapshot
from evals.memory.models import MemoryEvalRun, PassRecord, PolicyResult, RunSettings
from evals.memory.policies import policy_factory
from evals.store import build_run_id
from shared.streams import CONTEXT_PREFIX

if TYPE_CHECKING:
    from collections.abc import Callable

    from core.memory.embedding_provider import EmbeddingProvider
    from evals.memory.dataset import Dataset, Retrieval, SimMemory
    from evals.memory.env import SimEnv
    from evals.memory.policies import DecayPolicy
    from evals.memory.sandbox import RedisSandbox
    from shared.config import AlfredConfig

logger = logging.getLogger(__name__)

# The simulated clock runs in UTC (``SIM_START``), so the stamps in stored text do too.
_SIM_ZONE = ZoneInfo("UTC")


async def write_memory(env: SimEnv, memory: SimMemory) -> None:
    """The production write path: the Memory Ingestor, or the Librarian's episodic write."""
    if memory.observation is not None:
        await ingest_observation(
            memory.observation, env.episodic, env.scorer, env.passive_scorer, tz=_SIM_ZONE
        )
        return
    entry = EpisodicEntry(
        id=memory.id,
        timestamp=memory.at,
        source=memory.source,
        summary=memory.summary,
        entities=list(memory.entities),
        significance=SignificanceScore(overall=0.0),  # placeholder, scored below
    )
    await env.episodic.write(entry, await env.scorer.score(entry))


async def apply_retrieval(env: SimEnv, retrieval: Retrieval) -> bool:
    """Sir recalls a memory: bump its stats at the simulated time, if it is still hot.

    Deliberate recall only ever stamps hot results (``record_retrievals``), so a
    memory that already left hot gains nothing — the retrieval is lost.
    """
    if not await env.hot.exists(retrieval.target_id):
        return False
    raw = await env.redis.hget(f"{CONTEXT_PREFIX}{retrieval.target_id}", "retrieval_count")
    count = int(raw or 0)
    await env.hot.update_metadata(
        retrieval.target_id,
        {"retrieval_count": count + 1, "last_retrieved": retrieval.at.timestamp()},
    )
    return True


async def seed_index(env: SimEnv, dataset: Dataset) -> None:
    """Semantic sections and routines, indexed the way the Librarian indexes them."""
    for filename, text in dataset.semantic_files.items():
        directory = "profile" if filename.startswith("profile") else "preferences"
        (env.workdir / directory / filename).write_text(text)
    await env.context_index.reindex_semantic_files()
    for routine in dataset.routines:
        env.routine_store.save(routine)
    await env.librarian()._reindex_routines()


async def _timed_pass(
    policy: DecayPolicy, embedder: CountingEmbedder, now: datetime, hour: int
) -> PassRecord:
    embedder.phase = "decay"
    calls, seconds = embedder.calls["decay"], embedder.seconds["decay"]
    started = time.perf_counter()
    stats = await policy.run_pass(now)
    wall = time.perf_counter() - started
    return PassRecord(
        hour=hour,
        wall_ms=wall * 1000,
        embed_calls=embedder.calls["decay"] - calls,
        embed_ms=(embedder.seconds["decay"] - seconds) * 1000,
        migrated=stats.migrated,
        candidates=stats.candidates,
        candidates_episodic=stats.candidates_episodic,
        candidates_eligible=stats.candidates_eligible,
    )


async def simulate(
    env: SimEnv,
    dataset: Dataset,
    policy_name: str,
    settings: RunSettings,
    progress: Callable[[str], None] = logger.info,
) -> PolicyResult:
    """Run one policy over the whole timeline inside an already-open environment."""
    embedder = env.embedder
    embedder.reset_counts()
    embedder.phase = "setup"
    await seed_index(env, dataset)
    policy = policy_factory(policy_name)(env, settings.threshold)

    target_ids = {p.target_id for p in dataset.probes}
    target_content: dict[str, str] = {}
    tracker = EligibilityTracker()
    retrievals: Counter[str] = Counter()
    result = PolicyResult(
        name=policy.name,
        summary=policy.summary,
        threshold=policy.threshold,
        wall_seconds=0.0,
        write_embed_calls=0,
        decay_embed_calls=0,
        decay_embed_seconds=0.0,
        suppressed_stat_writes=0,
        passes=[],
        days=[],
        checkpoints=[],
    )
    started = time.perf_counter()
    memory_index = retrieval_index = 0
    for hour in range(dataset.days * 24):
        hour_end = dataset.start + timedelta(hours=hour + 1)

        embedder.phase = "write"
        while memory_index < len(dataset.memories) and dataset.memories[memory_index].at < hour_end:
            memory = dataset.memories[memory_index]
            await write_memory(env, memory)
            if memory.id in target_ids:
                raw = await env.redis.hget(f"{CONTEXT_PREFIX}{memory.id}", "content")
                target_content[memory.id] = raw.decode() if isinstance(raw, bytes) else str(raw)
            memory_index += 1
        while (
            retrieval_index < len(dataset.retrievals)
            and dataset.retrievals[retrieval_index].at < hour_end
        ):
            applied = await apply_retrieval(env, dataset.retrievals[retrieval_index])
            retrievals["applied" if applied else "lost"] += 1
            retrieval_index += 1

        if (hour + 1) % settings.pass_every_hours == 0:
            result.passes.append(await _timed_pass(policy, embedder, hour_end, hour))

        if (hour + 1) % 24 == 0:
            embedder.phase = "measure"
            # Eligibility is judged at the configured threshold for every policy, so
            # no_decay reports the backlog a working pass would have moved.
            snapshot = await day_snapshot(
                env, dataset, await scan_hot(env.redis), hour_end, settings.threshold, tracker
            )
            result.days.append(snapshot)
            if snapshot.day in settings.measure_days:
                embedder.phase = "probe"
                result.checkpoints.append(
                    await checkpoint(
                        env,
                        dataset,
                        hour_end,
                        snapshot,
                        tracker,
                        settings.threshold,
                        target_content,
                        retrievals,
                    )
                )
            progress(
                f"[{policy.name}] day {snapshot.day:>2}: hot={snapshot.hot_episodic} "
                f"cold={snapshot.cold_entries} eligible={snapshot.eligible_in_hot} "
                f"migrated={sum(p.migrated for p in result.passes)} "
                f"({time.perf_counter() - started:.0f}s)"
            )

    result.wall_seconds = time.perf_counter() - started
    result.write_embed_calls = embedder.calls["write"]
    result.decay_embed_calls = embedder.calls["decay"]
    result.decay_embed_seconds = embedder.seconds["decay"]
    result.suppressed_stat_writes = env.recall_view_store.suppressed_writes
    return result


async def run_policy(
    policy_name: str,
    dataset: Dataset,
    embedder: CountingEmbedder,
    config: AlfredConfig,
    settings: RunSettings,
    sandbox: RedisSandbox,
    progress: Callable[[str], None] = logger.info,
) -> PolicyResult:
    """One policy, one fresh Redis, one temp dir for the cold store."""
    async with sandbox as redis:
        settings.redis_version = sandbox.version
        with TemporaryDirectory(prefix="alfred-memory-eval-") as workdir:
            env = open_env(config, redis, embedder, Path(workdir))
            try:
                return await simulate(env, dataset, policy_name, settings, progress)
            finally:
                await env.close()


async def run_eval(
    dataset: Dataset,
    policies: list[str],
    provider: EmbeddingProvider,
    config: AlfredConfig,
    settings: RunSettings,
    sandbox_factory: Callable[[], RedisSandbox],
    progress: Callable[[str], None] = logger.info,
) -> MemoryEvalRun:
    """Every requested policy, sequentially, sharing one embedding model."""
    embedder = CountingEmbedder(provider)
    results = [
        await run_policy(name, dataset, embedder, config, settings, sandbox_factory(), progress)
        for name in policies
    ]
    timestamp = datetime.now(UTC)
    counts = Counter(str(m.category) for m in dataset.memories)
    return MemoryEvalRun(
        run_id=build_run_id(timestamp, f"memory-{settings.embedding_model.replace('/', '--')}"),
        timestamp=timestamp,
        settings=settings,
        dataset_counts=dict(sorted(counts.items())),
        probe_count=len(dataset.probes),
        policies=results,
    )
