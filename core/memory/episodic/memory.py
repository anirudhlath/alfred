"""EpisodicMemory — unified hot (Redis) + cold (SQLite) semantic memory."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.memory.embedding_provider import EmbeddingProvider
    from core.memory.vector_store import VectorStore

from core.memory.schemas import EpisodicEntry, EpisodicResult, SignificanceScore
from core.memory.vector_store import ContextMetadata, SearchResult


class EpisodicMemory:
    """Unified episodic memory with hot (Redis) + cold (SQLite) stores.

    Write path: embed content + semantic_key in parallel, write to hot store.
    Read path: search hot + cold in parallel, deduplicate by id (keep highest
    score), apply time filter, sort descending, return top-limit results.
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
    ) -> list[EpisodicResult]:
        """Search hot + cold in parallel, deduplicate, rank by score descending."""
        query_emb = await self._embedder.embed(query)

        filters: dict[str, str | float | int] | None = None
        if types:
            filters = {"type": "|".join(types)}

        hot_results, cold_results = await asyncio.gather(
            self._hot.search(query_emb, limit=limit, filters=filters),
            self._cold.search(query_emb, limit=limit, filters=filters),
        )

        # Deduplicate by id, keeping highest score
        best: dict[str, tuple[SearchResult, str]] = {}
        for r in hot_results:
            best[r.id] = (r, "hot")
        for r in cold_results:
            if r.id not in best or r.score > best[r.id][0].score:
                best[r.id] = (r, "cold")

        # Filter by time if requested
        merged: list[tuple[SearchResult, str]] = list(best.values())
        if since:
            since_ts = since.timestamp()
            merged = [(r, s) for r, s in merged if r.metadata.timestamp >= since_ts]

        # Sort by score descending, take top limit
        merged.sort(key=lambda x: x[0].score, reverse=True)
        merged = merged[:limit]

        # Persist retrieval stats for hot-store results (parallel writes)
        now_ts = datetime.now(UTC).timestamp()
        update_coros = [
            self._hot.update_metadata(
                sr.id,
                {
                    "retrieval_count": sr.metadata.retrieval_count + 1,
                    "last_retrieved": now_ts,
                },
            )
            for sr, store in merged
            if store == "hot"
        ]
        if update_coros:
            await asyncio.gather(*update_coros)

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
                    source_store=source_store,  # type: ignore[arg-type]
                )
            )

        return episodic_results

    async def copy_to_cold_and_remove(self, search_result: SearchResult) -> None:
        """Re-embed, write to cold, then delete from hot.

        Accepts a ``SearchResult`` (from a context index search) that contains
        the content and metadata needed to reconstruct the entry in cold storage.
        """
        content_emb, key_emb = await asyncio.gather(
            self._embedder.embed(search_result.content),
            self._embedder.embed(search_result.semantic_key or search_result.content),
        )
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
