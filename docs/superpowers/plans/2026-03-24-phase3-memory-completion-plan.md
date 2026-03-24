# Phase 3 Memory Completion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Phase 3 of Alfred's memory system — making it searchable, significance-aware, and self-organizing via a two-stage context assembly (involuntary + deliberate recall).

**Architecture:** Replace time-based episodic queries with dual-embedding vector search (RediSearch hot + sqlite-vec cold). Introduce significance-based decay, Librarian pattern detection, and memory-as-tools for the Conscious Engine. See `docs/superpowers/specs/2026-03-24-phase3-memory-completion-design.md` for full spec.

**Tech Stack:** Redis Stack (RediSearch), sqlite-vec, EmbeddingGemma-300M via sentence-transformers, LiteLLM (Claude), Pydantic v2, pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-03-24-phase3-memory-completion-design.md`

---

## File Structure

### New Files
- `core/memory/embedding_provider.py` — `EmbeddingProvider` ABC + `SentenceTransformerProvider`
- `core/memory/vector_store.py` — `VectorStore` ABC, `SearchResult`, `ContextMetadata`
- `core/memory/redis_vector_store.py` — `RedisVectorStore` (RediSearch implementation)
- `core/memory/sqlite_vec_store.py` — `SqliteVecStore` (sqlite-vec implementation)
- `core/memory/episodic/memory.py` — `EpisodicMemory` (unified hot+cold interface)
- `core/memory/significance.py` — `SignificanceScore`, `SignificanceScorer` (heuristic)
- `core/memory/context_index.py` — `ContextIndexManager` (manages unified idx:context)
- `core/conscious/memory_tools.py` — Internal memory tools (`recall_memories`, `get_live_state`) with dedicated dispatch, NOT using BaseFeature/SDK pattern
- `core/memory/episodic/migrations/v2.sql` — SQLite schema migration
- `tests/core/memory/test_embedding_provider.py`
- `tests/core/memory/test_redis_vector_store.py`
- `tests/core/memory/test_sqlite_vec_store.py`
- `tests/core/memory/test_episodic_memory.py`
- `tests/core/memory/test_significance.py`
- `tests/core/memory/test_context_index.py`
- `tests/core/conscious/test_memory_tools.py`
- `tests/core/conscious/test_two_stage_assembly.py`
- `tests/core/librarian/test_consolidator_v2.py`

### Modified Files
- `core/memory/schemas.py` — add `SignificanceScore`, `EpisodicResult`, update `EpisodicEntry`, `RoutineSpec`
- `core/memory/episodic/schema.sql` — add v2 migration path
- `core/conscious/engine.py` — replace episodic query with involuntary recall, add memory tool dispatch, update `ConsciousDeps`
- `core/conscious/context_assembler.py` — refactor `assemble()` for two-stage model
- `core/librarian/consolidator.py` — two-call pipeline, significance refinement, semantic keys, conflict resolution, contextual decay, pattern detection
- `core/memory/routines/store.py` — no changes to store itself, but `RoutineSpec` schema changes propagate
- `shared/config.py` — add embedding, significance, decay, recall, pattern config fields
- `shared/streams.py` — add `CONTEXT_INDEX`, `CONTEXT_PREFIX`, `ENTITY_FREQUENCY_KEY`
- `pyproject.toml` — add `sqlite-vec` dependency, bump `transformers` minimum
- `scripts/dev-up.sh` — redis-stack migration
- `conftest.py` — add mock embedding provider and vector store fixtures
- `evals/conscious/metrics.py` — upgrade `MemoryRetrievalPrecision`, `ProactivityRelevanceScore`, add `SemanticKeyQuality`
- `evals/conscious/runner.py` — wire mock integrations, update for new context assembler

### Deprecated (to be removed)
- `core/memory/episodic/decay.py` — `DecayScheduler` replaced by significance-based decay
- `core/memory/episodic/store.py` — `EpisodicStore` replaced by `EpisodicMemory` + `VectorStore`
- `core/memory/episodic/search.py` — `EpisodicSearch` replaced by `EpisodicMemory`
- `core/memory/episodic/embeddings.py` — `EmbeddingModel` replaced by `EmbeddingProvider`

---

## Task 1: Redis Stack Migration

**Files:**
- Modify: `scripts/dev-up.sh`
- Modify: `shared/streams.py:23-31`

- [ ] **Step 1: Update dev-up.sh to use redis-stack**

Replace `brew services start redis` with redis-stack. The script should check for redis-stack, install if needed, and handle the case where vanilla redis is currently running.

```bash
# In scripts/dev-up.sh, replace the redis section:
if ! brew list redis-stack &>/dev/null 2>&1; then
    echo "Installing redis-stack..."
    # Stop vanilla redis if running
    brew services stop redis 2>/dev/null || true
    brew tap redis/redis 2>/dev/null || true
    brew install redis-stack
fi
brew services start redis-stack
```

- [ ] **Step 2: Verify RediSearch is available**

```bash
redis-cli MODULE LIST | grep -i search
redis-cli FT._LIST
```

Expected: search module appears in list, `FT._LIST` returns empty array (no indexes yet).

- [ ] **Step 3: Add new stream constants**

In `shared/streams.py`, add after line 31:

```python
# Unified context index (RediSearch)
CONTEXT_INDEX = "idx:context"
CONTEXT_PREFIX = "ctx:"
ENTITY_FREQUENCY_KEY = "alfred:entity:freq"

# Deprecated: EPISODIC_STREAM is retained for backward compatibility
# during migration. Will be removed when EpisodicStore is deleted.
```

Also add a `# Deprecated` comment next to the existing `EPISODIC_STREAM` constant.

- [ ] **Step 4: Run existing tests to verify nothing broke**

