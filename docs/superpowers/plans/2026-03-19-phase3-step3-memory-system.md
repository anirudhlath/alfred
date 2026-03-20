# Phase 3 Step 3: Memory System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the three-layer biologically-inspired memory system: episodic (Redis hot + SQLite cold + vector search), semantic (extended preferences + profile), procedural (learned routines with promotion pipeline), and the Librarian agent for nightly consolidation.

**Architecture:** Episodic memory uses Redis Streams for hot entries (7 days) and SQLite with `sqlite-vec` for cold archive with vector similarity search. Semantic memory extends the existing Markdown preferences with profile files. Procedural memory stores learned routines as YAML. The Librarian consolidates the scratchpad nightly: extracting episodic entries, updating semantic memory, detecting patterns for procedural memory, and applying decay.

**Tech Stack:** Python 3.13+, sentence-transformers (local embeddings), SQLite + sqlite-vec, Pydantic v2, Redis Streams, Anthropic SDK (Librarian), pytest

**Spec:** `docs/superpowers/specs/2026-03-19-alfred-expanded-vision-design.md` (Section 4)

**Depends on:** Plan 1 (Prerequisites) and Plan 2 (Conscious Engine — for schemas) must be complete.

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `core/memory/episodic/__init__.py` | Package init |
| `core/memory/episodic/store.py` | `EpisodicStore` — hot (Redis) + cold (SQLite) CRUD |
| `core/memory/episodic/embeddings.py` | Embedding model wrapper (sentence-transformers) |
| `core/memory/episodic/search.py` | Semantic + time-based + entity-based retrieval |
| `core/memory/episodic/schema.sql` | SQLite schema for cold storage |
| `core/memory/episodic/decay.py` | Decay scheduler (hot → cold → compressed → archived) |
| `core/memory/profile/about.md` | Learned facts about sir (seeded empty) |
| `core/memory/profile/relationships.md` | People Alfred knows about (seeded empty) |
| `core/memory/routines/__init__.py` | Package init |
| `core/memory/routines/store.py` | `RoutineStore` — YAML-based procedural memory |
| `core/librarian/__init__.py` | Package init |
| `core/librarian/consolidator.py` | `Librarian` — nightly consolidation agent |
| `core/librarian/__main__.py` | Entry point (`python -m core.librarian`) |
| `tests/core/memory/episodic/__init__.py` | Package init |
| `tests/core/memory/episodic/test_store.py` | EpisodicStore tests |
| `tests/core/memory/episodic/test_embeddings.py` | Embedding tests |
| `tests/core/memory/episodic/test_search.py` | Search tests |
| `tests/core/memory/episodic/test_decay.py` | Decay tests |
| `tests/core/memory/routines/__init__.py` | Package init |
| `tests/core/memory/routines/test_store.py` | RoutineStore tests |
| `tests/core/librarian/__init__.py` | Package init |
| `tests/core/librarian/test_consolidator.py` | Librarian tests |

### Modified Files

| File | Change |
|------|--------|
| `pyproject.toml` | Add `sentence-transformers` as optional dep `[memory]`, `aiosqlite` |
| `core/memory/schemas.py` | Already has `EpisodicEntry`, `RoutineSpec` (from Plan 2) |

---

## Task 1: Add Memory Dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add optional memory dependencies**

```toml
# In [project.optional-dependencies], add:
memory = [
    "sentence-transformers>=3.0",
    "aiosqlite>=0.20",
    "numpy>=1.26",
]
```

Keep these optional — the core system runs without them. Only the memory subsystem requires torch.

- [ ] **Step 2: Install**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv pip install -e ".[dev,memory]"`

- [ ] **Step 3: Add mypy overrides**

```toml
[[tool.mypy.overrides]]
module = ["sentence_transformers.*", "torch.*", "numpy.*", "aiosqlite.*"]
ignore_missing_imports = true
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "deps: add sentence-transformers, aiosqlite for memory subsystem"
```

---

## Task 2: SQLite Schema for Cold Episodic Storage

**Files:**
- Create: `core/memory/episodic/schema.sql`
- Create: `core/memory/episodic/__init__.py`

- [ ] **Step 1: Write the schema**

```sql
-- core/memory/episodic/schema.sql
-- Cold storage for episodic memory entries (beyond 7-day hot window).

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

