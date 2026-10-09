"""Deliberate recall's merge of the hot store and the cold archive: one copy for both callers.

``ContextIndexManager.recall`` (the assistant's ``memory_recall_memories``) and
``EpisodicMemory.recall`` (admin search, the memory-decay eval) both rank the two stores
together through ``recall_hot_and_cold``.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, NamedTuple

from core.memory.vector_store import SearchResult, record_retrievals

if TYPE_CHECKING:
    from collections.abc import Callable, Collection
    from datetime import datetime

    from core.memory.embedding_provider import EmbeddingProvider
    from core.memory.vector_store import VectorStore

# Episodic memories' type: the only one the archive holds, as decay moves nothing else.
EPISODIC = "episodic"

StoreName = Literal["hot", "cold"]


class RecallHit(NamedTuple):
    """One recalled memory, and which store's copy it is."""

    result: SearchResult
    store: StoreName


async def recall_hot_and_cold(
    query: str,
    embedder: EmbeddingProvider,
    hot: VectorStore,
    cold: VectorStore | None,
    limit: int,
    *,
    types: Collection[str] | None = None,
    since: datetime | None = None,
    update_stats: bool,
) -> list[RecallHit]:
    """The best ``limit`` memories across hot and cold by score, each id once.

    An id in both stores keeps its higher-scoring copy, hot's on a tie — the copy whose
    retrieval ``update_stats`` records. Only the hot hits returned are recorded: cold
    entries keep no stats, and anything read but filtered out or cut was never used.

    ``types`` keeps only entries of those types, and ``since`` only those from then on or
    timeless (semantic sections and routines carry timestamp 0); the limit counts only
    what they keep (issue #311). The stores cannot apply them inside a search — the hot
    store's filters are TAG queries and its ``type`` field is TEXT, and the cold store
    ignores filters — so each is read deeper, its depth doubling, until none holds back
    an entry that could still place. Unfiltered, that is one search per store. A
    ``types`` without episodic leaves ``cold`` unread: it holds nothing else.

    The searches are gathered without ``return_exceptions``: a cold failure fails the
    recall instead of quietly answering from hot alone, which would read as "no such
    memory".
    """
    if limit < 1:
        return []
    query_embedding = await embedder.embed(query)
    matches = _recall_filter(types, since)
    readings = [_Reading(hot, "hot", depth=limit)]
    if cold is not None and (not types or EPISODIC in types):
        readings.append(_Reading(cold, "cold", depth=limit))
    pending = readings
    while True:
        replies = await asyncio.gather(
            *(
                reading.store.search(
                    query_embedding=query_embedding, limit=reading.depth, filters=None
                )
                for reading in pending
            )
        )
        for reading, hits in zip(pending, replies, strict=True):
            reading.hits = hits
        ranked = [hit for hit in _best_copies(readings) if matches(hit.result)]
        # What an entry not yet read must beat to displace the last of the best.
        bar = ranked[limit - 1].result.score if len(ranked) >= limit else -math.inf
        pending = [reading for reading in readings if reading.holds_back_above(bar)]
        if not pending:
            break
        for reading in pending:
            reading.depth *= 2
    best = ranked[:limit]
    hot_hits = [hit.result for hit in best if hit.store == "hot"]
    if update_stats and hot_hits:
        await record_retrievals(hot, hot_hits)
    return best


@dataclass
class _Reading:
    """One store's search in a recall, and how deep it has read."""

    store: VectorStore
    name: StoreName
    depth: int
    hits: list[SearchResult] = field(default_factory=list)

    def holds_back_above(self, bar: float) -> bool:
        """Whether an entry this search has not reached yet could score above ``bar``.

        A store answers with its best ``depth``, fewer only once it has nothing else,
        and whatever it holds back scores no higher than the lowest it handed over.
        """
        return len(self.hits) >= self.depth and min(hit.score for hit in self.hits) > bar


def _best_copies(readings: list[_Reading]) -> list[RecallHit]:
    """Every hit once, at its best score and best first, with the store it came from.

    Hot is read first, so on a tie the copy kept is the one whose retrieval is recorded.
    """
    best: dict[str, RecallHit] = {}
    for reading in readings:
        for hit in reading.hits:
            if hit.id not in best or hit.score > best[hit.id].result.score:
                best[hit.id] = RecallHit(hit, reading.name)
    return sorted(best.values(), key=lambda kept: kept.result.score, reverse=True)


def _recall_filter(
    types: Collection[str] | None, since: datetime | None
) -> Callable[[SearchResult], bool]:
    """Whether a hit is one of ``types`` and no older than ``since``; unset, either passes."""
    cutoff = since.timestamp() if since is not None else None

    def matches(hit: SearchResult) -> bool:
        if types and hit.metadata.type not in types:
            return False
        # Timestamp 0 is timeless (semantic sections, routines), so never too old.
        timestamp = hit.metadata.timestamp
        return cutoff is None or timestamp == 0.0 or timestamp >= cutoff

    return matches
