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

    def __init__(self, redis: AioRedis, dim: int = 384) -> None:
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
                await self._verify_index_dim()
                self._index_ready = True
                logger.debug("RediSearch index %s already exists", CONTEXT_INDEX)
            else:
                logger.warning(
                    "RediSearch unavailable — vector search disabled: %s", exc, exc_info=True
                )
                # Leave _index_ready = False so search degrades gracefully

    async def _verify_index_dim(self) -> None:
        """Fail loudly if the existing index was built at a different dimension.

        Changing EMBEDDING_MODEL (or EMBEDDING_BACKEND) changes the vector width.
        Writing those vectors into an index built at the old width produces no
        exception and no log — searches simply stop matching. Refuse to start.
        """
        try:
            raw = await self._redis.execute_command("FT.INFO", CONTEXT_INDEX)  # type: ignore[no-untyped-call]
        except Exception as exc:
            # Only a mismatch we can *prove* is worth failing on: an FT.INFO that
            # errors tells us nothing about the width, and refusing to start on it
            # would turn a transient Redis hiccup into an outage.
            logger.warning("Could not read %s dimension (FT.INFO failed): %s", CONTEXT_INDEX, exc)
            return
        existing = _index_vector_dim(_parse_ft_info(raw))
        if existing is None or existing == self._dim:
            return
        raise RuntimeError(
            f"RediSearch index {CONTEXT_INDEX} was built with dim={existing} but the "
            f"configured embedding model produces dim={self._dim}. Vector search would "
            f"silently return nothing. Re-embed into a fresh index "
            f"(FT.DROPINDEX {CONTEXT_INDEX} DD, then restart), or restore the previous "
            f"EMBEDDING_MODEL/EMBEDDING_BACKEND."
        )

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
        await self._redis.hset(key, mapping=mapping)  # type: ignore[arg-type]

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
            logger.warning("FT.INFO failed: %s", exc, exc_info=True)
            return 0

    async def update_metadata(
        self,
        id: str,  # noqa: A002
        fields: dict[str, str | float | int],
    ) -> None:
        """Update metadata fields on an existing Redis hash entry."""
        key = f"{CONTEXT_PREFIX}{id}"
        await self._redis.hset(key, mapping=fields)  # type: ignore[arg-type]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _decoded(value: object) -> str:
    """Bytes or str from Redis → str."""
    return value.decode() if isinstance(value, bytes) else str(value)


def _ft_documents(raw: object) -> list[tuple[str, dict[str, str]]]:
    """Normalise an FT.SEARCH reply to (doc_key, fields) pairs.

    The reply shape depends on the negotiated protocol, which redis-py picks —
    RESP2 gives a flat array ``[total, key, [f, v, ...], ...]`` while RESP3 gives a
    mapping ``{"results": [{"id": ..., "extra_attributes": {...}}, ...]}``. Handle
    both: pinning one protocol would work until the next client upgrade silently
    switched it back, and the failure mode here is empty results, not an error.
    """
    if isinstance(raw, dict):
        decoded_top = {_decoded(k): v for k, v in raw.items()}
        documents = decoded_top.get("results")
        if not isinstance(documents, (list, tuple)):
            return []
        pairs: list[tuple[str, dict[str, str]]] = []
        for document in documents:
            if not isinstance(document, dict):
                continue
            entry = {_decoded(k): v for k, v in document.items()}
            attributes = entry.get("extra_attributes")
            if not isinstance(attributes, dict):
                continue
            pairs.append(
                (
                    _decoded(entry.get("id", "")),
                    {_decoded(k): _decoded(v) for k, v in attributes.items()},
                )
            )
        return pairs

    if not isinstance(raw, (list, tuple)) or len(raw) < 1:
        return []

    items = list(raw)
    # First element is the total count; then pairs of (key, [field, value, ...])
    pairs = []
    i = 1
    while i + 1 < len(items):
        doc_key, fields_raw = items[i], items[i + 1]
        i += 2
        if not isinstance(fields_raw, (list, tuple)):
            continue
        field_list = list(fields_raw)
        pairs.append(
            (
                _decoded(doc_key),
                {
                    _decoded(field_list[j]): _decoded(field_list[j + 1])
                    for j in range(0, len(field_list) - 1, 2)
                },
            )
        )
    return pairs


def _parse_ft_results(raw: object, min_similarity: float) -> list[SearchResult]:
    """Parse an FT.SEARCH reply (RESP2 or RESP3) into SearchResult objects."""
    results: list[SearchResult] = []
    for doc_key, fields in _ft_documents(raw):
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

        doc_id = doc_key
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
    """Parse an FT.INFO reply (RESP2 alternating list, or RESP3 mapping)."""
    if isinstance(raw, dict):
        return {_decoded(key): value for key, value in raw.items()}
    if not isinstance(raw, (list, tuple)):
        return {}
    items = list(raw)
    return {_decoded(items[i]): items[i + 1] for i in range(0, len(items) - 1, 2)}


def _index_vector_dim(info: dict[str, object]) -> int | None:
    """Vector dimension declared by an existing index, or None if undeterminable.

    ``FT.INFO``'s ``attributes`` entry is a list of per-field descriptors whose shape
    follows the negotiated protocol: RESP3 gives dicts, RESP2 gives flat alternating
    lists. Normalise both, then read ``dim`` off the first vector field. Returning
    None (rather than guessing) keeps a parse quirk from failing startup.
    """
    attributes = info.get("attributes")
    if not isinstance(attributes, (list, tuple)):
        return None
    for attribute in attributes:
        if isinstance(attribute, dict):
            fields = {_decoded(k): v for k, v in attribute.items()}
        elif isinstance(attribute, (list, tuple)):
            items = list(attribute)
            fields = {_decoded(items[i]): items[i + 1] for i in range(0, len(items) - 1, 2)}
        else:
            continue
        raw_dim = fields.get("dim")
        if isinstance(raw_dim, (bytes, str, int)):
            try:
                return int(raw_dim)
            except ValueError:
                return None
    return None