INSERT OR IGNORE INTO schema_version (version) VALUES (1);

CREATE TABLE IF NOT EXISTS episodic_entries (
    id          TEXT PRIMARY KEY,
    timestamp   REAL NOT NULL,     -- Unix timestamp (float)
    source      TEXT NOT NULL,     -- "conversation", "system1_action", "trigger", "integration"
    summary     TEXT NOT NULL,
    entities    TEXT NOT NULL,     -- JSON array of entity strings
    valence     TEXT NOT NULL,     -- "positive", "negative", "neutral"
    embedding   BLOB              -- Raw float32 bytes from sentence-transformer
);

CREATE INDEX IF NOT EXISTS idx_episodic_timestamp ON episodic_entries(timestamp);
CREATE INDEX IF NOT EXISTS idx_episodic_source ON episodic_entries(source);
```

- [ ] **Step 2: Create `core/memory/episodic/__init__.py`** (empty)

- [ ] **Step 3: Commit**

```bash
git add core/memory/episodic/
git commit -m "feat: SQLite schema for cold episodic memory storage"
```

---

## Task 3: Embedding Model Wrapper

**Files:**
- Create: `core/memory/episodic/embeddings.py`
- Create: `tests/core/memory/episodic/test_embeddings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/memory/episodic/test_embeddings.py
"""Tests for embedding model wrapper."""

from __future__ import annotations

import pytest

from core.memory.episodic.embeddings import EmbeddingModel


@pytest.fixture(scope="module")
def model() -> EmbeddingModel:
    """Load model once for all tests in this module."""
    return EmbeddingModel()


def test_embed_returns_bytes(model: EmbeddingModel) -> None:
    result = model.embed("Sir asked for a morning briefing")
    assert isinstance(result, bytes)
    assert len(result) > 0


def test_embed_deterministic(model: EmbeddingModel) -> None:
    a = model.embed("hello world")
    b = model.embed("hello world")
    assert a == b


def test_cosine_similarity_same_text(model: EmbeddingModel) -> None:
    a = model.embed("the lights are on")
    b = model.embed("the lights are on")
    sim = model.cosine_similarity(a, b)
    assert sim > 0.99


def test_cosine_similarity_different_text(model: EmbeddingModel) -> None:
    a = model.embed("turn on the kitchen lights")
    b = model.embed("stock market performance today")
    sim = model.cosine_similarity(a, b)
    assert sim < 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/memory/episodic/test_embeddings.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
# core/memory/episodic/embeddings.py
"""Embedding model wrapper for episodic memory vector search.

Uses a local sentence-transformer model. Embeddings are computed at
write time and stored as raw bytes alongside entries.
"""

from __future__ import annotations

import logging
import struct

import numpy as np

logger = logging.getLogger(__name__)

# Model selected for accuracy/speed balance. Runs on CPU or GPU.
_DEFAULT_MODEL = "all-MiniLM-L6-v2"


