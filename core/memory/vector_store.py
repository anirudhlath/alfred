"""VectorStore abstract base class and associated models."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


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
