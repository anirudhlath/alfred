"""ContextIndexManager — unified context index for the Conscious Engine and Librarian."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.memory.embedding_provider import EmbeddingProvider
    from core.memory.vector_store import VectorStore

from core.memory.vector_store import ContextMetadata, SearchResult


class ContextIndexManager:
    """Manages the unified idx:context RediSearch index.

    Wraps a VectorStore and adds higher-level operations for indexing episodic
    entries, semantic memory sections, and routines.  The Conscious Engine and
    Librarian interact with this class, never with the VectorStore directly.
    """

    def __init__(
        self,
        store: VectorStore,
        embedder: EmbeddingProvider,
        semantic_dirs: list[Path] | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._semantic_dirs = semantic_dirs or []

    async def index_episodic(
        self,
        id: str,  # noqa: A002
        content: str,
        semantic_key: str,
        source: str,
        entities: list[str],
        timestamp: float,
        significance: float,
    ) -> None:
        """Index an episodic memory entry."""
        content_emb, key_emb = await asyncio.gather(
            self._embedder.embed(content),
            self._embedder.embed(semantic_key or content),
        )
        metadata = ContextMetadata(
            type="episodic",
            source=source,
            entities=",".join(entities),
            timestamp=timestamp,
            significance=significance,
            retrieval_count=0,
        )
        await self._store.add(
            id=id,
            content=content,
            semantic_key=semantic_key or content,
            embedding_content=content_emb,
            embedding_semantic=key_emb,
            metadata=metadata,
        )

    async def index_semantic(
        self,
        id: str,  # noqa: A002
        content: str,
        source_file: str,
    ) -> None:
        """Index a section of semantic memory (from a Markdown file)."""
        emb = await self._embedder.embed(content)
        metadata = ContextMetadata(
            type="semantic",
            source=source_file,
            entities="",
            timestamp=0.0,
            significance=1.0,  # Semantic memory is always significant
            retrieval_count=0,
        )
        await self._store.add(
            id=id,
            content=content,
            semantic_key=content,
            embedding_content=emb,
            embedding_semantic=emb,
            metadata=metadata,
        )

    async def index_routine(
        self,
        id: str,  # noqa: A002
        content: str,
        confidence: float,
    ) -> None:
        """Index a routine/pattern."""
        emb = await self._embedder.embed(content)
        metadata = ContextMetadata(
            type="routine",
            source="librarian",
            entities="",
            timestamp=0.0,
            significance=confidence,
            retrieval_count=0,
        )
        await self._store.add(
            id=id,
            content=content,
            semantic_key=content,
            embedding_content=emb,
            embedding_semantic=emb,
            metadata=metadata,
        )

    async def search(
        self,
        query_embedding: list[float],
        limit: int = 10,
        min_similarity: float = 0.0,
        include_compressed: bool = False,
    ) -> list[SearchResult]:
        """Search the unified context index.

        By default compressed entries (compressed="yes") are excluded.
        Pass ``include_compressed=True`` for deliberate recall of compressed
        entries.
        """
        filters: dict[str, str | float | int] | None = None
        if not include_compressed:
            filters = {"compressed": ""}
        return await self._store.search(
            query_embedding=query_embedding,
            limit=limit,
            filters=filters,
            min_similarity=min_similarity,
        )

    async def remove(self, id: str) -> None:  # noqa: A002
        """Remove an entry from the index."""
        await self._store.delete(id)

    async def reindex_semantic_files(self) -> None:
        """Re-read all semantic memory Markdown files and re-index them.

        Iterates over every configured semantic directory, parses each ``.md``
        file into heading-delimited sections, and calls :meth:`index_semantic`
        for each non-empty section.
        """
        for dir_path in self._semantic_dirs:
            if not dir_path.exists():
                continue
            for md_file in dir_path.glob("*.md"):
                sections = self._parse_markdown_sections(md_file)
                for i, (heading, body) in enumerate(sections):
                    section_id = f"sem:{md_file.stem}:{i}"
                    content = f"{heading}\n{body}" if heading else body
                    if content.strip():
                        await self.index_semantic(
                            id=section_id,
                            content=content.strip(),
                            source_file=str(md_file.name),
                        )

    @staticmethod
    def _parse_markdown_sections(path: Path) -> list[tuple[str, str]]:
        """Parse a Markdown file into (heading, body) sections.

        Splits on ``#``-style headings (up to level 3).  Content before the
        first heading is returned as a section with an empty heading string.
        """
        text = path.read_text()
        sections: list[tuple[str, str]] = []
        current_heading = ""
        current_body: list[str] = []

        for line in text.split("\n"):
            if re.match(r"^#{1,3}\s", line):
                if current_heading or current_body:
                    sections.append((current_heading, "\n".join(current_body)))
                current_heading = line
                current_body = []
            else:
                current_body.append(line)

        if current_heading or current_body:
            sections.append((current_heading, "\n".join(current_body)))

        return sections
