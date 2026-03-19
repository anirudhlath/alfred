"""Episodic memory search — semantic, time-based, and entity-based retrieval."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.memory.episodic.embeddings import EmbeddingModel
    from core.memory.episodic.store import EpisodicStore
    from core.memory.schemas import EpisodicEntry

logger = logging.getLogger(__name__)


class EpisodicSearch:
    """Search episodic memory across hot and cold storage."""

    def __init__(self, store: EpisodicStore, embedder: EmbeddingModel) -> None:
        self._store = store
        self._embedder = embedder

    def filter_by_entity(self, entries: list[EpisodicEntry], entity: str) -> list[EpisodicEntry]:
        """Filter entries by entity reference."""
        return [e for e in entries if entity in e.entities]

    async def search_cold(
        self,
        query: str,
        limit: int = 10,
        since: datetime | None = None,
        entity: str | None = None,
        recency_weight: float = 0.3,
    ) -> list[EpisodicEntry]:
        """Search cold storage with combined semantic + recency scoring.

        Args:
            query: Natural language search query.
            limit: Max entries to return.
            since: Only entries after this time.
            entity: Filter by entity reference.
            recency_weight: Weight for recency vs semantic similarity (0-1).
        """
        # Fetch candidates from cold storage
        candidates = await self._store.query_cold(
            limit=limit * 3,  # Over-fetch for re-ranking
            since=since,
            entity=entity,
        )

        if not candidates:
            return []

        # Embed query
        query_embedding = self._embedder.embed(query)

        # Score and rank
        scored: list[tuple[float, EpisodicEntry]] = []
        for entry in candidates:
            entry_embedding = await self._store.get_cold_embedding(entry.id)
            if entry_embedding is None:
                continue

            semantic_score = self._embedder.cosine_similarity(query_embedding, entry_embedding)

            # Recency score: exponential decay, 1.0 at now, ~0.5 at 7 days
            age_days = (datetime.now(UTC) - entry.timestamp).days
            recency_score = 0.5 ** (age_days / 7.0) if age_days >= 0 else 1.0

            combined = (1 - recency_weight) * semantic_score + recency_weight * recency_score
            scored.append((combined, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:limit]]