```bash
.venv/bin/python -m pytest -x -q
```

Expected: all existing tests pass (redis-stack is a superset of redis).

- [ ] **Step 5: Commit**

```bash
git add scripts/dev-up.sh shared/streams.py
git commit -m "infra: migrate from redis to redis-stack for RediSearch support"
```

---

## Task 2: Schema Updates

**Files:**
- Modify: `core/memory/schemas.py`
- Modify: `shared/config.py`
- Test: `core/memory/tests/test_schemas.py` (existing — note: tests live in `core/memory/tests/`, not `tests/core/memory/`)

- [ ] **Step 1: Write tests for new schemas**

```python
# In core/memory/tests/test_schemas.py, add:

def test_significance_score_defaults() -> None:
    score = SignificanceScore(overall=0.5)
    assert score.safety == 0.0
    assert score.novelty == 0.0
    assert score.personal == 0.0
    assert score.emotional == 0.0
    assert score.source == "heuristic"


def test_significance_score_full() -> None:
    score = SignificanceScore(
        overall=0.8, safety=0.9, novelty=0.3,
        personal=0.7, emotional=0.6, source="librarian",
    )
    assert score.overall == 0.8


def test_episodic_entry_with_significance() -> None:
    entry = EpisodicEntry(
        id="ep-1", timestamp=datetime.now(UTC),
        source="conversation", summary="test",
        entities=["light.kitchen"],
        significance=SignificanceScore(overall=0.5),
    )
    assert entry.significance.overall == 0.5
    assert entry.retrieval_count == 0
    assert entry.compressed_into is None


def test_episodic_result() -> None:
    entry = EpisodicEntry(
        id="ep-1", timestamp=datetime.now(UTC),
        source="conversation", summary="test",
        entities=[], significance=SignificanceScore(overall=0.5),
    )
    result = EpisodicResult(entry=entry, score=0.85, source_store="hot")
    assert result.score == 0.85


def test_routine_spec_last_suggested() -> None:
    spec = RoutineSpec(
        name="test", trigger_pattern="8pm",
        steps=[], confidence=0.7,
        learned_from=[], state="candidate",
    )
    assert spec.last_suggested is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest core/memory/tests/test_schemas.py -v -k "significance or episodic_result or last_suggested"
```

Expected: FAIL — `SignificanceScore`, `EpisodicResult` not defined.

- [ ] **Step 3: Update schemas.py**

In `core/memory/schemas.py`:

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from core.triggers.models import ActionPayload


class SignificanceScore(BaseModel):
    """Multi-dimensional significance score inspired by amygdala function."""
    overall: float
    safety: float = 0.0
    novelty: float = 0.0
    personal: float = 0.0
    emotional: float = 0.0
    source: Literal["heuristic", "librarian"] = "heuristic"


class EpisodicEntry(BaseModel):
    id: str
    timestamp: datetime
    source: str
    summary: str
    entities: list[str]
    significance: SignificanceScore
    semantic_key: str = ""
    retrieval_count: int = 0
    last_retrieved: datetime | None = None
    compressed_into: str | None = None


class EpisodicResult(BaseModel):
    """Result from episodic memory recall."""
    entry: EpisodicEntry
    score: float
    source_store: Literal["hot", "cold"]


class RoutineStep(BaseModel):
    description: str
    action: ActionPayload | None = None


class RoutineSpec(BaseModel):
    name: str
    trigger_pattern: str
    steps: list[RoutineStep]
    confidence: float
    learned_from: list[str]
    state: Literal["candidate", "active", "dormant", "archived"]
    last_hit: datetime | None = None
    consecutive_misses: int = 0
    last_suggested: datetime | None = None
```

- [ ] **Step 4: Update AlfredConfig with new fields**

In `shared/config.py`, add fields to `AlfredConfig` and `from_env()`:

```python
# Embedding
embedding_model: str = "google/embeddinggemma-300m"
embedding_dim: int = 768

# Significance weights
significance_weight_safety: float = 0.35
significance_weight_novelty: float = 0.25
significance_weight_personal: float = 0.25
significance_weight_emotional: float = 0.15

# Decay
decay_migration_threshold: float = 1.0

# Involuntary recall
involuntary_recall_limit: int = 10
involuntary_recall_threshold: float = 0.5

# Pattern detection
pattern_min_occurrences: int = 3
pattern_min_days: int = 7
pattern_confidence_threshold: float = 0.6
routine_decay_per_cycle: float = 0.05
routine_archive_threshold: float = 0.3
routine_suggestion_cooldown_hours: int = 24

# Semantic conflict resolution
conflict_min_observations: int = 5
conflict_min_days: int = 14
```

- [ ] **Step 5: Keep `valence` as deprecated optional field for backward compatibility**

Since `EpisodicStore` is not deprecated until Task 17 and still references `valence`, keep it as an optional deprecated field to avoid breaking the existing code during the transition:

```python
class EpisodicEntry(BaseModel):
    # ... new fields ...
    significance: SignificanceScore
    # Deprecated: kept for backward compat with EpisodicStore until Task 17
    valence: Literal["positive", "negative", "neutral"] = "neutral"
```

Also grep for and update all test files creating `EpisodicEntry` to include the new `significance` field:

```bash
grep -rn "EpisodicEntry(" tests/ core/ evals/ --include="*.py"
```

Add `significance=SignificanceScore(overall=0.5)` to each constructor call while keeping `valence` temporarily.

- [ ] **Step 6: Run all tests**

```bash
.venv/bin/python -m pytest -x -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add core/memory/schemas.py shared/config.py tests/
git commit -m "feat: add SignificanceScore, EpisodicResult schemas and config fields"
```

---

## Task 3: EmbeddingProvider Abstraction

**Files:**
- Create: `core/memory/embedding_provider.py`
- Create: `tests/core/memory/test_embedding_provider.py`
- Modify: `pyproject.toml` (bump transformers minimum)

- [ ] **Step 1: Write tests**

```python
# tests/core/memory/test_embedding_provider.py
import pytest
from core.memory.embedding_provider import SentenceTransformerProvider


