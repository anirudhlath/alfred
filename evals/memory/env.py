"""The memory stack under test, wired exactly as the services wire it — plus two lenses.

Real: RediSearch hot store, sqlite-vec cold store, ``EpisodicMemory``,
``ContextIndexManager``, ``SignificanceScorer`` (one per population, as the ingestor
runs them) and the ``Librarian``. The embedding provider comes from
``build_embedding_provider()`` and is wrapped, not replaced:

* ``CountingEmbedder`` counts every ``embed()`` by phase and times the calls. Writes and
  probes may be served from a text cache (the model is deterministic, so the vectors
  are identical); decay passes never are, so their cost is real.
* ``ReadOnlyStore`` lets deliberate-recall probes run the real tool path without
  stamping wall-clock retrieval stats into the hot store mid-simulation — the stats
  decay reads are the very thing being measured.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from core.librarian.consolidator import Librarian
from core.memory.context_index import ContextIndexManager
from core.memory.embedding_provider import EmbeddingProvider
from core.memory.episodic.memory import EpisodicMemory
from core.memory.redis_vector_store import RedisVectorStore
from core.memory.routines.store import RoutineStore
from core.memory.significance import SignificanceScorer
from core.memory.sqlite_vec_store import SqliteVecStore
from core.memory.vector_store import ContextMetadata, SearchResult, VectorStore
from core.reflex.context_reader import ContextReader
from shared.streams import CONTEXT_PREFIX, OBSERVED_FREQUENCY_KEY

if TYPE_CHECKING:
    from pathlib import Path

    from numpy.typing import NDArray

    from shared.config import AlfredConfig
    from shared.types import AioRedis

# Phases whose embeddings may come from the text cache. "decay" is deliberately absent.
CACHED_PHASES: frozenset[str] = frozenset({"setup", "write", "probe"})


class CountingEmbedder(EmbeddingProvider):
    """Counts and times ``embed`` calls per phase; caches outside decay passes."""

    def __init__(self, inner: EmbeddingProvider) -> None:
        self._inner = inner
        self._cache: dict[str, list[float]] = {}
        self.phase = "setup"
        self.calls: Counter[str] = Counter()
        self.seconds: defaultdict[str, float] = defaultdict(float)

    def reset_counts(self) -> None:
        self.calls.clear()
        self.seconds.clear()

    async def embed(self, text: str) -> list[float]:
        self.calls[self.phase] += 1
        if self.phase in CACHED_PHASES and text in self._cache:
            return self._cache[text]
        started = time.perf_counter()
        vector = await self._inner.embed(text)
        self.seconds[self.phase] += time.perf_counter() - started
        if self.phase in CACHED_PHASES:
            self._cache[text] = vector
        return vector

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(text) for text in texts]

    def dimension(self) -> int:
        return self._inner.dimension()

    def model_name(self) -> str:
        return self._inner.model_name()

    async def warmup(self) -> None:
        await self._inner.warmup()

    async def aclose(self) -> None:
        await self._inner.aclose()


class ReadOnlyStore(VectorStore):
    """Searches a store without letting the caller change it.

    ``update_metadata`` is swallowed (and counted): it is how ``record_retrievals``
    stamps ``last_retrieved`` with the wall clock, which inside a simulation would
    plant 2026-wall-clock retrievals into memories the decay pass then reads.
    """

    def __init__(self, inner: VectorStore) -> None:
        self._inner = inner
        self.suppressed_writes = 0

    async def add(
        self,
        id: str,  # noqa: A002
        content: str,
        semantic_key: str,
        embedding_content: list[float],
        embedding_semantic: list[float],
        metadata: ContextMetadata,
    ) -> None:
        raise RuntimeError("ReadOnlyStore cannot add")

    async def search(
        self,
        query_embedding: list[float],
        limit: int,
        filters: dict[str, str | float | int] | None = None,
        min_similarity: float = 0.0,
    ) -> list[SearchResult]:
        return await self._inner.search(query_embedding, limit, filters, min_similarity)

    async def delete(self, id: str) -> None:  # noqa: A002
        raise RuntimeError("ReadOnlyStore cannot delete")

    async def exists(self, id: str) -> bool:  # noqa: A002
        return await self._inner.exists(id)

    async def count(self) -> int:
        return await self._inner.count()

    async def update_metadata(
        self,
        id: str,  # noqa: A002
        fields: dict[str, str | float | int],
    ) -> None:
        self.suppressed_writes += 1


# ---------------------------------------------------------------------------
# Reading the hot store directly
# ---------------------------------------------------------------------------

_HOT_FIELDS = (
    "type",
    "source",
    "entities",
    "timestamp",
    "significance",
    "retrieval_count",
    "last_retrieved",
    "compressed",
    "content",
    "semantic_key",
)


def _text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    return value.decode() if isinstance(value, bytes) else value


async def scan_hot(redis: AioRedis) -> list[SearchResult]:
    """Every entry in the hot store, found by SCAN — not by search, so nothing hides.

    Returned as ``SearchResult`` (score 0.0) so the Librarian's own decay code can
    consume it unchanged.
    """
    keys = sorted(
        [_text(key) async for key in redis.scan_iter(match=f"{CONTEXT_PREFIX}*", count=1000)]
    )
    if not keys:
        return []
    pipe = redis.pipeline(transaction=False)
    for key in keys:
        pipe.hmget(key, list(_HOT_FIELDS))
    rows: list[list[bytes | str | None]] = await pipe.execute()
    results: list[SearchResult] = []
    for key, row in zip(keys, rows, strict=True):
        fields = dict(zip(_HOT_FIELDS, (_text(value) for value in row), strict=True))
        if not fields["type"]:
            continue  # not an index entry (a stray partial hash)
        results.append(
            SearchResult(
                id=key.removeprefix(CONTEXT_PREFIX),
                score=0.0,
                content=fields["content"],
                semantic_key=fields["semantic_key"],
                metadata=ContextMetadata(
                    type=fields["type"],
                    source=fields["source"],
                    entities=fields["entities"],
                    timestamp=float(fields["timestamp"] or 0),
                    significance=float(fields["significance"] or 0),
                    retrieval_count=int(fields["retrieval_count"] or 0),
                    last_retrieved=float(fields["last_retrieved"] or 0),
                    compressed=fields["compressed"],
                ),
            )
        )
    return results


@dataclass(frozen=True)
class StoredVectors:
    """The hot store's own vectors for some ids, row-normalised for cosine."""

    ids: list[str]
    content: NDArray[np.float32]
    semantic: NDArray[np.float32]

    def similarity(self, query: list[float]) -> NDArray[np.float32]:
        """Per id, the better of content and semantic cosine — the store's own merge."""
        q = _normalise(np.asarray([query], dtype=np.float32))[0]
        result: NDArray[np.float32] = np.maximum(self.content @ q, self.semantic @ q)
        return result


