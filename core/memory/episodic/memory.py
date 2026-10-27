"""EpisodicMemory — unified hot (Redis) + cold (SQLite) semantic memory."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.memory.embedding_provider import EmbeddingProvider
    from core.memory.vector_store import SearchResult, VectorStore

from core.memory.recall import recall_hot_and_cold
from core.memory.schemas import EpisodicEntry, EpisodicResult, SignificanceScore
from core.memory.vector_store import ContextMetadata


class EpisodicMemory:
    """Unified episodic memory with hot (Redis) + cold (SQLite) stores.

    Write path: embed content + semantic_key in parallel, write to hot store.
    Read path: ``recall_hot_and_cold`` (``core/memory/recall.py``), the hot + cold merge
    deliberate recall shares — filtered before the limit, each id once at its best score.
    Migration: hot → cold is a two-step caller responsibility; this class only
    deletes the hot entry once called via migrate_to_cold().
    """

    def __init__(
        self,
        hot: VectorStore,
        cold: VectorStore,
        embedder: EmbeddingProvider,
    ) -> None:
        self._hot = hot
        self._cold = cold
        self._embedder = embedder

    async def write(self, entry: EpisodicEntry, significance: SignificanceScore) -> None:
        """Embed content + semantic_key in parallel, then write to hot store."""
        entry.significance = significance
        semantic_text = entry.semantic_key if entry.semantic_key else entry.summary
        content_emb, key_emb = await asyncio.gather(
            self._embedder.embed(entry.summary),
            self._embedder.embed(semantic_text),
        )
        metadata = ContextMetadata(
            type="episodic",
            source=entry.source,
            entities=",".join(entry.entities),
            timestamp=entry.timestamp.timestamp(),
            significance=significance.overall,
            retrieval_count=entry.retrieval_count,
        )
        await self._hot.add(
            id=entry.id,
            content=entry.summary,
            semantic_key=semantic_text,
            embedding_content=content_emb,
            embedding_semantic=key_emb,
            metadata=metadata,
        )

    async def recall(
        self,
        query: str,
        limit: int = 10,
        since: datetime | None = None,
        types: list[str] | None = None,
        *,
        update_stats: bool = True,
    ) -> list[EpisodicResult]:
        """The best ``limit`` memories across hot and cold, by score descending.

        Args:
            query: Natural-language search string to embed and match.
            limit: Maximum number of results to return after merging both stores; it
                counts only what ``types`` and ``since`` keep (issue #311).
            since: Exclude entries older than this datetime (timestamp-0 entries —
                semantic sections and routines — are timeless and kept).
            types: Optional list of memory type strings to filter by (e.g. ["episodic"]).
            update_stats: When True (default), persist retrieval_count/last_retrieved
                to the hot store for every hot result returned.  Pass False from
                read-only admin callers to avoid perturbing decay-relevant stats.

        The merge is ``recall_hot_and_cold``'s, shared with ``ContextIndexManager.recall``
        — including that a cold-store failure fails the recall.
        """
        merged = await recall_hot_and_cold(
            query,
            self._embedder,
            self._hot,
            self._cold,
            limit,
            types=types,
            since=since,
            update_stats=update_stats,
        )

        # Convert to EpisodicResult, increment retrieval_count
        episodic_results: list[EpisodicResult] = []
        for search_result, source_store in merged:
            entities = (
                [e for e in search_result.metadata.entities.split(",") if e]
                if search_result.metadata.entities
                else []
            )
            entry = EpisodicEntry(
                id=search_result.id,
                timestamp=datetime.fromtimestamp(search_result.metadata.timestamp, tz=UTC),
                source=search_result.metadata.source,
                summary=search_result.content,
                entities=entities,
                significance=SignificanceScore(overall=search_result.metadata.significance),
                semantic_key=search_result.semantic_key,
                retrieval_count=search_result.metadata.retrieval_count + 1,
            )
            episodic_results.append(
                EpisodicResult(
                    entry=entry,
                    score=search_result.score,
                    source_store=source_store,
                )
            )

        return episodic_results

    async def copy_to_cold_and_remove(self, search_result: SearchResult) -> None:
        """Write to cold with the vectors hot holds, then delete from hot.

        Accepts a ``SearchResult`` (from a context index search) that contains
        the content and metadata needed to reconstruct the entry in cold storage.
        The hot store's own vectors are reused — the same text was embedded on the way
        in — and the text is embedded again only when hot cannot hand them back.
        """
        stored = await self._hot.embeddings(search_result.id)
        if stored is None:
            stored = await asyncio.gather(
                self._embedder.embed(search_result.content),
                self._embedder.embed(search_result.semantic_key or search_result.content),
            )
        content_emb, key_emb = stored
        await self._cold.add(
            id=search_result.id,
            content=search_result.content,
            semantic_key=search_result.semantic_key or search_result.content,
            embedding_content=content_emb,
            embedding_semantic=key_emb,
            metadata=search_result.metadata,
        )
        await self._hot.delete(search_result.id)

    async def migrate_to_cold(self, entry_id: str) -> None:
        """Remove entry from hot store only (legacy — prefer copy_to_cold_and_remove).

        Only deletes from hot. The caller must ensure the entry already exists
        in cold storage before calling this method.
        """
        await self._hot.delete(entry_id)