@pytest.fixture
def provider() -> SentenceTransformerProvider:
    # Use small model for tests to avoid downloading large model
    return SentenceTransformerProvider(model_name="all-MiniLM-L6-v2")


def test_embed_returns_list_of_floats(provider: SentenceTransformerProvider) -> None:
    result = provider.embed_sync("hello world")
    assert isinstance(result, list)
    assert all(isinstance(x, float) for x in result)


def test_embed_dimension_matches(provider: SentenceTransformerProvider) -> None:
    result = provider.embed_sync("hello world")
    assert len(result) == provider.dimension()


def test_embed_batch(provider: SentenceTransformerProvider) -> None:
    results = provider.embed_batch_sync(["hello", "world"])
    assert len(results) == 2
    assert len(results[0]) == provider.dimension()


def test_model_name(provider: SentenceTransformerProvider) -> None:
    assert provider.model_name() == "all-MiniLM-L6-v2"


@pytest.mark.asyncio
async def test_async_embed(provider: SentenceTransformerProvider) -> None:
    result = await provider.embed("hello world")
    assert len(result) == provider.dimension()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/core/memory/test_embedding_provider.py -v
```

- [ ] **Step 3: Implement EmbeddingProvider**

```python
# core/memory/embedding_provider.py
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """Abstract embedding model interface."""

    @abstractmethod
    async def embed(self, text: str) -> list[float]: ...

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def dimension(self) -> int: ...

    @abstractmethod
    def model_name(self) -> str: ...


class SentenceTransformerProvider(EmbeddingProvider):
    """EmbeddingProvider backed by sentence-transformers."""

    def __init__(self, model_name: str = "google/embeddinggemma-300m") -> None:
        self._model_name = model_name
        self._model: object | None = None

    def _load(self) -> object:
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_name)
        return self._model

    def embed_sync(self, text: str) -> list[float]:
        model = self._load()
        arr = model.encode(text, normalize_embeddings=True)  # type: ignore[union-attr]
        return arr.tolist()  # type: ignore[union-attr]

    def embed_batch_sync(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        arr = model.encode(texts, normalize_embeddings=True)  # type: ignore[union-attr]
        return arr.tolist()  # type: ignore[union-attr]

    async def embed(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_sync, text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_batch_sync, texts)

    def dimension(self) -> int:
        model = self._load()
        dim: int = model.get_sentence_embedding_dimension()  # type: ignore[union-attr]
        return dim

    def model_name(self) -> str:
        return self._model_name
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/core/memory/test_embedding_provider.py -v
```

Expected: all pass.

- [ ] **Step 5: Update pyproject.toml dependencies**

In `pyproject.toml` under `[project.optional-dependencies]`, in the `memory` group, add `sqlite-vec` and ensure `transformers` minimum version for EmbeddingGemma-300M:

```toml
memory = [
    "sentence-transformers>=3.0",
    "transformers>=4.48",  # Required for Gemma 3 architecture (EmbeddingGemma-300M)
    "sqlite-vec>=0.1",
    "aiosqlite>=0.20",
    "numpy>=1.26",
]
```

Run: `uv pip install -e ".[dev,memory]"`

- [ ] **Step 6: Commit**

```bash
git add core/memory/embedding_provider.py tests/core/memory/test_embedding_provider.py pyproject.toml
git commit -m "feat: add EmbeddingProvider abstraction with SentenceTransformer backend"
```

---

## Task 4: VectorStore Abstraction + RedisVectorStore

**Files:**
- Create: `core/memory/vector_store.py`
- Create: `core/memory/redis_vector_store.py`
- Create: `tests/core/memory/test_redis_vector_store.py`
- Modify: `conftest.py` (add mock embedding and vector store fixtures)

- [ ] **Step 0: Add test fixtures to conftest.py**

Add mock `EmbeddingProvider` and `VectorStore` fixtures used across multiple test files:

```python
# In conftest.py, add:
from unittest.mock import AsyncMock

@pytest.fixture
def mock_embedder() -> AsyncMock:
    """Mock EmbeddingProvider returning deterministic 4-dim vectors."""
    embedder = AsyncMock()
    embedder.embed = AsyncMock(return_value=[0.1, 0.2, 0.3, 0.4])
    embedder.embed_batch = AsyncMock(return_value=[[0.1, 0.2, 0.3, 0.4]])
    embedder.dimension.return_value = 4
    embedder.model_name.return_value = "mock-model"
    return embedder


@pytest.fixture
def mock_vector_store() -> AsyncMock:
    """Mock VectorStore returning empty search results."""
    store = AsyncMock()
    store.search = AsyncMock(return_value=[])
    store.add = AsyncMock()
    store.delete = AsyncMock()
    store.exists = AsyncMock(return_value=False)
    store.count = AsyncMock(return_value=0)
    return store
```

- [ ] **Step 1: Write VectorStore ABC and models**

```python
# core/memory/vector_store.py
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
        self, id: str, content: str, semantic_key: str,
        embedding_content: list[float], embedding_semantic: list[float],
        metadata: ContextMetadata,
    ) -> None: ...

    @abstractmethod
    async def search(
        self, query_embedding: list[float], limit: int,
        filters: dict[str, str | float | int] | None = None,
        min_similarity: float = 0.0,
    ) -> list[SearchResult]: ...

    @abstractmethod
    async def delete(self, id: str) -> None: ...

    @abstractmethod
    async def exists(self, id: str) -> bool: ...

    @abstractmethod
    async def count(self) -> int: ...
