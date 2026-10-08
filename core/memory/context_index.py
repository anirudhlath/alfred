"""ContextIndexManager — unified context index for the Conscious Engine and Librarian."""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping
    from datetime import datetime
    from pathlib import Path

    from core.memory.embedding_provider import EmbeddingProvider
    from core.memory.vector_store import Range, VectorStore

from core.memory.vector_store import ContextMetadata, SearchResult, record_retrievals

# Episodic memories' type: the only one the archive holds, as decay moves nothing else.
_EPISODIC = "episodic"


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
        *,
        archive: VectorStore | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._semantic_dirs = semantic_dirs or []
        # Where decay moves episodic memories (the cold store) — searched only by
        # deliberate recall.
        self._archive = archive

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
            type=_EPISODIC,
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

    async def search_text(
        self,
        query: str,
        limit: int = 10,
        min_similarity: float = 0.0,
        include_compressed: bool = False,
        *,
        update_stats: bool = False,
    ) -> list[SearchResult]:
        """Search the unified context index by text query (embeds internally)."""
        query_embedding = await self._embedder.embed(query)
        return await self.search(
            query_embedding=query_embedding,
            limit=limit,
            min_similarity=min_similarity,
            include_compressed=include_compressed,
            update_stats=update_stats,
        )

    async def search(
        self,
        query_embedding: list[float],
        limit: int = 10,
        min_similarity: float = 0.0,
        include_compressed: bool = False,
        *,
        update_stats: bool = False,
    ) -> list[SearchResult]:
        """Search the unified context index.

        By default compressed entries (compressed="yes") are excluded.
        Pass ``include_compressed=True`` for deliberate recall of compressed
        entries.

        ``update_stats`` records the hits as retrieved. It defaults to False
        because most callers here are not deliberate recall: the Librarian's decay
        pass *reads* these stats, and involuntary context assembly runs every turn.
        """
        filters: dict[str, str | float | int] | None = None
        if not include_compressed:
            filters = {"compressed": ""}
        results = await self._store.search(
            query_embedding=query_embedding,
            limit=limit,
            filters=filters,
            min_similarity=min_similarity,
        )
        if update_stats and results:
            await record_retrievals(self._store, results)
        return results

    async def recall(
        self,
        query: str,
        limit: int = 10,
        *,
        types: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> list[SearchResult]:
        """Deliberate recall: the whole hot index plus the archive, best ``limit`` by score.

        Compressed entries are included, and the hot hits returned are recorded as
        retrieved — deliberate recall is what the decay pass counts as using a memory.
        Without the archive, nothing decay had moved out of hot could be recalled.

        ``types`` keeps only entries of those types, and ``since`` only those from then
        on or timeless (semantic sections and routines carry timestamp 0); the limit
        counts only what they keep (issue #311). The stores cannot apply them inside a
        search, so each is read deeper, its depth doubling, until none holds back an
        entry that could still place — unfiltered, that is one search per store. A
        ``types`` without episodic leaves the archive unread: it holds nothing else.

        The searches are gathered without ``return_exceptions``, as
        ``EpisodicMemory.recall`` does: an archive failure fails the recall instead of
        quietly answering from hot alone, which would read as "no such memory".
        """
        if limit < 1:
            return []
        query_embedding = await self._embedder.embed(query)
        matches = _recall_filter(types, since)
        readings = [_Reading(self._store, hot=True, depth=limit)]
        if self._archive is not None and (not types or _EPISODIC in types):
            readings.append(_Reading(self._archive, hot=False, depth=limit))
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
            ranked = [pair for pair in _best_copies(readings) if matches(pair[0])]
            # What an entry not yet read must beat to displace the last of the best.
            bar = ranked[limit - 1][0].score if len(ranked) >= limit else -math.inf
            pending = [reading for reading in readings if reading.holds_back_above(bar)]
            if not pending:
                break
            for reading in pending:
                reading.depth *= 2
        best = ranked[:limit]
        hot_hits = [result for result, in_hot in best if in_hot]
        if hot_hits:
            await record_retrievals(self._store, hot_hits)
        return [result for result, _ in best]

    async def select(self, where: Mapping[str, Range]) -> list[SearchResult]:
        """Entries chosen by metadata ranges, not similarity — see ``VectorStore.select``."""
        return await self._store.select(where)

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


@dataclass
class _Reading:
    """One store's search in a deliberate recall, and how deep it has read."""

    store: VectorStore
    hot: bool
    depth: int
    hits: list[SearchResult] = field(default_factory=list)

    def holds_back_above(self, bar: float) -> bool:
        """Whether an entry this search has not reached yet could score above ``bar``.

        A store answers with its best ``depth``, fewer only once it has nothing else,
        and whatever it holds back scores no higher than the lowest it handed over.
        """
        return len(self.hits) >= self.depth and min(hit.score for hit in self.hits) > bar


def _best_copies(readings: list[_Reading]) -> list[tuple[SearchResult, bool]]:
    """Every hit once, at its best score and best first, with whether it is hot's copy.

    Hot is read first, so on a tie the copy kept is the one whose retrieval is recorded.
    """
    best: dict[str, tuple[SearchResult, bool]] = {}
    for reading in readings:
        for hit in reading.hits:
            if hit.id not in best or hit.score > best[hit.id][0].score:
                best[hit.id] = (hit, reading.hot)
    return sorted(best.values(), key=lambda pair: pair[0].score, reverse=True)


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