async def stored_vectors(redis: AioRedis, ids: list[str]) -> StoredVectors:
    """Read the (content, semantic) vectors the hot store holds for ``ids``."""
    pipe = redis.pipeline(transaction=False)
    for memory_id in ids:
        pipe.hmget(f"{CONTEXT_PREFIX}{memory_id}", ["embedding_content", "embedding_semantic"])
    rows: list[list[bytes | None]] = await pipe.execute()
    found: list[str] = []
    content: list[NDArray[np.float32]] = []
    semantic: list[NDArray[np.float32]] = []
    for memory_id, (content_blob, semantic_blob) in zip(ids, rows, strict=True):
        if content_blob is None or semantic_blob is None:
            continue
        found.append(memory_id)
        content.append(np.frombuffer(content_blob, dtype="<f4"))
        semantic.append(np.frombuffer(semantic_blob, dtype="<f4"))
    if not found:
        empty = np.zeros((0, 0), dtype=np.float32)
        return StoredVectors(ids=[], content=empty, semantic=empty)
    return StoredVectors(
        ids=found,
        content=_normalise(np.vstack(content)),
        semantic=_normalise(np.vstack(semantic)),
    )


def _normalise(matrix: NDArray[np.float32]) -> NDArray[np.float32]:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    result: NDArray[np.float32] = (matrix / norms).astype(np.float32)
    return result


# ---------------------------------------------------------------------------
# The environment
# ---------------------------------------------------------------------------


@dataclass
class SimEnv:
    config: AlfredConfig
    redis: AioRedis
    workdir: Path
    embedder: CountingEmbedder
    hot: RedisVectorStore
    cold: SqliteVecStore
    episodic: EpisodicMemory
    context_index: ContextIndexManager
    recall_view: ContextIndexManager
    recall_view_store: ReadOnlyStore
    context_reader: ContextReader
    scorer: SignificanceScorer
    passive_scorer: SignificanceScorer
    routine_store: RoutineStore

    def librarian(self, context_index: ContextIndexManager | None = None) -> Librarian:
        """A Librarian with no LLM key: no analysis calls, and compression falls back to
        its own deterministic concatenation summary (the "stub" summarizer)."""
        return Librarian(
            redis=self.redis,
            episodic_memory=self.episodic,
            routine_store=self.routine_store,
            significance_scorer=self.scorer,
            context_index=context_index or self.context_index,
            preferences_dir=str(self.workdir / "preferences"),
            profile_dir=str(self.workdir / "profile"),
            claude_api_key="",
        )

    async def close(self) -> None:
        await self.cold.close()


def open_env(
    config: AlfredConfig, redis: AioRedis, embedder: CountingEmbedder, workdir: Path
) -> SimEnv:
    """Build the stack against ``redis`` (a sandbox) and ``workdir`` (a temp dir)."""
    preferences = workdir / "preferences"
    profile = workdir / "profile"
    preferences.mkdir(parents=True, exist_ok=True)
    profile.mkdir(parents=True, exist_ok=True)
    dim = embedder.dimension()
    hot = RedisVectorStore(redis, dim=dim)
    cold = SqliteVecStore(workdir / "cold.db", dim=dim, embedder=embedder)
    recall_view_store = ReadOnlyStore(hot)
    return SimEnv(
        config=config,
        redis=redis,
        workdir=workdir,
        embedder=embedder,
        hot=hot,
        cold=cold,
        episodic=EpisodicMemory(hot=hot, cold=cold, embedder=embedder),
        context_index=ContextIndexManager(hot, embedder, semantic_dirs=[preferences, profile]),
        recall_view=ContextIndexManager(recall_view_store, embedder, archive=cold),
        recall_view_store=recall_view_store,
        context_reader=ContextReader(redis),
        scorer=SignificanceScorer(redis=redis, config=config),
        passive_scorer=SignificanceScorer(
            redis=redis, config=config, frequency_key=OBSERVED_FREQUENCY_KEY
        ),
        routine_store=RoutineStore(str(workdir / "routines")),
    )