```

- [ ] **Step 2: Write RedisVectorStore tests**

```python
# tests/core/memory/test_redis_vector_store.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.memory.redis_vector_store import RedisVectorStore
from core.memory.vector_store import ContextMetadata


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.execute_command = AsyncMock()
    redis.hset = AsyncMock()
    redis.delete = AsyncMock()
    redis.exists = AsyncMock(return_value=0)
    return redis


@pytest.fixture
def store(mock_redis: AsyncMock) -> RedisVectorStore:
    return RedisVectorStore(redis=mock_redis, dim=4)


def _meta() -> ContextMetadata:
    return ContextMetadata(
        type="episodic", source="conversation",
        entities="light.kitchen", timestamp=1711000000.0,
        significance=0.5, retrieval_count=0,
    )


@pytest.mark.asyncio
async def test_add_creates_hash(store: RedisVectorStore, mock_redis: AsyncMock) -> None:
    await store.add(
        id="ep-1", content="test content", semantic_key="test key",
        embedding_content=[0.1, 0.2, 0.3, 0.4],
        embedding_semantic=[0.5, 0.6, 0.7, 0.8],
        metadata=_meta(),
    )
    mock_redis.hset.assert_called_once()


@pytest.mark.asyncio
async def test_delete_removes_hash(store: RedisVectorStore, mock_redis: AsyncMock) -> None:
    await store.delete("ep-1")
    mock_redis.delete.assert_called_once()


@pytest.mark.asyncio
async def test_exists_checks_hash(store: RedisVectorStore, mock_redis: AsyncMock) -> None:
    mock_redis.exists.return_value = 1
    assert await store.exists("ep-1") is True
```

- [ ] **Step 3: Implement RedisVectorStore**

Create `core/memory/redis_vector_store.py` implementing the `VectorStore` interface using RediSearch `FT.CREATE`, `FT.SEARCH` with KNN queries on both vector fields, merged client-side via `asyncio.gather`. Handle index creation at init, graceful fallback if RediSearch unavailable.

Key implementation details:
- `FT.CREATE` with both `embedding_semantic` and `embedding_content` VECTOR HNSW fields
- `search()` issues two parallel `FT.SEARCH` KNN queries, merges results by taking max score per id
- `add()` converts embeddings to `bytes` (struct.pack float32) for Redis HSET
- Use `CONTEXT_PREFIX` from `shared.streams` for hash key prefix

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/core/memory/test_redis_vector_store.py -v
```

- [ ] **Step 5: Commit**

```bash
git add core/memory/vector_store.py core/memory/redis_vector_store.py tests/core/memory/test_redis_vector_store.py
git commit -m "feat: add VectorStore abstraction and RedisVectorStore implementation"
```

---

## Task 5: SqliteVecStore

**Files:**
- Create: `core/memory/sqlite_vec_store.py`
- Create: `core/memory/episodic/migrations/v2.sql`
- Create: `tests/core/memory/test_sqlite_vec_store.py`

- [ ] **Step 1: Write tests**

Test KNN search, add/delete, rowid coordination, transactional writes. Use an in-memory SQLite database with sqlite-vec loaded.

- [ ] **Step 2: Write v2 migration SQL**

```sql
-- core/memory/episodic/migrations/v2.sql
-- Migration from schema v1 to v2: add significance, semantic_key, vec0 tables

ALTER TABLE episodic_entries ADD COLUMN significance TEXT DEFAULT '{}';
ALTER TABLE episodic_entries ADD COLUMN semantic_key TEXT DEFAULT '';
ALTER TABLE episodic_entries ADD COLUMN compressed_into TEXT DEFAULT NULL;

-- vec0 virtual tables for KNN search
CREATE VIRTUAL TABLE IF NOT EXISTS vec_episodic_semantic USING vec0(embedding float[768]);
CREATE VIRTUAL TABLE IF NOT EXISTS vec_episodic_content USING vec0(embedding float[768]);

UPDATE schema_version SET version = 2 WHERE version = 1;
```

**Data migration for existing entries:** After running the DDL migration, a Python migration step must:
1. Read all existing entries from `episodic_entries`
2. Embed each entry's `summary` using `EmbeddingProvider` (content embedding)
3. Generate a template semantic key (`"{source} event involving {entities}"`) and embed it
4. Insert into both vec0 virtual tables with matching rowids
5. Set `significance` to a default `SignificanceScore(overall=0.3)` (heuristic neutral)

This runs once at startup when `schema_version` transitions from 1 to 2. Implemented in `SqliteVecStore._ensure_schema()`. If no entries exist (fresh install), this is a no-op.

- [ ] **Step 3: Implement SqliteVecStore**

