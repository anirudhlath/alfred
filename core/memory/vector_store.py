"""VectorStore abstract base class and associated models."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


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


class Range(BaseModel):
    """An open interval on a numeric metadata field; ``None`` leaves that side unbounded."""

    above: float | None = None
    below: float | None = None


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

    async def select(self, where: Mapping[str, Range]) -> list[SearchResult]:
        """Every entry whose numeric metadata lies inside all of ``where``'s ranges.

        Chosen by metadata rather than similarity, so nothing that matches is left out;
        scores are 0. Only stores something scans by metadata implement it.
        """
        raise NotImplementedError(f"{type(self).__name__} cannot select by metadata")

    async def embeddings(self, id: str) -> tuple[list[float], list[float]] | None:  # noqa: A002
        """The (content, semantic key) vectors stored for ``id``, as ``add`` received them.

        ``None`` when the entry is absent or the store cannot hand its vectors back —
        callers then embed the text themselves.
        """
        return None

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
