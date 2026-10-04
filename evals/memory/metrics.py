"""What decay did for the user: probes, retention, stuck memories and selection blind spots."""

from __future__ import annotations

import inspect
import json
from collections import Counter
from datetime import timedelta
from typing import TYPE_CHECKING

from core.conscious.memory_tools import dispatch_memory_tool
from core.librarian.consolidator import Librarian, migration_pressure
from evals.memory.dataset import NOISE, Category
from evals.memory.env import StoredVectors, scan_hot, stored_vectors
from evals.memory.models import Checkpoint, DaySnapshot, ProbeOutcome, Ratio

if TYPE_CHECKING:
    from datetime import datetime

    from core.memory.vector_store import ContextMetadata, SearchResult
    from evals.memory.dataset import Dataset, SimMemory
    from evals.memory.env import SimEnv

# The decay pass picks its candidates by similarity to this phrase. Read from the
# signature so the eval measures whatever the Librarian actually uses.
DECAY_SEARCH_QUERY: str = (
    inspect.signature(Librarian._apply_decay).parameters["search_query"].default
)
# memory_recall_memories' own default (core/conscious/memory_tools.py).
TOOL_DEFAULT_LIMIT = 10
# The source _compress_and_migrate stamps on the summary it writes to cold.
COMPRESSED_SOURCE = "librarian_compressed"
# "Routine memories older than about two weeks."
NOISE_AGE = timedelta(days=14)
# Daily checks a memory must stay eligible for to count as stuck (>= 1 day, >= 7 days).
STUCK_1D_CHECKS = 2
STUCK_7D_CHECKS = 8


def decay_pressure(metadata: ContextMetadata, now_ts: float) -> float | None:
    """Migration pressure exactly as ``Librarian._apply_decay`` computes it.

    ``None`` where the decay pass skips the entry outright (not episodic, or no
    timestamp).
    """
    if metadata.type != "episodic" or metadata.timestamp <= 0:
        return None
    age_days = (now_ts - metadata.timestamp) / 86400.0
    if metadata.last_retrieved > 0:
        days_since_last_retrieved = (now_ts - metadata.last_retrieved) / 86400.0
    else:
        days_since_last_retrieved = age_days
    return migration_pressure(
        age_days, metadata.significance, metadata.retrieval_count, days_since_last_retrieved
    )


def is_eligible(result: SearchResult, now_ts: float, threshold: float) -> bool:
    pressure = decay_pressure(result.metadata, now_ts)
    return pressure is not None and pressure > threshold


class EligibilityTracker:
    """Consecutive daily checks each hot memory has spent above the threshold."""

    def __init__(self) -> None:
        self._streak: dict[str, int] = {}

    def update(self, eligible_ids: set[str]) -> None:
        self._streak = {memory_id: self._streak.get(memory_id, 0) + 1 for memory_id in eligible_ids}

    def stuck(self, min_checks: int) -> list[str]:
        return sorted(m for m, streak in self._streak.items() if streak >= min_checks)


async def day_snapshot(
    env: SimEnv,
    dataset: Dataset,
    hot: list[SearchResult],
    now: datetime,
    threshold: float,
    tracker: EligibilityTracker,
) -> DaySnapshot:
    """Read the hot store at ``now`` and advance the stuck tracker."""
    category_of = dataset.category_of()
    now_ts = now.timestamp()
    episodic = [r for r in hot if r.metadata.type == "episodic"]
    in_hot = {r.id for r in episodic}
    tracker.update({r.id for r in episodic if is_eligible(r, now_ts, threshold)})
    old_noise = [m.id for m in dataset.memories if m.category in NOISE and m.at <= now - NOISE_AGE]
    by_category = Counter(str(category_of.get(r.id, "other")) for r in episodic)
    return DaySnapshot(
        day=round((now - dataset.start) / timedelta(days=1)),
        hot_episodic=len(episodic),
        hot_by_category=dict(sorted(by_category.items())),
        hot_distinct_content=len({r.content for r in episodic}),
        hot_distinct_semantic_keys=len({r.semantic_key for r in episodic}),
        cold_entries=await env.cold.count(),
        eligible_in_hot=sum(1 for r in episodic if is_eligible(r, now_ts, threshold)),
        stuck_1d=len(tracker.stuck(STUCK_1D_CHECKS)),
        stuck_7d=len(tracker.stuck(STUCK_7D_CHECKS)),
        noise_older_14d=len(old_noise),
        noise_older_14d_gone=sum(1 for m in old_noise if m not in in_hot),
    )