Key details:
- Load sqlite-vec via `sqlite_vec.loadable_path()` with fallback to full-table-scan
- Transactional writes (metadata + both vec0 tables in one transaction)
- `search()` queries both vec0 tables, merges results
- `_ensure_schema()` runs v1 or v2 migration as needed

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/core/memory/test_sqlite_vec_store.py -v
```

- [ ] **Step 5: Commit**

```bash
git add core/memory/sqlite_vec_store.py core/memory/episodic/migrations/ tests/core/memory/test_sqlite_vec_store.py
git commit -m "feat: add SqliteVecStore with sqlite-vec KNN search"
```

---

## Task 6: Significance Model

**Files:**
- Create: `core/memory/significance.py`
- Create: `tests/core/memory/test_significance.py`

- [ ] **Step 1: Write tests**

Test heuristic scoring for each dimension: safety (urgent trigger → 1.0), novelty (first-time entity → 1.0, frequent entity → 0.1), personal (conversation → 0.8), emotional (urgent → 0.9). Test overall weighted calculation. Test entity frequency tracking.

- [ ] **Step 2: Implement SignificanceScorer**

```python
# core/memory/significance.py
class SignificanceScorer:
    """Heuristic significance scoring (the Amygdala)."""

    def __init__(self, redis: AioRedis, config: AlfredConfig) -> None: ...

    async def score(self, entry: EpisodicEntry) -> SignificanceScore:
        """Compute heuristic significance from structured fields."""
        ...

    async def _score_safety(self, entry: EpisodicEntry) -> float: ...
    async def _score_novelty(self, entry: EpisodicEntry) -> float: ...
    def _score_personal(self, entry: EpisodicEntry) -> float: ...
    def _score_emotional(self, entry: EpisodicEntry) -> float: ...
```

Uses `ENTITY_FREQUENCY_KEY` sorted set for novelty scoring via `ZINCRBY`.

- [ ] **Step 3: Run tests**

```bash
.venv/bin/python -m pytest tests/core/memory/test_significance.py -v
```

- [ ] **Step 4: Commit**

```bash
git add core/memory/significance.py tests/core/memory/test_significance.py
git commit -m "feat: add significance scoring model (the Amygdala)"
```

---

## Task 7: EpisodicMemory (Unified Hot+Cold)

**Files:**
- Create: `core/memory/episodic/memory.py`
- Create: `tests/core/memory/test_episodic_memory.py`

- [ ] **Step 1: Write tests**

Test `write()` (creates dual embeddings, writes to hot store), `recall()` (searches both stores, deduplicates, ranks), `increment_retrieval()`, `migrate_to_cold()`. Use mock VectorStores and mock EmbeddingProvider.

- [ ] **Step 2: Implement EpisodicMemory**

```python
# core/memory/episodic/memory.py
class EpisodicMemory:
    def __init__(self, hot: VectorStore, cold: VectorStore, embedder: EmbeddingProvider) -> None: ...

    async def write(self, entry: EpisodicEntry, significance: SignificanceScore) -> None:
        """Embed content + semantic_key, write to hot store with significance."""
        entry.significance = significance
        content_emb = await self._embedder.embed(entry.summary)
        key_emb = await self._embedder.embed(entry.semantic_key or entry.summary)
        # ... write to hot store with metadata including significance

    async def recall(self, query: str, limit: int = 10,
                     since: datetime | None = None,
                     types: list[str] | None = None) -> list[EpisodicResult]:
        """Search hot + cold, deduplicate, rank by combined score."""
        query_emb = await self._embedder.embed(query)
        hot_results, cold_results = await asyncio.gather(
            self._hot.search(query_emb, limit=limit, ...),
            self._cold.search(query_emb, limit=limit, ...),
        )
        # Deduplicate, rank, increment retrieval counts
        ...

    async def migrate_to_cold(self, entry_id: str) -> None:
        """Move entry from hot to cold storage."""
        ...
```

- [ ] **Step 3: Run tests**

```bash
.venv/bin/python -m pytest tests/core/memory/test_episodic_memory.py -v
```

- [ ] **Step 4: Commit**

```bash
git add core/memory/episodic/memory.py tests/core/memory/test_episodic_memory.py
git commit -m "feat: add EpisodicMemory with unified hot+cold semantic search"
```

---

## Task 8: ContextIndexManager

**Files:**
- Create: `core/memory/context_index.py`
- Create: `tests/core/memory/test_context_index.py`

- [ ] **Step 1: Write tests**

Test indexing episodic entries, semantic memory sections (parsed from Markdown), and routines into the unified index. Test re-indexing semantic files. Test exclusion of compressed entries from search — `search()` must apply filter `@compressed:{} | -@compressed:{yes}` by default to exclude entries with `compressed="yes"`. Test that compressed entries ARE returned when a `include_compressed=True` flag is passed (for deliberate recall).

- [ ] **Step 2: Implement ContextIndexManager**

Manages the unified `idx:context` RediSearch index. Wraps `RedisVectorStore` and adds higher-level operations. Responsible for:
- Creating the index at startup (idempotent)
- Adding/updating/removing entries across all memory types
- `search()` — delegates to the underlying `RedisVectorStore.search()` (this is the interface used by involuntary recall in the engine)
- Parsing semantic memory Markdown files into sections for indexing
- `reindex_semantic_files()` — re-reads all preference/profile Markdown files, splits into sections, embeds, and re-indexes (called by Librarian each cycle)

`ContextIndexManager` owns a `RedisVectorStore` internally. The engine and Librarian interact with `ContextIndexManager`, never with `RedisVectorStore` directly.

**Startup indexing:** `ContextIndexManager.__init__()` triggers an initial `reindex_semantic_files()` call to populate the index with semantic memory content. This ensures involuntary recall works immediately, not after waiting up to 1 hour for the first Librarian cycle. The Librarian re-indexes each cycle to pick up changes.

- [ ] **Step 3: Run tests, commit**

```bash
git add core/memory/context_index.py tests/core/memory/test_context_index.py
git commit -m "feat: add ContextIndexManager for unified context search"
```

---

## Tasks 9-11: Two-Stage Context Assembly (ATOMIC UNIT)

> **IMPORTANT:** Tasks 9, 10, and 11 must be treated as a single atomic unit. Between Task 9 (involuntary recall) and Task 11 (assembler refactor), the system is in an intermediate state where involuntary results are computed but not injected into the prompt. Do NOT deploy or cut a release between these tasks.

## Task 9: Two-Stage Context Assembly (Step 6a — Involuntary Recall)

**Files:**
- Modify: `core/conscious/engine.py` (process_request)
- Create: `tests/core/conscious/test_two_stage_assembly.py`

- [ ] **Step 1: Write tests**

Test that `process_request` performs involuntary recall before assembling context. Mock the unified index search. Verify that search results are injected into the context. Verify that simple commands still work when involuntary recall returns nothing.

- [ ] **Step 2: Add involuntary recall to process_request**

In `engine.py`, after identity resolution and before context assembly:

```python
# Involuntary recall — embed user query, search unified context index
involuntary_context: list[SearchResult] = []
if self._context_index and request.content:
    try:
        query_emb = await self._embedder.embed(request.content)
        involuntary_context = await self._context_index.search(
            query_emb,
            limit=self._config.involuntary_recall_limit,
            min_similarity=self._config.involuntary_recall_threshold,
        )
    except Exception:
        logger.warning("Involuntary recall failed", exc_info=True)
