"""RedisVectorStore — VectorStore implementation backed by RediSearch."""

from __future__ import annotations

import asyncio
import logging
import struct
from typing import TYPE_CHECKING

from core.memory.vector_store import ContextMetadata, SearchResult, VectorStore
from shared.streams import CONTEXT_INDEX, CONTEXT_PREFIX

if TYPE_CHECKING:
    from shared.types import AioRedis

logger = logging.getLogger(__name__)


def _pack_floats(values: list[float]) -> bytes:
    """Pack a list of float32 values into bytes (little-endian)."""
    n = len(values)
    return struct.pack(f"<{n}f", *values)


class RedisVectorStore(VectorStore):
    """VectorStore backed by RediSearch HNSW vector index.

    Uses two vector fields (``embedding_content`` and ``embedding_semantic``) so
    that both lexical similarity and semantic key similarity contribute to search
    results.  ``search()`` fires two parallel KNN queries and merges results by
    taking the max score per id.

    Index creation is deferred to the first operation via ``ensure_index()``.
    If RediSearch is unavailable the store degrades gracefully — ``add`` and
    ``delete`` still work on plain Redis hashes, but ``search`` returns ``[]``.
    """

    def __init__(self, redis: AioRedis, dim: int = 768) -> None:
        self._redis = redis
        self._dim = dim
        self._index_ready: bool = False

    # ------------------------------------------------------------------
    # Index lifecycle
    # ------------------------------------------------------------------

    async def ensure_index(self) -> None:
        """Create the RediSearch index if it does not already exist."""
        if self._index_ready:
            return
        try:
            await self._redis.execute_command(  # type: ignore[no-untyped-call]
                "FT.CREATE",
                CONTEXT_INDEX,
                "ON",
                "HASH",
                "PREFIX",
                "1",
                CONTEXT_PREFIX,
                "SCHEMA",
                # Scalar fields
                "type",
                "TEXT",
                "source",
                "TEXT",
                "entities",
                "TEXT",
                "timestamp",
                "NUMERIC",
                "significance",
                "NUMERIC",
                "retrieval_count",
                "NUMERIC",
                "last_retrieved",
                "NUMERIC",
                "compressed",
                "TAG",
                "content",
                "TEXT",
                "semantic_key",
                "TEXT",
                # Vector fields
                "embedding_content",
                "VECTOR",
                "HNSW",
                "6",
                "TYPE",
                "FLOAT32",
                "DIM",
                str(self._dim),
                "DISTANCE_METRIC",
                "COSINE",
                "embedding_semantic",
                "VECTOR",
                "HNSW",
                "6",
                "TYPE",
                "FLOAT32",
                "DIM",
                str(self._dim),
                "DISTANCE_METRIC",
                "COSINE",
            )
            self._index_ready = True
            logger.info("Created RediSearch index %s (dim=%d)", CONTEXT_INDEX, self._dim)
        except Exception as exc:
            # Index may already exist — that is fine
            err = str(exc)
            if "Index already exists" in err or "already exists" in err.lower():
                self._index_ready = True
                logger.debug("RediSearch index %s already exists", CONTEXT_INDEX)
            else:
                logger.warning("RediSearch unavailable — vector search disabled: %s", exc)
                # Leave _index_ready = False so search degrades gracefully

    # ------------------------------------------------------------------
    # VectorStore interface
    # ------------------------------------------------------------------

    async def add(
        self,
        id: str,  # noqa: A002
        content: str,
        semantic_key: str,
        embedding_content: list[float],
        embedding_semantic: list[float],
        metadata: ContextMetadata,
    ) -> None:
        await self.ensure_index()
        key = f"{CONTEXT_PREFIX}{id}"
        mapping: dict[str, bytes | str | float | int] = {
            "content": content,
            "semantic_key": semantic_key,
            "type": metadata.type,
            "source": metadata.source,
            "entities": metadata.entities,
            "timestamp": metadata.timestamp,
            "significance": metadata.significance,
            "retrieval_count": metadata.retrieval_count,
            "last_retrieved": metadata.last_retrieved,
            "compressed": metadata.compressed,
            "embedding_content": _pack_floats(embedding_content),
            "embedding_semantic": _pack_floats(embedding_semantic),
        }
        await self._redis.hset(key, mapping=mapping)  # type: ignore[misc]

    async def search(
        self,
        query_embedding: list[float],
        limit: int,
        filters: dict[str, str | float | int] | None = None,
        min_similarity: float = 0.0,
    ) -> list[SearchResult]:
        if not self._index_ready:
            await self.ensure_index()
        if not self._index_ready:
            return []

        query_bytes = _pack_floats(query_embedding)

        # Build optional pre-filter expression
        filter_expr = "*"
        if filters:
            parts: list[str] = []
            for field, value in filters.items():
                if isinstance(value, str):
                    if value == "":
                        # Empty string = exclude entries with non-empty tag
                        # For TAG fields, -@field:{yes} excludes tagged entries
                        parts.append(f"(-@{field}:{{yes}})")
                    else:
                        parts.append(f"@{field}:{{{value}}}")
                else:
                    parts.append(f"@{field}:[{value} {value}]")
            filter_expr = " ".join(parts)

        async def _knn(field: str) -> list[SearchResult]:
            knn_query = f"({filter_expr})=>[KNN {limit} @{field} $vec AS __score]"
            try:
                raw = await self._redis.execute_command(  # type: ignore[no-untyped-call]
                    "FT.SEARCH",
                    CONTEXT_INDEX,
                    knn_query,
                    "PARAMS",
                    "2",
                    "vec",
                    query_bytes,
                    "RETURN",
                    "11",
                    "content",
                    "semantic_key",
                    "type",
                    "source",
                    "entities",
                    "timestamp",
                    "significance",
                    "retrieval_count",
                    "last_retrieved",
                    "compressed",
                    "__score",
                    "SORTBY",
                    "__score",
                    "DIALECT",
                    "2",
                )
            except Exception as exc:
                logger.warning("FT.SEARCH failed on field %s: %s", field, exc)
                return []

            return _parse_ft_results(raw, min_similarity)

        results_content, results_semantic = await asyncio.gather(
            _knn("embedding_content"),
            _knn("embedding_semantic"),
        )

        # Merge: keep max score per id
        merged: dict[str, SearchResult] = {}
        for result in (*results_content, *results_semantic):
            existing = merged.get(result.id)
            if existing is None or result.score > existing.score:
                merged[result.id] = result

        # Sort descending by score and cap at limit
        return sorted(merged.values(), key=lambda r: r.score, reverse=True)[:limit]

    async def delete(self, id: str) -> None:  # noqa: A002
        key = f"{CONTEXT_PREFIX}{id}"
        await self._redis.delete(key)

    async def exists(self, id: str) -> bool:  # noqa: A002
        key = f"{CONTEXT_PREFIX}{id}"
        result: int = await self._redis.exists(key)
        return result > 0

    async def count(self) -> int:
        if not self._index_ready:
            await self.ensure_index()
        if not self._index_ready:
            return 0
        try:
            raw = await self._redis.execute_command("FT.INFO", CONTEXT_INDEX)  # type: ignore[no-untyped-call]
            # FT.INFO returns a flat list of alternating key/value pairs
            info: dict[str, object] = _parse_ft_info(raw)
            num_docs = info.get("num_docs", b"0")
            if isinstance(num_docs, (bytes, str, int)):
                return int(num_docs)
            return 0
        except Exception as exc:
            logger.warning("FT.INFO failed: %s", exc)
            return 0


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _parse_ft_results(raw: object, min_similarity: float) -> list[SearchResult]:
    """Parse the flat list returned by FT.SEARCH into SearchResult objects."""
    if not isinstance(raw, (list, tuple)) or len(raw) < 1:
        return []

    items = list(raw)
    # First element is the total count; then pairs of (key, [field, value, ...])
    results: list[SearchResult] = []
    i = 1
    while i + 1 < len(items):
        doc_key = items[i]
        fields_raw = items[i + 1]
        i += 2

        if not isinstance(fields_raw, (list, tuple)):
            continue

        fields: dict[str, str] = {}
        j = 0
        field_list = list(fields_raw)
        while j + 1 < len(field_list):
            fname = field_list[j]
            fval = field_list[j + 1]
            if isinstance(fname, bytes):
                fname = fname.decode()
            if isinstance(fval, bytes):
                fval = fval.decode()
            fields[str(fname)] = str(fval)
            j += 2

        score_str = fields.get("__score", "1.0")
        try:
            # RediSearch cosine distance: 0 = identical, 2 = opposite.
            # Convert to similarity: similarity = 1 - distance
            distance = float(score_str)
            score = 1.0 - distance
        except ValueError:
            score = 0.0

        if score < min_similarity:
            continue

        doc_id = str(doc_key)
        if isinstance(doc_key, bytes):
            doc_id = doc_key.decode()
        # Strip the CONTEXT_PREFIX to get the bare id
        if doc_id.startswith(CONTEXT_PREFIX):
            doc_id = doc_id[len(CONTEXT_PREFIX) :]

        metadata = ContextMetadata(
            type=fields.get("type", ""),
            source=fields.get("source", ""),
            entities=fields.get("entities", ""),
            timestamp=float(fields.get("timestamp", 0)),
            significance=float(fields.get("significance", 0)),
            retrieval_count=int(fields.get("retrieval_count", 0)),
            last_retrieved=float(fields.get("last_retrieved", 0)),
            compressed=fields.get("compressed", ""),
        )
        results.append(
            SearchResult(
                id=doc_id,
                score=score,
                content=fields.get("content", ""),
                semantic_key=fields.get("semantic_key", ""),
                metadata=metadata,
            )
        )

    return results


def _parse_ft_info(raw: object) -> dict[str, object]:
    """Parse the flat alternating key/value list from FT.INFO."""
    if not isinstance(raw, (list, tuple)):
        return {}
    items = list(raw)
    result: dict[str, object] = {}
    i = 0
    while i + 1 < len(items):
        key = items[i]
        val = items[i + 1]
        if isinstance(key, bytes):
            key = key.decode()
        result[str(key)] = val
        i += 2
    return result
