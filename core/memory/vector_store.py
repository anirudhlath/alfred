"""VectorStore abstract base class and associated models."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Iterable


class ContextMetadata(BaseModel):
    """Typed metadata for context index entries."""

    type: str
    source: str
    entities: str
    timestamp: float
    significance: float
    retrieval_count: int
    last_retrieved: float = 0.0
    compressed: str = ""  # "yes" if compressed into summary


class SearchResult(BaseModel):
    """Result from a vector store search."""

    id: str
    score: float
    content: str
    semantic_key: str
    metadata: ContextMetadata


class VectorStore(ABC):
    """Abstract vector storage with similarity search."""

    @abstractmethod
    async def add(
        self,
        id: str,  # noqa: A002
        content: str,
        semantic_key: str,
        embedding_content: list[float],
        embedding_semantic: list[float],
        metadata: ContextMetadata,
    ) -> None: ...

    @abstractmethod
    async def search(
        self,
        query_embedding: list[float],
        limit: int,
        filters: dict[str, str | float | int] | None = None,
        min_similarity: float = 0.0,
    ) -> list[SearchResult]: ...

    @abstractmethod
    async def delete(self, id: str) -> None: ...  # noqa: A002

    @abstractmethod
    async def exists(self, id: str) -> bool: ...  # noqa: A002

    @abstractmethod
    async def count(self) -> int: ...

    @abstractmethod
    async def update_metadata(
        self,
        id: str,  # noqa: A002
        fields: dict[str, str | float | int],
    ) -> None:
        """Update specific metadata fields in-place (no re-embedding)."""
        ...


async def record_retrievals(store: VectorStore, results: Iterable[SearchResult]) -> None:
    """Mark search results as retrieved, bumping count and stamping the time.

    The Librarian's decay pass reads both fields to let recalled memories resist
    migration to cold storage, so only *deliberate* recall should call this — a
    caller that reads these stats (decay) or that fires on every turn (involuntary
    context assembly) would flatten the signal it depends on.
    """
    now_ts = datetime.now(UTC).timestamp()
    updates = [
        store.update_metadata(
            result.id,
            {
                "retrieval_count": result.metadata.retrieval_count + 1,
                "last_retrieved": now_ts,
            },
        )
        for result in results
    ]
    if updates:
        await asyncio.gather(*updates)