```

- [ ] **Step 3: Update ConsciousDeps**

Add `embedder: EmbeddingProvider | None = None` and `context_index: ContextIndexManager | None = None` to `ConsciousDeps`.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/core/conscious/test_two_stage_assembly.py -v
```

- [ ] **Step 5: Commit**

```bash
git add core/conscious/engine.py tests/core/conscious/test_two_stage_assembly.py
git commit -m "feat: add involuntary recall to Conscious Engine process_request"
```

---

## Task 10: Two-Stage Context Assembly (Step 6b — Memory Tools)

**Files:**
- Create: `core/conscious/memory_tools.py`
- Create: `tests/core/conscious/test_memory_tools.py`
- Modify: `core/conscious/engine.py` (`_dispatch_tool_call`)

**IMPORTANT:** Memory tools are INTERNAL to the Conscious Engine process. They do NOT use the SDK's `BaseFeature` / `@tool` / `ToolRegistry` pattern — that pattern is for external services that register via Redis. Memory tools follow the same pattern as integration tools and trigger tools: direct in-process dispatch with a dedicated prefix.

- [ ] **Step 1: Write tests**

Test `recall_memories`: searches unified index, returns formatted results. Test `get_live_state`: calls `ContextReader.get_entity_states()`, returns structured data. Test tool dispatch: verify `_dispatch_tool_call` correctly routes `memory_recall_memories` and `memory_get_live_state` calls.

- [ ] **Step 2: Implement memory tools module**

```python
# core/conscious/memory_tools.py
"""Internal memory tools for deliberate recall during agentic reasoning.

These are in-process tools dispatched directly by the Conscious Engine,
following the same pattern as integration and trigger tools. They are NOT
registered in the Redis ToolRegistry (that's for external services).
"""
from __future__ import annotations

from datetime import datetime, timedelta, UTC
from typing import Any

from core.memory.context_index import ContextIndexManager
from core.memory.embedding_provider import EmbeddingProvider
from core.memory.vector_store import SearchResult

MEMORY_TOOL_PREFIX = "memory_"

MEMORY_TOOLS_MANIFEST: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "memory_recall_memories",
            "description": "Search Alfred's memory for relevant information",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language search query"},
                    "types": {"type": "array", "items": {"type": "string"}, "description": "Filter: episodic, semantic, routine"},
                    "since_days_ago": {"type": "integer", "description": "Only include entries from last N days"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_get_live_state",
            "description": "Get current Home Assistant device state",
            "parameters": {
                "type": "object",
                "properties": {
                    "entities": {"type": "array", "items": {"type": "string"}, "description": "Entity IDs or patterns like light.*"},
                },
            },
        },
    },
]


async def dispatch_memory_tool(
    tool_name: str,
    params: dict[str, Any],
    context_index: ContextIndexManager,
    context_reader: Any,
    embedder: EmbeddingProvider,
) -> str:
    """Dispatch a memory tool call. Returns JSON string result."""
    ...
```

- [ ] **Step 3: Add memory tool dispatch path to `_dispatch_tool_call`**

In `engine.py`, add a fourth dispatch path before domain routing:

```python
# In _dispatch_tool_call, after trigger tools check:
if tool_name.startswith(MEMORY_TOOL_PREFIX):
    result = await dispatch_memory_tool(
        tool_name, params, self._context_index, self._context_reader, self._embedder,
    )
    return self._make_tool_result(tc["id"], result)
```

- [ ] **Step 4: Add memory tools to the tools manifest**

In `process_request`, append `MEMORY_TOOLS_MANIFEST` to the OpenAI tools list alongside integration and domain tools.

- [ ] **Step 5: Add `get_entity_states()` to ContextReader**

In `core/reflex/context_reader.py`, add a method that returns structured JSON data filterable by entity glob patterns (e.g., `light.*` matches `light.living_room`, `light.kitchen`). Uses the same Redis `alfred:context:*` scan as `get_rendered_context()` but returns structured dicts instead of a pre-rendered string.

- [ ] **Step 6: Run tests, commit**

```bash
git add core/conscious/memory_tools.py tests/core/conscious/test_memory_tools.py core/conscious/engine.py
git commit -m "feat: add memory tools with dedicated dispatch path for deliberate recall"
```

---

## Task 11: Two-Stage Context Assembly (Step 6c — Refactor ContextAssembler)

**Files:**
- Modify: `core/conscious/context_assembler.py`
- Modify: `core/conscious/engine.py:519-532` (assemble call)
- Modify: all test files that call `ContextAssembler.assemble()`

- [ ] **Step 1: Refactor assemble() signature**

Replace the old string-parameter interface with the new one that accepts involuntary recall results. Remove HA state from base context (now available via `get_live_state` tool).