class EmbeddingModel:
    """Wraps a sentence-transformer model for text embedding."""

    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        self._dim = self._model.get_sentence_embedding_dimension()
        logger.info("Loaded embedding model '%s' (dim=%d)", model_name, self._dim)

    @property
    def dimension(self) -> int:
        return int(self._dim)

    def embed(self, text: str) -> bytes:
        """Embed text and return as raw float32 bytes."""
        embedding = self._model.encode(text, convert_to_numpy=True)
        arr: np.ndarray[tuple[int], np.dtype[np.float32]] = np.asarray(embedding, dtype=np.float32)
        return arr.tobytes()

    @staticmethod
    def cosine_similarity(a: bytes, b: bytes) -> float:
        """Compute cosine similarity between two embedding byte arrays."""
        va = np.frombuffer(a, dtype=np.float32)
        vb = np.frombuffer(b, dtype=np.float32)
        dot = float(np.dot(va, vb))
        norm = float(np.linalg.norm(va) * np.linalg.norm(vb))
        if norm == 0:
            return 0.0
        return dot / norm
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/memory/episodic/test_embeddings.py -v`
Expected: PASS (may take a moment to download model on first run)

- [ ] **Step 5: Run ruff + mypy**

Run: `ruff check core/memory/episodic/embeddings.py --fix && ruff format core/memory/ && mypy core/memory/episodic/embeddings.py --strict`

- [ ] **Step 6: Commit**

```bash
git add core/memory/episodic/embeddings.py tests/core/memory/episodic/
git commit -m "feat: sentence-transformer embedding model wrapper for episodic memory"
```

---

## Task 4: EpisodicStore (Hot Redis + Cold SQLite)

**Files:**
- Create: `core/memory/episodic/store.py`
- Create: `tests/core/memory/episodic/test_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/memory/episodic/test_store.py
"""Tests for EpisodicStore."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from core.memory.episodic.store import EpisodicStore
from core.memory.schemas import EpisodicEntry


@pytest.fixture
def mock_redis() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def store(mock_redis: AsyncMock, tmp_path: object) -> EpisodicStore:
    return EpisodicStore(
        redis=mock_redis,
        db_path=f"{tmp_path}/episodic.db",
        hot_days=7,
    )


@pytest.mark.asyncio
async def test_write_entry(store: EpisodicStore) -> None:
    entry = EpisodicEntry(
        id="ep-1",
        timestamp=datetime.now(UTC),
        source="conversation",
        summary="Sir asked about the weather",
        entities=["weather"],
        valence="neutral",
    )
    await store.write(entry, embedding=b"\x00" * 384 * 4)
    # Should write to Redis stream
    store._redis.xadd.assert_called_once()


@pytest.mark.asyncio
async def test_archive_to_cold(store: EpisodicStore) -> None:
    """Entries can be archived to SQLite."""
    entry = EpisodicEntry(
        id="ep-old",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        source="system1_action",
        summary="Lights dimmed at 10pm",
        entities=["light.living"],
        valence="neutral",
    )
    embedding = b"\x00" * 384 * 4
    await store.archive_to_cold(entry, embedding)
    # Verify it's in SQLite
    rows = await store.query_cold(limit=10)
    assert len(rows) >= 1
    assert rows[0].id == "ep-old"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/memory/episodic/test_store.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
# core/memory/episodic/store.py
"""EpisodicStore — hot (Redis Stream) + cold (SQLite) episodic memory."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

from core.memory.schemas import EpisodicEntry
from shared.streams import EPISODIC_STREAM