async def probe(
    env: SimEnv,
    question: str,
    target: SimMemory,
    target_content: str,
    category_of: dict[str, Category],
    hot_vectors: StoredVectors,
) -> ProbeOutcome:
    """Ask one question every way Alfred can answer it, plus an exact brute-force ranking.

    The exact ranking scores the question against every vector in the hot store, so it
    says what a perfect KNN would have returned — RediSearch's HNSW is approximate.
    """
    config = env.config
    query = await env.embedder.embed(question)
    exact_rank, exact_score = _exact_rank(hot_vectors, query, target.id)
    cold_matches = [
        r
        for r in await env.cold.search(query, limit=TOOL_DEFAULT_LIMIT)
        if names_target(target.id, target_content, r.id, r.metadata.source, r.content)
    ]
    involuntary = await env.context_index.search_text(
        question,
        limit=config.involuntary_recall_limit,
        min_similarity=config.involuntary_recall_threshold,
    )
    open_results = await env.context_index.search_text(
        question, limit=config.involuntary_recall_limit, min_similarity=0.0
    )
    tool_reply = json.loads(
        await dispatch_memory_tool(
            "memory_recall_memories", {"query": question}, env.recall_view, env.context_reader
        )
    )
    recalled = await env.episodic.recall(question, limit=TOOL_DEFAULT_LIMIT, update_stats=False)

    involuntary_ids = [r.id for r in involuntary]
    open_ids = [r.id for r in open_results]
    target_score = next((r.score for r in open_results if r.id == target.id), None)
    if await env.hot.exists(target.id):
        location = "hot"
    elif await env.cold.exists(target.id):
        location = "cold"
    else:
        location = "missing"
    return ProbeOutcome(
        question=question,
        target_id=target.id,
        category=target.category,
        location=location,
        involuntary_hit=target.id in involuntary_ids,
        involuntary_rank=_rank(involuntary_ids, target.id),
        involuntary_returned=len(involuntary),
        involuntary_noise=sum(1 for r in involuntary if category_of.get(r.id) in NOISE),
        exact_rank=exact_rank,
        exact_score=exact_score,
        exact_hit=(
            exact_rank is not None
            and exact_score is not None
            and exact_rank <= config.involuntary_recall_limit
            and exact_score >= config.involuntary_recall_threshold
        ),
        open_hit=target.id in open_ids,
        open_rank=_rank(open_ids, target.id),
        target_score=target_score,
        tool_hit=any(m["content"] == target_content for m in tool_reply["memories"]),
        recall_hit=any(
            names_target(target.id, target_content, r.entry.id, r.entry.source, r.entry.summary)
            for r in recalled
        ),
        cold_hit=bool(cold_matches),
        cold_score=max((r.score for r in cold_matches), default=None),
    )


def names_target(
    target_id: str, target_content: str, memory_id: str, source: str, text: str
) -> bool:
    """A recalled memory answers the probe if it is the target, or a compression summary
    the target was folded into."""
    return memory_id == target_id or (source == COMPRESSED_SOURCE and target_content in text)


def _rank(ids: list[str], target_id: str) -> int | None:
    return ids.index(target_id) + 1 if target_id in ids else None


def _exact_rank(
    vectors: StoredVectors, query: list[float], target_id: str
) -> tuple[int | None, float | None]:
    if target_id not in vectors.ids:
        return None, None
    scores = vectors.similarity(query)
    target_score = float(scores[vectors.ids.index(target_id)])
    # Rank = 1 + entries strictly better; ties (identical routine vectors) do not push
    # the target down.
    return int((scores > target_score).sum()) + 1, target_score


async def checkpoint(
    env: SimEnv,
    dataset: Dataset,
    now: datetime,
    snapshot: DaySnapshot,
    tracker: EligibilityTracker,
    threshold: float,
    target_content: dict[str, str],
    retrievals: Counter[str],
) -> Checkpoint:
    category_of = dataset.category_of()
    by_id = {m.id: m for m in dataset.memories}
    hot = await scan_hot(env.redis)
    # Everything involuntary recall can return: the hot index minus compressed entries.
    hot_vectors = await stored_vectors(
        env.redis, [r.id for r in hot if r.metadata.compressed != "yes"]
    )
    outcomes = [
        await probe(
            env,
            p.question,
            by_id[p.target_id],
            target_content[p.target_id],
            category_of,
            hot_vectors,
        )
        for p in dataset.probes
        if by_id[p.target_id].at < now
    ]

    in_hot = {r.id for r in hot}
    now_ts = now.timestamp()
    eligible = sorted(r.id for r in hot if is_eligible(r, now_ts, threshold))
    stuck = set(tracker.stuck(STUCK_1D_CHECKS))
    unpickable = await _unpickable(env, eligible)
    return Checkpoint(
        day=snapshot.day,
        probes=outcomes,
        kept_significant=_kept(dataset, Category.SIGNIFICANT, now, in_hot),
        kept_recalled=_kept(dataset, Category.RECALLED, now, in_hot),
        snapshot=snapshot,
        eligible_unpickable=Ratio(hits=len(unpickable), n=len(eligible)),
        stuck_unpickable=Ratio(hits=len(unpickable & stuck), n=len(stuck)),
        retrievals_applied=retrievals["applied"],
        retrievals_lost=retrievals["lost"],
    )


def _kept(dataset: Dataset, category: Category, now: datetime, in_hot: set[str]) -> Ratio:
    written = [m.id for m in dataset.memories if m.category == category and m.at < now]
    return Ratio(hits=sum(1 for m in written if m in in_hot), n=len(written))


async def _unpickable(env: SimEnv, ids: list[str]) -> set[str]:
    """Memories whose stored vectors both sit at negative cosine to the decay query.

    RediSearch scores cosine as ``1 - distance`` = cosine similarity, and the decay
    pass searches with ``min_similarity=0.0``, so these can never be returned to it.
    """
    if not ids:
        return set()
    vectors = await stored_vectors(env.redis, ids)
    scores = vectors.similarity(await env.embedder.embed(DECAY_SEARCH_QUERY))
    return {memory_id for memory_id, score in zip(vectors.ids, scores, strict=True) if score < 0}