New signature:
```python
def assemble(
    self,
    identity: IdentityResult,
    tools_section: str,
    integrations_section: str = "",
    proactivity_level: str = "opinionated",
    now: datetime | None = None,
    history: list[dict[str, str]] | None = None,
    relevant_context: list[SearchResult] | None = None,
    channel: str = "",
    content_type: str = "text",
) -> str:
```

Note: `integrations_section` and `proactivity_level` are retained from the old interface — they render the integrations hint and proactivity instruction blocks. What's removed: `preferences_text`, `context_text`, `episodic_text`, `procedural_text` (all now surfaced via involuntary recall or deliberate recall tools). Conversation `history` is still passed separately to the LLM as messages, not rendered into the system prompt.

- [ ] **Step 2: Update engine.py to use new signature**

Remove the episodic/preferences/context/procedural text building (lines ~480-515). Pass `involuntary_context` as `relevant_context`. Remove `MemoryReader` usage from `process_request` — preferences are now surfaced via involuntary recall from the unified index. Remove `memory_reader` from `ConsciousDeps` (deprecated). Remove HA state injection via `context_reader.get_rendered_context()` — now available via `get_live_state` tool.

- [ ] **Step 3: Update all test callers**

Grep for `assemble(` across all test files and update to the new signature.

- [ ] **Step 4: Run all tests**

```bash
.venv/bin/python -m pytest -x -q
```

- [ ] **Step 5: Commit**

```bash
git commit -m "refactor: ContextAssembler for two-stage assembly, remove static context injection"
```

---

## Task 12: Librarian Upgrade — Analysis Call

**Files:**
- Modify: `core/librarian/consolidator.py`
- Create: `tests/core/librarian/test_consolidator_v2.py`

- [ ] **Step 1: Write tests for upgraded consolidation**

Test the first LLM call: entity extraction + significance scoring + semantic key generation. Mock the LLM response. Verify entries get written to `EpisodicMemory` with correct significance scores and semantic keys.

- [ ] **Step 2: Upgrade Librarian init**

Update `Librarian.__init__()` to accept `EpisodicMemory`, `EmbeddingProvider`, `ContextIndexManager`, `SignificanceScorer` instead of `EpisodicStore`.

- [ ] **Step 3: Implement upgraded analysis call**

Replace the current single LLM call with the first of two calls. The prompt now requests: entities, significance scores (4 dimensions), and semantic key rewrites for each scratchpad entry. Parse structured JSON output.

- [ ] **Step 4: Write to EpisodicMemory instead of EpisodicStore**

Replace `self._episodic.write(entry, embedding)` with `await self._episodic_memory.write(entry, significance)` (which handles dual embeddings internally).

- [ ] **Step 5: Update wiring in `__main__.py` files immediately**

Since the Librarian `__init__` signature changes in this task, the wiring in `core/conscious/__main__.py` and `core/librarian/__main__.py` must be updated in the same task — otherwise the system cannot start. Instantiate the new dependencies (`EpisodicMemory`, `EmbeddingProvider`, `ContextIndexManager`, `SignificanceScorer`) and inject them into `Librarian.__init__()`. This partially overlaps with Task 16 (full wiring) — Task 16 handles engine-side wiring, this task handles Librarian-side wiring.

- [ ] **Step 6: Re-index semantic files**

At the end of each consolidation cycle, call `self._context_index.reindex_semantic_files()` to keep the unified index current with any preference file changes.

- [ ] **Step 6: Run tests, commit**

```bash
git commit -m "feat: upgrade Librarian analysis call with significance and semantic keys"
```

---

## Task 13: Librarian Upgrade — Consolidation Call

**Files:**
- Modify: `core/librarian/consolidator.py`
- Add tests to: `tests/core/librarian/test_consolidator_v2.py`

- [ ] **Step 1: Write tests for conflict resolution**

Test confirm (skip), contradict (update with provenance), and new (add) cases. Mock the LLM response. Verify learned.md is updated correctly with provenance tags.

- [ ] **Step 2: Implement semantic conflict resolution**

Replace the append-only `_update_semantic_memory()` with a conflict-aware version. The second LLM call reads existing `learned.md`, compares against new observations, and returns structured output (confirm/contradict/new for each observation).

- [ ] **Step 3: Write tests for contextual decay**

Test migration pressure formula. Verify high-significance entries resist migration. Verify entries with many retrievals stay hot. Verify mundane entries migrate after threshold.

- [ ] **Step 4: Implement contextual decay**

Replace `_apply_decay()` stub with significance-based migration:
- Read all hot entries
- Compute migration pressure for each
- Migrate entries above threshold to cold (via `EpisodicMemory.migrate_to_cold()`)
- Compress related entries (same entity cluster + day) into summaries

- [ ] **Step 5: Remove DecayScheduler**

Delete `core/memory/episodic/decay.py` and its tests. Remove deprecated config fields `episodic_hot_days` and `episodic_compress_days` from `AlfredConfig`.

- [ ] **Step 6: Run all tests, commit**

```bash
git commit -m "feat: Librarian conflict resolution and contextual decay"
```

---

## Task 14: Pattern Detection

**Files:**
- Modify: `core/librarian/consolidator.py`
- Add tests to: `tests/core/librarian/test_consolidator_v2.py`

- [ ] **Step 1: Write tests for pattern detection**

Test that repeated action sequences (3+ occurrences over 7+ days) create `RoutineSpec` candidates. Mock the LLM call that identifies patterns. Verify confidence scores and `learned_from` episodic IDs.

- [ ] **Step 2: Implement pattern detection in consolidation call**

Add pattern detection to the second Librarian LLM call. The prompt asks Claude to identify repeated patterns across episodic entries and return candidate routines with trigger patterns, steps, and confidence.

- [ ] **Step 3: Write tests for routine lifecycle**