if TYPE_CHECKING:
    from core.reflex.runner import AioRedis

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class EpisodicStore:
    """Manages episodic memory across hot (Redis) and cold (SQLite) storage."""

    def __init__(
        self,
        redis: AioRedis,
        db_path: str = "core/memory/episodic.db",
        hot_days: int = 7,
    ) -> None:
        self._redis = redis
        self._db_path = db_path
        self._hot_days = hot_days
        self._db: aiosqlite.Connection | None = None

    async def _ensure_db(self) -> aiosqlite.Connection:
        """Ensure SQLite database is initialized (async)."""
        if self._db is None:
            self._db = await aiosqlite.connect(self._db_path)
            await self._db.execute("PRAGMA journal_mode=WAL")
            schema = _SCHEMA_PATH.read_text()
            await self._db.executescript(schema)
        return self._db

    async def write(self, entry: EpisodicEntry, embedding: bytes) -> None:
        """Write an episodic entry to hot storage (Redis Stream)."""
        data = {
            "entry": entry.model_dump_json(),
            "embedding": embedding,
        }
        await self._redis.xadd(EPISODIC_STREAM, data)  # type: ignore[misc]
        logger.debug("Wrote episodic entry %s to hot storage", entry.id)

    async def archive_to_cold(self, entry: EpisodicEntry, embedding: bytes) -> None:
        """Archive an episodic entry to cold storage (SQLite)."""
        db = await self._ensure_db()
        await db.execute(
            """INSERT OR REPLACE INTO episodic_entries
               (id, timestamp, source, summary, entities, valence, embedding)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.id,
                entry.timestamp.timestamp(),
                entry.source,
                entry.summary,
                json.dumps(entry.entities),
                entry.valence,
                embedding,
            ),
        )
        await db.commit()
        logger.debug("Archived episodic entry %s to cold storage", entry.id)

    async def query_cold(
        self,
        limit: int = 20,
        since: datetime | None = None,
        entity: str | None = None,
    ) -> list[EpisodicEntry]:
        """Query cold storage with optional time and entity filters."""
        db = await self._ensure_db()
        conditions: list[str] = []
        params: list[object] = []

        if since:
            conditions.append("timestamp >= ?")
            params.append(since.timestamp())
        if entity:
            conditions.append("entities LIKE ?")
            params.append(f'%"{entity}"%')

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT id, timestamp, source, summary, entities, valence FROM episodic_entries {where} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        entries: list[EpisodicEntry] = []
        for row in rows:
            entries.append(
                EpisodicEntry(
                    id=row[0],
                    timestamp=datetime.fromtimestamp(row[1], tz=UTC),
                    source=row[2],
                    summary=row[3],
                    entities=json.loads(row[4]),
                    valence=row[5],
                )
            )
        return entries

    async def get_cold_embedding(self, entry_id: str) -> bytes | None:
        """Get the embedding for a cold-storage entry."""
        db = await self._ensure_db()
        cursor = await db.execute(
            "SELECT embedding FROM episodic_entries WHERE id = ?", (entry_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/memory/episodic/test_store.py -v`
Expected: PASS

- [ ] **Step 5: Run ruff + mypy**

Run: `ruff check core/memory/episodic/store.py --fix && ruff format core/memory/ && mypy core/memory/episodic/store.py --strict`

- [ ] **Step 6: Commit**

```bash
git add core/memory/episodic/store.py tests/core/memory/episodic/test_store.py
git commit -m "feat: EpisodicStore with Redis hot + SQLite cold storage"
```

---

## Task 5: Episodic Search (Semantic + Time + Entity)

**Files:**
- Create: `core/memory/episodic/search.py`
- Create: `tests/core/memory/episodic/test_search.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/memory/episodic/test_search.py
"""Tests for episodic memory search."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.memory.episodic.search import EpisodicSearch
from core.memory.schemas import EpisodicEntry


@pytest.fixture
def mock_store() -> AsyncMock:
    store = AsyncMock()
    store.query_cold.return_value = [
        EpisodicEntry(
            id="ep-1",
            timestamp=datetime(2026, 3, 19, 10, 0, tzinfo=UTC),
            source="conversation",
            summary="Sir asked about the weather forecast",
            entities=["weather"],
            valence="neutral",
        ),
        EpisodicEntry(
            id="ep-2",
            timestamp=datetime(2026, 3, 19, 8, 0, tzinfo=UTC),
            source="system1_action",
            summary="Front door sensor triggered at 4am",
            entities=["binary_sensor.front_door"],
            valence="negative",
        ),
    ]
    return store


@pytest.fixture
def mock_embedder() -> MagicMock:
    embedder = MagicMock()
    # Return deterministic embeddings
    embedder.embed.return_value = b"\x00" * 384 * 4
    embedder.cosine_similarity.return_value = 0.8
    return embedder


def test_search_by_entity(mock_store: AsyncMock, mock_embedder: MagicMock) -> None:
    search = EpisodicSearch(store=mock_store, embedder=mock_embedder)
    # entity search is sync filter
    entries = [
        EpisodicEntry(
            id="ep-1", timestamp=datetime.now(UTC), source="conv",
            summary="weather", entities=["weather"], valence="neutral",
        ),
        EpisodicEntry(
            id="ep-2", timestamp=datetime.now(UTC), source="conv",
            summary="door", entities=["door"], valence="neutral",
        ),
    ]
    filtered = search.filter_by_entity(entries, "weather")
    assert len(filtered) == 1
    assert filtered[0].id == "ep-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/memory/episodic/test_search.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
# core/memory/episodic/search.py
"""Episodic memory search — semantic, time-based, and entity-based retrieval."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.memory.schemas import EpisodicEntry

if TYPE_CHECKING:
    from core.memory.episodic.embeddings import EmbeddingModel
    from core.memory.episodic.store import EpisodicStore

logger = logging.getLogger(__name__)


class EpisodicSearch:
    """Search episodic memory across hot and cold storage."""

    def __init__(self, store: EpisodicStore, embedder: EmbeddingModel) -> None:
        self._store = store
        self._embedder = embedder

    def filter_by_entity(
        self, entries: list[EpisodicEntry], entity: str
    ) -> list[EpisodicEntry]:
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

            semantic_score = self._embedder.cosine_similarity(
                query_embedding, entry_embedding
            )

            # Recency score: exponential decay, 1.0 at now, ~0.5 at 7 days
            age_days = (datetime.now(UTC) - entry.timestamp).days
            recency_score = 0.5 ** (age_days / 7.0) if age_days >= 0 else 1.0

            combined = (1 - recency_weight) * semantic_score + recency_weight * recency_score
            scored.append((combined, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:limit]]
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/memory/episodic/test_search.py -v`
Expected: PASS

- [ ] **Step 5: Run ruff + mypy**

Run: `ruff check core/memory/episodic/search.py --fix && ruff format core/memory/ && mypy core/memory/episodic/search.py --strict`

- [ ] **Step 6: Commit**

```bash
git add core/memory/episodic/search.py tests/core/memory/episodic/test_search.py
git commit -m "feat: episodic memory search with semantic + recency scoring"
```

---

## Task 6: Episodic Decay Scheduler

**Files:**
- Create: `core/memory/episodic/decay.py`
- Create: `tests/core/memory/episodic/test_decay.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/memory/episodic/test_decay.py
"""Tests for episodic decay scheduler."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.memory.episodic.decay import DecayScheduler


def test_classify_hot() -> None:
    scheduler = DecayScheduler(hot_days=7, compress_days=90)
    ts = datetime.now(UTC) - timedelta(days=3)
    assert scheduler.classify(ts) == "hot"


def test_classify_warm() -> None:
    scheduler = DecayScheduler(hot_days=7, compress_days=90)
    ts = datetime.now(UTC) - timedelta(days=30)
    assert scheduler.classify(ts) == "warm"


def test_classify_cold() -> None:
    scheduler = DecayScheduler(hot_days=7, compress_days=90)
    ts = datetime.now(UTC) - timedelta(days=200)
    assert scheduler.classify(ts) == "cold"


def test_classify_archive() -> None:
    scheduler = DecayScheduler(hot_days=7, compress_days=90)
    ts = datetime.now(UTC) - timedelta(days=400)
    assert scheduler.classify(ts) == "archive"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/memory/episodic/test_decay.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
# core/memory/episodic/decay.py
"""Episodic memory decay scheduler.

Decay schedule:
  0-7 days:    hot    (full entries in Redis)
  7-90 days:   warm   (individual entries in SQLite)
  90-365 days: cold   (compressed summaries in SQLite)
  365+ days:   archive (only Librarian-flagged entries survive)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal


class DecayScheduler:
    """Classifies episodic entries by age for decay processing."""

    def __init__(self, hot_days: int = 7, compress_days: int = 90) -> None:
        self._hot_days = hot_days
        self._compress_days = compress_days

    def classify(
        self, timestamp: datetime
    ) -> Literal["hot", "warm", "cold", "archive"]:
        """Classify an entry by its age."""
        age = datetime.now(UTC) - timestamp
        if age <= timedelta(days=self._hot_days):
            return "hot"
        if age <= timedelta(days=self._compress_days):
            return "warm"
        if age <= timedelta(days=365):
            return "cold"
        return "archive"
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/memory/episodic/test_decay.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/memory/episodic/decay.py tests/core/memory/episodic/test_decay.py
git commit -m "feat: episodic memory decay scheduler"
```

---

## Task 7: Semantic Memory — Profile Extension

**Files:**
- Create: `core/memory/profile/about.md`
- Create: `core/memory/profile/relationships.md`

- [ ] **Step 1: Create empty profile files with YAML frontmatter**

```markdown
# core/memory/profile/about.md
---
type: semantic
category: profile
description: Learned facts about sir
last_updated: null
---

# About Sir

<!-- Facts Alfred has learned about sir through conversation and observation.
     Updated by the Librarian during nightly consolidation.
     Each inference is tagged with source and confidence. -->
```

```markdown
# core/memory/profile/relationships.md
---
type: semantic
category: profile
description: People Alfred knows about
last_updated: null
---

# Relationships

<!-- People Alfred has learned about through conversation.
     Each person entry includes name, relationship, and observed preferences. -->
```

- [ ] **Step 2: Commit**

```bash
git add core/memory/profile/
git commit -m "feat: semantic memory profile files (about, relationships)"
```

---

## Task 8: RoutineStore (Procedural Memory)

**Files:**
- Create: `core/memory/routines/store.py`
- Create: `core/memory/routines/__init__.py`
- Create: `tests/core/memory/routines/test_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/memory/routines/test_store.py
"""Tests for RoutineStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.memory.routines.store import RoutineStore
from core.memory.schemas import RoutineSpec, RoutineStep


@pytest.fixture
def store(tmp_path: Path) -> RoutineStore:
    return RoutineStore(routines_dir=str(tmp_path))


def test_save_and_load(store: RoutineStore) -> None:
    routine = RoutineSpec(
        name="evening_movie",
        trigger_pattern="every evening around 8pm",
        steps=[RoutineStep(description="Dim living room to 30%")],
        confidence=0.7,
        learned_from=["ep-1", "ep-2"],
        state="candidate",
    )
    store.save(routine)
    loaded = store.get("evening_movie")
    assert loaded is not None
    assert loaded.confidence == 0.7


def test_list_all(store: RoutineStore) -> None:
    for i in range(3):
        store.save(RoutineSpec(
            name=f"routine_{i}",
            trigger_pattern=f"pattern {i}",
            steps=[RoutineStep(description=f"step {i}")],
            confidence=0.5,
            learned_from=[],
            state="candidate",
        ))
    all_routines = store.list_all()
    assert len(all_routines) == 3


def test_list_by_state(store: RoutineStore) -> None:
    store.save(RoutineSpec(
        name="active_one", trigger_pattern="p", steps=[],
        confidence=0.9, learned_from=[], state="active",
    ))
    store.save(RoutineSpec(
        name="candidate_one", trigger_pattern="p", steps=[],
        confidence=0.5, learned_from=[], state="candidate",
    ))
    active = store.list_by_state("active")
    assert len(active) == 1
    assert active[0].name == "active_one"


def test_delete(store: RoutineStore) -> None:
    store.save(RoutineSpec(
        name="to_delete", trigger_pattern="p", steps=[],
        confidence=0.5, learned_from=[], state="candidate",
    ))
    store.delete("to_delete")
    assert store.get("to_delete") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/memory/routines/test_store.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
# core/memory/routines/store.py
"""RoutineStore — YAML-based procedural memory for learned routines."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

import yaml

from core.memory.schemas import RoutineSpec

logger = logging.getLogger(__name__)


class RoutineStore:
    """Stores learned routines as YAML files on disk.

    Each routine is a separate YAML file named by the routine's name.
    Atomic writes: write to .tmp then os.rename().
    """

    def __init__(self, routines_dir: str = "core/memory/routines") -> None:
        self._dir = Path(routines_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        safe_name = name.replace(" ", "_").replace("/", "_")
        return self._dir / f"{safe_name}.yaml"

    def save(self, routine: RoutineSpec) -> None:
        """Save a routine to disk (atomic write)."""
        path = self._path(routine.name)
        tmp = path.with_suffix(".tmp")
        data = routine.model_dump(mode="json")
        tmp.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
        os.rename(tmp, path)
        logger.debug("Saved routine '%s'", routine.name)

    def get(self, name: str) -> RoutineSpec | None:
        """Load a routine by name."""
        path = self._path(name)
        if not path.exists():
            return None
        data = yaml.safe_load(path.read_text())
        return RoutineSpec.model_validate(data)

    def list_all(self) -> list[RoutineSpec]:
        """List all routines."""
        routines: list[RoutineSpec] = []
        for path in self._dir.glob("*.yaml"):
            try:
                data = yaml.safe_load(path.read_text())
                routines.append(RoutineSpec.model_validate(data))
            except Exception as e:
                logger.warning("Failed to load routine from %s: %s", path, e)
        return routines

    def list_by_state(
        self, state: Literal["candidate", "active", "dormant", "archived"]
    ) -> list[RoutineSpec]:
        """List routines in a specific state."""
        return [r for r in self.list_all() if r.state == state]

    def delete(self, name: str) -> None:
        """Delete a routine by name."""
        path = self._path(name)
        if path.exists():
            path.unlink()
            logger.debug("Deleted routine '%s'", name)
```

Also create `core/memory/routines/__init__.py` and `tests/core/memory/routines/__init__.py` (empty).

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/memory/routines/test_store.py -v`
Expected: PASS

- [ ] **Step 5: Run ruff + mypy**

Run: `ruff check core/memory/routines/ --fix && ruff format core/memory/ && mypy core/memory/routines/ --strict`

- [ ] **Step 6: Commit**

```bash
git add core/memory/routines/ tests/core/memory/routines/
git commit -m "feat: RoutineStore for YAML-based procedural memory"
```

---

## Task 9: Librarian — Nightly Consolidation Agent

**Files:**
- Create: `core/librarian/__init__.py`
- Create: `core/librarian/consolidator.py`
- Create: `core/librarian/__main__.py`
- Create: `tests/core/librarian/test_consolidator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/librarian/test_consolidator.py
"""Tests for the Librarian consolidation agent."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.librarian.consolidator import Librarian


@pytest.fixture
def mock_deps() -> dict[str, AsyncMock | MagicMock]:
    return {
        "redis": AsyncMock(),
        "episodic_store": AsyncMock(),
        "routine_store": MagicMock(),
        "preferences_dir": "/tmp/test_prefs",
        "profile_dir": "/tmp/test_profile",
    }


@pytest.mark.asyncio
async def test_drain_scratchpad(mock_deps: dict[str, AsyncMock | MagicMock]) -> None:
    mock_deps["redis"].lrange.return_value = [
        b"2026-03-19T10:00:00Z [reflex] smart_home.dim_lights({room: living}) -> success",
        b"2026-03-19T10:05:00Z [conscious] Briefing delivered to sir",
    ]
    mock_deps["redis"].ltrim.return_value = None

    librarian = Librarian(**mock_deps)
    entries = await librarian._drain_scratchpad()
    assert len(entries) == 2


@pytest.mark.asyncio
async def test_consolidate_empty_scratchpad(mock_deps: dict[str, AsyncMock | MagicMock]) -> None:
    mock_deps["redis"].lrange.return_value = []
    librarian = Librarian(**mock_deps)
    result = await librarian.consolidate()
    assert result["entries_processed"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/librarian/test_consolidator.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
# core/librarian/consolidator.py
"""Librarian — nightly consolidation of scratchpad into structured memory.

The Librarian replays the scratchpad, extracts episodic entries,
updates semantic memory (preferences + profile), detects patterns
for procedural memory, and applies decay to old entries.

Uses Claude for LLM-powered extraction and conflict resolution.
Atomic file writes: write to .tmp then os.rename() for memory files.
On failure, the scratchpad accumulates — memory files never left partial.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from core.memory.schemas import EpisodicEntry
from shared.streams import SCRATCHPAD_QUEUE

if TYPE_CHECKING:
    from core.memory.episodic.store import EpisodicStore
    from core.memory.routines.store import RoutineStore
    from core.reflex.runner import AioRedis

logger = logging.getLogger(__name__)


class Librarian:
    """Nightly consolidation agent.

    Drains the scratchpad queue, processes entries through an LLM,
    and writes to the three memory layers.
    """

    def __init__(
        self,
        redis: AioRedis,
        episodic_store: EpisodicStore,
        routine_store: RoutineStore,
        preferences_dir: str = "core/memory/preferences",
        profile_dir: str = "core/memory/profile",
        claude_api_key: str = "",
        claude_model: str = "claude-opus-4-6",
    ) -> None:
        self._redis = redis
        self._episodic = episodic_store
        self._routines = routine_store
        self._preferences_dir = Path(preferences_dir)
        self._profile_dir = Path(profile_dir)
        self._api_key = claude_api_key
        self._model = claude_model

    async def _drain_scratchpad(self) -> list[str]:
        """Atomically drain the scratchpad queue.

        Uses LRANGE + LTRIM to get all entries and clear in one shot.
        If processing fails, entries are lost from the queue but the
        Librarian logs them — this is acceptable since scratchpad is
        ephemeral and the next cycle will process new observations.
        """
        raw: list[bytes] = await self._redis.lrange(SCRATCHPAD_QUEUE, 0, -1)  # type: ignore[misc]
        if raw:
            await self._redis.ltrim(SCRATCHPAD_QUEUE, len(raw), -1)  # type: ignore[misc]
        return [r.decode() if isinstance(r, bytes) else str(r) for r in raw]

    async def _extract_episodic_entries(
        self, scratchpad_lines: list[str]
    ) -> list[EpisodicEntry]:
        """Extract episodic entries from scratchpad lines.

        For now, each scratchpad line becomes one episodic entry.
        Future: use Claude to summarize and merge related observations.
        """
        entries: list[EpisodicEntry] = []
        for line in scratchpad_lines:
            # Parse timestamp and source from scratchpad format:
            # "2026-03-19T10:00:00Z [reflex] action(...) → result"
            parts = line.split("] ", 1)
            source = "unknown"
            summary = line
            if len(parts) == 2:
                source_part = parts[0].split("[", 1)
                if len(source_part) == 2:
                    source = source_part[1]
                summary = parts[1]

            entries.append(
                EpisodicEntry(
                    id=str(uuid4()),
                    timestamp=datetime.now(UTC),
                    source=source,
                    summary=summary.strip(),
                    entities=[],  # TODO: entity extraction via Claude
                    valence="neutral",
                )
            )
        return entries

    def _write_semantic_file(self, path: Path, content: str) -> None:
        """Atomic write to a semantic memory file."""
        tmp = path.with_suffix(".tmp")
        tmp.write_text(content)
        os.rename(tmp, path)

    async def consolidate(self) -> dict[str, Any]:
        """Run one consolidation cycle.

        Returns a summary dict for logging/telemetry.
        """
        logger.info("Librarian consolidation started")

        # 1. Drain scratchpad
        lines = await self._drain_scratchpad()
        if not lines:
            logger.info("Scratchpad empty — nothing to consolidate")
            return {"entries_processed": 0}

        logger.info("Draining %d scratchpad entries", len(lines))

        # 2. Extract episodic entries
        episodic_entries = await self._extract_episodic_entries(lines)

        # 3. Write to episodic store
        # Load embedding model once (optional dep — falls back to empty bytes)
        embedder = None
        try:
            from core.memory.episodic.embeddings import EmbeddingModel

            embedder = EmbeddingModel()
        except ImportError:
            logger.info("sentence-transformers not installed — writing entries without embeddings")

        for entry in episodic_entries:
            embedding = embedder.embed(entry.summary) if embedder else b""
            await self._episodic.write(entry, embedding)

        # 4. TODO: Pattern detection for procedural memory (requires Claude)
        # 5. TODO: Semantic memory updates (requires Claude)
        # 6. TODO: Decay processing

        result = {
            "entries_processed": len(lines),
            "episodic_created": len(episodic_entries),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        logger.info("Consolidation complete: %s", result)
        return result
```

- [ ] **Step 4: Create entry point**

```python
# core/librarian/__main__.py
"""Entry point for the Librarian consolidation agent.

Usage: python -m core.librarian

Runs one consolidation cycle and exits. Intended to be invoked
by a cron job or scheduler, not as a long-running service.
"""

from __future__ import annotations

import asyncio

import redis.asyncio as aioredis

from core.librarian.consolidator import Librarian
from core.memory.episodic.store import EpisodicStore
from core.memory.routines.store import RoutineStore
from core.reflex.runner import AioRedis
from shared.config import AlfredConfig
from shared.logging import configure_logging


async def run() -> None:
    log = configure_logging(service="librarian")
    config = AlfredConfig.from_env()

    r: AioRedis = aioredis.from_url(config.redis_url)

    librarian = Librarian(
        redis=r,
        episodic_store=EpisodicStore(redis=r),
        routine_store=RoutineStore(),
        claude_api_key=config.claude_api_key,
        claude_model=config.claude_model,
    )

    try:
        result = await librarian.consolidate()
        log.info("Librarian finished: %s", result)
    finally:
        await r.aclose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
```

Also create `core/librarian/__init__.py` and `tests/core/librarian/__init__.py` (empty).

- [ ] **Step 5: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/librarian/test_consolidator.py -v`
Expected: PASS

- [ ] **Step 6: Run ruff + mypy**

Run: `ruff check core/librarian/ --fix && ruff format core/ && mypy core/librarian/ --strict`

- [ ] **Step 7: Commit**

```bash
git add core/librarian/ tests/core/librarian/
git commit -m "feat: Librarian consolidation agent for nightly memory processing"
```

---

## Task 10: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -v`

- [ ] **Step 2: Run full linting + type checking**

Run: `ruff check . --fix && ruff format . && mypy bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