Test hit tracking: pattern occurred → update `last_hit`, reset misses. Pattern missed → increment `consecutive_misses`. 3 misses → dormant. 30 days dormant → archived. Test suggestion dedup via `last_suggested`.

- [ ] **Step 4: Implement routine lifecycle in Librarian**

After pattern detection, check active routines for hits/misses. Update state machine. Clean up archived routines from the unified index.

- [ ] **Step 5: Write tests for routine suggestion in engine**

Test that the Conscious Engine surfaces candidate routines when context matches. Verify `last_suggested` throttling.

- [ ] **Step 6: Add routine suggestion logic to engine**

In `process_request`, after involuntary recall, check if any candidate routine's time window matches current time. If so and not recently suggested, inject a suggestion hint into the system prompt.

- [ ] **Step 7: Run all tests, commit**

```bash
git commit -m "feat: pattern detection and routine lifecycle management"
```

---

## Task 15: Eval Coverage

**Files:**
- Modify: `evals/conscious/metrics.py`
- Modify: `evals/conscious/runner.py`
- Create: `evals/e2e/demo_good_morning.py`

- [ ] **Step 1: Upgrade MemoryRetrievalPrecision**

Replace keyword overlap with LLM-as-judge. The metric receives injected memories and Claude's response, asks an LLM which memories were actually used.

- [ ] **Step 2: Upgrade ProactivityRelevanceScore**

Replace hardcoded 0.5 stub with LLM-as-judge that evaluates whether a proactive suggestion was relevant.

- [ ] **Step 3: Add SemanticKeyQuality metric**

New metric that compares retrieval using content embeddings vs semantic key embeddings for a set of test queries.

- [ ] **Step 4: Wire mock integrations in eval runner**

In `run_conscious_evals_live()`, inject `mock_integrations` from YAML scenarios into the engine so controlled responses are used.

- [ ] **Step 5: Update e2e demo script**

`evals/e2e/demo_good_morning.py` already exists (146 lines). Update it to use the new memory system: add involuntary recall verification, memory tool call assertions, and integration with `EpisodicMemory` and `ContextIndexManager`.

- [ ] **Step 6: Run evals, commit**

```bash
.venv/bin/python -m pytest tests/evals/ -v
git commit -m "feat: upgrade eval metrics and add e2e demo"
```

---

## Task 16: Wire New Components into Process Startup

**Files:**
- Modify: `core/conscious/__main__.py`
- Modify: `core/librarian/__main__.py`
- Modify: `runner/__main__.py` (if it instantiates components)

These are the critical wiring files where all new components get instantiated and injected into the running system.

- [ ] **Step 1: Update `core/conscious/__main__.py`**

Replace `EpisodicStore` instantiation (line ~145-148) with:
- `SentenceTransformerProvider(config.embedding_model)` → `EmbeddingProvider`
- `RedisVectorStore(redis, dim=config.embedding_dim)` → hot VectorStore
- `SqliteVecStore(db_path, dim=config.embedding_dim)` → cold VectorStore
- `EpisodicMemory(hot, cold, embedder)` → unified episodic
- `SignificanceScorer(redis, config)` → amygdala
- `ContextIndexManager(hot_store, embedder, ...)` → unified index
- `MemoryFeature(context_index, context_reader, embedder)` → memory tools

Inject all into `ConsciousDeps`.

- [ ] **Step 2: Update `core/librarian/__main__.py`**

Replace `EpisodicStore` (line ~34) with `EpisodicMemory`, `EmbeddingProvider`, `ContextIndexManager`, `SignificanceScorer`. Pass to `Librarian.__init__()`.

- [ ] **Step 3: Run all tests**

```bash
.venv/bin/python -m pytest -x -q
```

- [ ] **Step 4: Smoke test the running system**

```bash
uv run python -m runner
# Verify all services start, no import errors
```

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: wire new memory components into process startup"
```

---

## Task 17: Cleanup and Deprecation

**Files:**
- Delete: `core/memory/episodic/decay.py`
- Delete: `core/memory/episodic/store.py`
- Delete: `core/memory/episodic/search.py`
- Delete: `core/memory/episodic/embeddings.py`
- Modify: all files importing deprecated classes

- [ ] **Step 1: Grep for all imports of deprecated classes**

```bash
grep -rn "EpisodicStore\|EpisodicSearch\|EmbeddingModel\|DecayScheduler" --include="*.py" .
```

- [ ] **Step 2: Update all imports to use new classes**

Replace `EpisodicStore` → `EpisodicMemory`, `EmbeddingModel` → `EmbeddingProvider`, remove `EpisodicSearch` and `DecayScheduler` imports.

- [ ] **Step 3: Delete deprecated files**

- [ ] **Step 4: Run all tests**

```bash
.venv/bin/python -m pytest -x -q
```

- [ ] **Step 5: Run linting and type checking**

```bash
ruff check . --fix && ruff format .
mypy --strict bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/
```

- [ ] **Step 6: Commit**

```bash
git commit -m "chore: remove deprecated EpisodicStore, EpisodicSearch, EmbeddingModel, DecayScheduler"
```

---

## Task 18: Code Architect Review

Run `@feature-dev:code-architect` review on all changes. Fix every issue raised.

---

## Task 19: Simplify

Run `/simplify` on all new and modified code. Fix every issue raised.

---

## Task 20: Update CLAUDE.md and Memory

Run `claude-md-management:revise-claude-md` to update project documentation with the new memory architecture, new dependencies, new config fields, and updated workflows.

---

## Task 21: Create PR

Create a pull request with all changes. Include:
- Summary of what was built
- Test count (before/after)
- Breaking changes (schema migration, ContextAssembler interface)
- Migration steps for deployment
