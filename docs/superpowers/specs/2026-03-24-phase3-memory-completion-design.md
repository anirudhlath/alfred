# Phase 3 Memory Completion — Design Specification

**Date:** 2026-03-24
**Status:** Draft
**Author:** Anirudh Lath + Claude (Lead Engineer / Background Scientist)
**Scope:** Completes Phase 3 Step 3 (Memory Expansion) and Step 6 (Evals), filling all gaps between the expanded vision spec and current implementation.

---

## 1. Problem Statement

Phase 3 of the expanded vision spec defines a biologically-inspired, three-layer memory system as the foundation for Alfred's intelligence. While the scaffolding exists (EpisodicStore, RoutineStore, MemoryReader, Librarian scheduler), the core intelligence capabilities are unimplemented:

- **Episodic memory is unsearchable** — hot storage (Redis) has no search capability, cold storage (SQLite) uses time-based queries only. `EpisodicSearch` exists but is never wired into the engine.
- **The Conscious Engine pre-assembles a static prompt** — every request gets the same context structure regardless of relevance.
- **The Librarian can't learn** — pattern detection, decay processing, and semantic conflict resolution are stubs.
- **Memory has no significance model** — all entries are treated equally. No mechanism to prioritize important memories or forget irrelevant ones.
- **Evals can't validate memory quality** — ProactivityRelevanceScore is a stub, MemoryRetrievalPrecision uses keyword overlap, e2e demo is missing.

This spec completes Phase 3 by making the memory system actually intelligent: searchable, significance-aware, self-organizing, and validated.

---

## 2. Design Principles

### Biologically Inspired (Neuroscience Model)

The expanded vision spec claims "biologically-inspired" memory. This spec delivers on that claim by modeling three cognitive processes:

| Cognitive Process | Brain Structure | Alfred Equivalent |
|-------------------|----------------|-------------------|
| **Significance tagging** | Amygdala — evaluates threat, reward, novelty, personal relevance | SignificanceScore — multi-dimensional scoring at write time + LLM refinement |
| **Involuntary recall** | Hippocampal pattern completion — hearing a word activates associated memories | Pre-LLM vector search — query embedding surfaces relevant context automatically |
| **Deliberate recall** | Prefrontal cortex — effortful search through memory | Memory tools in agentic loop — Claude explicitly fetches deeper context |
| **Consolidation** | Sleep replay — hippocampus replays to neocortex | Librarian — significance-based migration, pattern detection, conflict resolution |
| **Forgetting** | Active pruning during sleep — irrelevant memories weakened | Contextual decay — significance + retrieval frequency determine retention |

### Research-Backed Architecture

The context management design draws from recent research:

- **Letta/MemGPT** (Berkeley, 2023-2026) — LLM-as-Operating-System paradigm. Agent manages its own memory through function calls. Three-tier memory: core (in-context), recall (searchable history), archival (long-term). Alfred adopts this with base context (always present) + memory tools (on-demand).
- **A-MEM** (NeurIPS 2025) — Agentic memory with self-organizing knowledge networks. Agent decides memory organization, not hand-coded rules. Dynamic linking between memories. Alfred's unified vector index enables cross-layer discovery.
- **JetBrains "The Complexity Trap"** (NeurIPS 2025) — Simple observation masking is as efficient as LLM summarization for context management. ~50% cost reduction without quality loss. Alfred's involuntary recall is a simple, cheap retrieval step, not expensive summarization.
- **MemoBrain** (Beijing AI, Jan 2026) — Executive memory co-pilot that prunes invalid steps and folds completed trajectories. Operates alongside reasoning, not before it. Alfred's deliberate recall tools operate within the agentic loop.
- **Windsurf** — Multi-layer context assembly with M-Query retrieval improving on basic cosine similarity. Dual-embedding approach (semantic key + content) improves retrieval precision.

### Abstraction for Swappability

Every technology choice is behind an interface. The embedding model, vector store, and memory stores are independently swappable without touching consumers.

---

## 3. Prerequisites

### 3.1 Redis Stack

**Change:** Replace Homebrew `redis` with `redis-stack` (or `redis/redis/redis-stack` tap).

**Why:** Redis 8 integrates RediSearch (now "Redis Query Engine") as a core module, providing HNSW vector indexing with hybrid text + metadata + vector queries via `FT.SEARCH`. The current Homebrew `redis` formula compiles without the search module. `redis-stack` includes it pre-built.

**Impact:**
- `scripts/dev-up.sh` updated to use redis-stack
- Production Docker Compose uses `redis/redis-stack` image
- All existing Redis usage (streams, hashes, lists) unchanged
- Gains: `FT.CREATE`, `FT.SEARCH`, `FT.DROPINDEX` commands

### 3.2 Embedding Model: EmbeddingGemma-300M

**Change:** Replace `all-MiniLM-L6-v2` (384 dim) with `google/embeddinggemma-300m` (768 dim).

**Why:** EmbeddingGemma-300M is the current best-in-class for local inference (2026 MTEB benchmarks). 300M parameters, outperforms models 2x its size. Optimized for on-device deployment (<200MB RAM, <22ms on EdgeTPU). Supports Matryoshka Representation Learning — embeddings can be truncated to lower dimensions (384, 256, 128) with minimal quality loss. Uses the same `sentence-transformers` framework as the current model — drop-in replacement.

**Impact:**
- `SentenceTransformer("google/embeddinggemma-300m")` replaces `SentenceTransformer("all-MiniLM-L6-v2")`
- Embedding dimension changes from 384 to 768
- Existing embeddings in Redis and SQLite become invalid — full re-embed required (minimal data currently)
- Does not support float16 — must use float32
- Auto-downloads from HuggingFace on first use (matches Piper TTS pattern)
- Requires `transformers>=4.48` for Gemma 3 architecture support
- Memory footprint: ~600MB for model weights. Fits comfortably alongside Ollama SLM on both dev (128GB) and prod (64GB) machines
- Two 768-dim float32 embeddings per entry = ~6KB. For 10K hot entries, ~60MB Redis memory for embeddings

### 3.3 Abstraction Layer

```python
class EmbeddingProvider(ABC):
    """Wraps embedding model loading and inference."""

    @abstractmethod
    async def embed(self, text: str) -> list[float]: ...

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def dimension(self) -> int: ...

    @abstractmethod
    def model_name(self) -> str: ...


class ContextMetadata(BaseModel):
    """Typed metadata for context index entries."""
    type: str               # episodic, semantic, routine, integration
    source: str             # conversation, system1_action, trigger, preference_file, etc.
    entities: str           # comma-separated entity names
    timestamp: float        # unix timestamp
    significance: float     # 0.0-1.0 overall
    retrieval_count: int    # incremented on each retrieval
    last_retrieved: float   # unix timestamp of last retrieval, 0.0 if never


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
    async def add(self, id: str, content: str, semantic_key: str,
                  embedding_content: list[float], embedding_semantic: list[float],
                  metadata: ContextMetadata) -> None: ...

    @abstractmethod
    async def search(self, query_embedding: list[float], limit: int,
                     filters: dict[str, str | float | int] | None = None,
                     min_similarity: float = 0.0) -> list[SearchResult]: ...

    @abstractmethod
    async def delete(self, id: str) -> None: ...

    @abstractmethod
    async def exists(self, id: str) -> bool: ...

    @abstractmethod
    async def count(self) -> int: ...
```

**Implementation notes:**
- `SentenceTransformerProvider` — default `EmbeddingProvider` using EmbeddingGemma-300M. Since `SentenceTransformer.encode()` is CPU-bound and synchronous, async methods use `asyncio.to_thread()` to avoid blocking the event loop.
- `RedisVectorStore` — hot storage via RediSearch `FT.SEARCH`
- `SqliteVecStore` — cold storage via `sqlite-vec` virtual tables

**Graceful degradation:** If the RediSearch index is unavailable (e.g., `FT.CREATE` fails at startup), the `RedisVectorStore` logs a warning and falls back to returning empty results. The system continues to function — involuntary recall returns nothing, but deliberate recall and all other capabilities work. The Librarian retries index creation on next cycle.

---

## 4. Unified Context Index

All searchable content goes into a single RediSearch index for involuntary recall. This enables cross-layer discovery — a query can surface episodic entries, semantic preferences, and routine descriptions in one search.

### 4.1 Dual-Embedding Strategy

Each entry has two embedded fields:

- **`content`** — the raw memory with full details. What Claude reads in context.
- **`semantic_key`** — a generalized, retrieval-optimized rewrite. What similarity search matches against.

**Why dual embeddings:** Raw content often embeds poorly for retrieval. "Front door sensor triggered at 04:12" doesn't embed near "anything unusual happen overnight?" But the semantic key "Unexpected home entry point activation during overnight hours — potential security event" does. The semantic key captures the meaning and significance; the content preserves the details.

RediSearch `FT.SEARCH` KNN queries can only target one vector field per call. To search both fields, two `FT.SEARCH` calls are made in parallel (using `asyncio.gather`), then results are merged client-side by taking the higher similarity score per entry. Expected latency: ~15-25ms total (two parallel queries). The `RedisVectorStore.search()` method encapsulates this — callers see a single search interface.

**Semantic key generation:**
- Episodic entries: generated by the Librarian during consolidation (folded into existing LLM call). At write time, a heuristic template is used as placeholder: `"{source} event involving {entities} — {significance_summary}"`.
- Semantic memory sections: generated when the Librarian updates them.
- Routines: `trigger_pattern` + step descriptions, generated at save time.

**Semantic memory file indexing:** Preference and profile Markdown files (`core/memory/preferences/`, `core/memory/profile/`) are indexed into `idx:context` by the Librarian. Each file is split into logical sections (by `##` headers) and each section becomes a separate index entry with `type=semantic`. The Librarian re-indexes all semantic files every consolidation cycle — this handles both Librarian updates and manual human edits to preference files (Pillar 4 allows humans to edit these). Re-indexing is cheap (a few files, a few sections each, embed + HSET). Initial indexing happens on first Librarian run after this feature is deployed.

### 4.2 Hot Storage Migration: Redis Stream to Redis Hash

The existing `EpisodicStore.write()` writes to a Redis Stream (`XADD` to `EPISODIC_STREAM`). RediSearch indexes operate on Redis Hashes, not Streams. This spec replaces the Stream-based hot storage with Hash-based storage indexed by RediSearch.

**Migration path:**
- `EpisodicStore` is superseded by `EpisodicMemory`, which writes to `RedisVectorStore` (Hashes with `ctx:` prefix)
- The `EPISODIC_STREAM` is no longer used for hot storage. It is retained temporarily for backward compatibility — the Librarian's `_extract_episodic_entries()` currently reads from the scratchpad (Redis List), not the stream, so no Librarian changes are needed
- The `EpisodicStore` class is deprecated and will be removed once all consumers are migrated to `EpisodicMemory`
- The existing `maxlen=10000` cap on the stream is replaced by the significance-based decay system managing hash entry count

### 4.3 RediSearch Index Schema

```
FT.CREATE idx:context ON HASH PREFIX 1 ctx: SCHEMA
  type TAG                       # episodic, semantic, routine, integration
  content TEXT                   # raw memory content
  semantic_key TEXT              # retrieval-optimized rewrite
  source TAG                    # conversation, system1_action, trigger, preference_file, etc.
  entities TAG                  # comma-separated entity names
  timestamp NUMERIC SORTABLE    # unix timestamp
  significance NUMERIC SORTABLE # 0.0-1.0 overall significance
  retrieval_count NUMERIC       # incremented on each retrieval
  last_retrieved NUMERIC SORTABLE  # unix timestamp, 0.0 if never
  compressed TAG                  # "yes" if compressed into summary, empty otherwise
  embedding_semantic VECTOR HNSW 6 TYPE FLOAT32 DIM 768 DISTANCE_METRIC COSINE
  embedding_content VECTOR HNSW 6 TYPE FLOAT32 DIM 768 DISTANCE_METRIC COSINE
```

### 4.4 Cold Storage (sqlite-vec)

Replace the current full-table-scan cosine similarity with proper KNN via `sqlite-vec`:

```sql
-- Updated table: INTEGER PRIMARY KEY for stable rowid (required for vec0 join)
CREATE TABLE episodic_entries(
  rowid INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT UNIQUE NOT NULL,           -- UUID string identifier
  timestamp REAL,
  source TEXT,
  summary TEXT,
  entities JSON,
  significance JSON,                 -- SignificanceScore as JSON
  semantic_key TEXT,
  compressed_into TEXT               -- if compressed, points to summary entry ID
);

-- Vector virtual tables: rowid matches episodic_entries.rowid
CREATE VIRTUAL TABLE vec_episodic_semantic USING vec0(embedding float[768]);
CREATE VIRTUAL TABLE vec_episodic_content USING vec0(embedding float[768]);

-- Insert: explicitly set vec0 rowid to match episodic_entries rowid
INSERT INTO vec_episodic_semantic(rowid, embedding) VALUES (?, ?);
INSERT INTO vec_episodic_content(rowid, embedding) VALUES (?, ?);

-- KNN query (searches semantic embeddings, joins for metadata)
SELECT e.id, e.summary, e.significance, e.semantic_key, v.distance
FROM vec_episodic_semantic v
JOIN episodic_entries e ON e.rowid = v.rowid
WHERE v.embedding MATCH ?
ORDER BY v.distance
LIMIT ?;
```

The `SqliteVecStore` coordinates rowid assignment: insert into `episodic_entries` first (gets auto-incremented rowid), then insert into both vec0 tables with the same rowid. Both `semantic_key` and `content` embeddings stored in separate vec0 tables. Search queries both in parallel and merges results client-side (same pattern as `RedisVectorStore`).

**Schema migration:** Current schema is version 1 (tracked via `schema_version` table). This spec introduces version 2. A migration script (`core/memory/episodic/migrations/v2.sql`) handles: adding `significance`, `semantic_key`, `compressed_into` columns; changing primary key to integer rowid with text id as unique; creating vec0 virtual tables; re-embedding existing entries (if any) with the new model.

**sqlite-vec extension loading:** The `sqlite-vec` package is installed via `uv pip install sqlite-vec`. At `SqliteVecStore` initialization:
```python
import sqlite_vec
db = await aiosqlite.connect(path)
await db.execute("SELECT load_extension(?)", (sqlite_vec.loadable_path(),))
```
If the extension fails to load, `SqliteVecStore` falls back to the existing full-table-scan cosine similarity in Python (current behavior) with a warning log.

**Transactional writes:** All writes (metadata + both vec0 tables) happen within a single SQLite transaction. If any insert fails, the transaction rolls back, preventing orphaned rows:
```python
async with db.execute("BEGIN"):
    cursor = await db.execute("INSERT INTO episodic_entries ...", ...)
    rowid = cursor.lastrowid
    await db.execute("INSERT INTO vec_episodic_semantic(rowid, embedding) VALUES (?, ?)", ...)
    await db.execute("INSERT INTO vec_episodic_content(rowid, embedding) VALUES (?, ?)", ...)
    await db.commit()
```

---

## 5. Significance Model (The Amygdala)

### 5.1 SignificanceScore Schema

Replaces `valence: Literal["positive", "negative", "neutral"]` on `EpisodicEntry`.

```python
class SignificanceScore(BaseModel):
    """Multi-dimensional significance score inspired by amygdala function."""
    overall: float              # 0.0-1.0, weighted combination of dimensions
    safety: float = 0.0         # threat/security relevance
    novelty: float = 0.0        # how unusual vs routine
    personal: float = 0.0       # direct user involvement
    emotional: float = 0.0      # inferred emotional weight
    source: Literal["heuristic", "librarian"] = "heuristic"
```

### 5.2 Phase 1 — Heuristic Scoring (Write Time)

Computed instantly when an episodic entry is created. Zero LLM cost.

| Dimension | Heuristic |
|-----------|-----------|
| **safety** | Source is `trigger_fired` with urgency `URGENT` -> 1.0. Security-related entities (door, lock, alarm, leak, smoke, water) -> 0.7. Otherwise 0.0. |
| **novelty** | Compare individual entities against a rolling frequency table in Redis (`ZINCRBY` on sorted set `alfred:entity:freq`). First-time entities -> 1.0. Entities seen < 5 times -> 0.7. Daily routine entities (> 50 occurrences) -> 0.1. Score is the average novelty of all entities in the entry. |
| **personal** | Source is `conversation` (direct user interaction) -> 0.8. Source is `trigger_fired` -> 0.5. Source is `system1_action` -> 0.3. Source is `state_changed` -> 0.1. |
| **emotional** | Urgency `URGENT` -> 0.9. Urgency `IMPORTANT` -> 0.5. Cost alert -> 0.7. Otherwise 0.0. |
| **overall** | Weighted: `0.35*safety + 0.25*novelty + 0.25*personal + 0.15*emotional` |

Weights configurable in `AlfredConfig` via env vars (`SIGNIFICANCE_WEIGHT_SAFETY`, etc.).

### 5.3 Phase 2 — LLM-Refined Scoring (Librarian Consolidation)

The Librarian already makes a Claude call to extract entities and update semantic memory. The prompt is expanded to include significance scoring:

> "For each entry, rate significance 0.0-1.0 on four dimensions: safety (threat/security relevance to sir's household), novelty (how unusual is this relative to daily patterns), personal (how directly does this involve sir), emotional (inferred emotional weight of this event). Consider the full context of sir's known patterns and preferences."

LLM-refined scores overwrite heuristic scores. `source` field changes from `"heuristic"` to `"librarian"`. This is batched — no extra LLM call, just an expanded prompt on the existing consolidation call.

### 5.4 Retrieval Tracking

Every time involuntary or deliberate recall returns an entry, its `retrieval_count` is incremented (`HINCRBY` on the Redis hash). The Librarian reads retrieval counts during consolidation to inform decay decisions — frequently retrieved entries are significant regardless of their initial score.

---

## 6. Episodic Search — Hot + Cold

### 6.1 EpisodicMemory Class

New unified interface replacing direct `EpisodicStore.query_cold()` usage in the engine.

```python
class EpisodicMemory:
    """Unified episodic memory access across hot and cold stores."""

    def __init__(
        self,
        hot: RedisVectorStore,
        cold: SqliteVecStore,
        embedder: EmbeddingProvider,
    ) -> None: ...

    async def write(self, entry: EpisodicEntry, significance: SignificanceScore) -> None:
        """Write entry to hot store with embeddings and significance."""
        ...

    async def recall(self, query: str, limit: int = 10,
                     since: datetime | None = None,
                     types: list[str] | None = None) -> list[EpisodicResult]:
        """Semantic search across hot + cold, deduplicated and ranked."""
        ...

    async def increment_retrieval(self, entry_ids: list[str]) -> None:
        """Increment retrieval_count for returned entries."""
        ...

    async def migrate_to_cold(self, entry_id: str) -> None:
        """Move entry from hot to cold storage."""
        ...
```

### 6.2 Recall Flow

1. Embed query via `EmbeddingProvider`
2. Search hot store (RediSearch) — most recent, highest significance entries
3. Search cold store (sqlite-vec) — older but potentially relevant entries
4. Deduplicate by entry ID (same entry might be in both during migration)
5. Rank by combined score: `0.5 * vector_similarity + 0.3 * significance.overall + 0.2 * recency_factor`
6. Increment `retrieval_count` on all returned entries
7. Return top-K `EpisodicResult` objects

---

## 7. Two-Stage Context Assembly

Replaces the current static prompt assembly with a biologically-inspired two-stage approach: involuntary recall (automatic, pre-LLM) + deliberate recall (agentic, on-demand).

### 7.1 Base Context (Always Present)

Loaded for every request, regardless of query:

- Personality + identity (who Alfred is, who the user is)
- Tools manifest (smart home actions, integration tools)
- Memory tools manifest (`recall_memories`, `get_live_state`)
- Recent conversation history (last N turns of current session)
- Current time

This is Letta's "Core Memory" — the RAM that's always loaded.

**ContextAssembler refactoring:** The existing `ContextAssembler.assemble()` method is refactored to support the two-stage model. Instead of receiving pre-rendered `episodic_text`, `preferences_text`, etc. as string parameters, it receives: (1) base context components (personality, identity, tools, history, time) which are always assembled, and (2) an optional `relevant_context: list[SearchResult]` from involuntary recall, formatted as a `## Relevant Context` block grouped by type. The old string-parameter interface is removed.

### 7.2 Stage 1 — Involuntary Recall (Pre-LLM)

Before every Claude call:

1. Embed the user's query via `EmbeddingProvider`
2. Search the unified context index (`idx:context`) — queries both `embedding_semantic` and `embedding_content` fields
3. Return top-K results (configurable, default 10) above a similarity threshold
4. Inject into the prompt as a `## Relevant Context` block, grouped by source type (episodic, preferences, routines)

**Performance:** One embedding (~22ms) + two parallel RediSearch queries (~15-25ms for both). Total <50ms. No LLM cost.

If the query has weak matches (vague greeting, novel request), this block is minimal or empty. Claude still has base context and memory tools to work with.

### 7.3 Stage 2 — Deliberate Recall (During Agentic Loop)

Claude has two memory tools available as function calls:

**`recall_memories(query: str, types?: list[str], since_days_ago?: int, limit?: int) -> list[MemoryResult]`**

Searches the unified context index with optional filters. Returns memory content with metadata. Used when Claude needs deeper or more specific recall than involuntary provided.

- `query` — natural language search query
- `types` — optional filter: `["episodic", "semantic", "routine"]`
- `since_days_ago` — optional integer time filter: `7` for last week, `30` for last month. The tool handler converts this to `datetime` via `now - timedelta(days=N)` before passing to `EpisodicMemory.recall(since=...)`. No natural language date parsing needed.
- `limit` — max results (default 10)

**`get_live_state(entities?: list[str]) -> dict`**

Fetches current Home Assistant state. Optionally filtered to specific entities. Not a memory lookup — a real-time query to HA via ContextReader.

- `entities` — optional: `["light.living_room", "climate.thermostat"]`. If omitted, returns summary of all active entities.

**Implementation note:** The existing `ContextReader.get_rendered_context()` returns a pre-rendered string. This tool requires a new method `ContextReader.get_entity_states(entities: list[str] | None) -> dict[str, Any]` that returns structured JSON data filterable by entity pattern. The rendered string method remains for backward compatibility.

### 7.4 Memory Tool Registration and Dispatch

Memory tools must be discoverable, not hardcoded (Pillar 2). They are implemented as a `MemoryFeature` class extending `BaseFeature` from the SDK — the same pattern used by integration tools:

```python
class MemoryFeature(BaseFeature):
    """Memory tools for deliberate recall during agentic reasoning."""

    @tool(name="recall_memories", description="Search Alfred's memory for relevant information")
    async def recall_memories(self, query: str, ...) -> list[MemoryResult]: ...

    @tool(name="get_live_state", description="Get current Home Assistant device state")
    async def get_live_state(self, entities: list[str] | None = None) -> dict: ...
```

`MemoryFeature` is registered via `AlfredClient.discover_features()` at startup, just like integration features. The tool dispatch in `_dispatch_tool_call()` routes through the existing `ToolRegistry` — no new dispatch path needed. Memory tools appear in the tools manifest alongside integration and domain tools.

### 7.5 HA State: Base Context vs. Tool

The current engine injects full HA state into every prompt via `ContextReader.get_rendered_context()`. With the new design, HA state is **removed from base context** and available only through the `get_live_state` tool. This saves prompt tokens on requests that don't need device state (conversations, recall queries, briefings). Claude calls the tool when it needs to know what's on/off — typically for commands and when involuntary recall surfaces a device-related memory.

### 7.6 Why Only Two Tools

Claude shouldn't need to know Alfred's internal memory architecture. `recall_memories` searches across all memory layers — episodic, semantic, procedural — via the unified index. The `types` filter is optional for when Claude wants to narrow down. `get_live_state` is separate because it's real-time system state, not stored memory.

Memory writing is not exposed as a tool. The Conscious Engine writes observations to the scratchpad after each interaction (existing behavior), which flows through the Librarian for consolidation. This preserves Pillar 4 — scratchpad only at runtime.

### 7.5 Example Flows

**"Turn off the lights"**
- Involuntary: may surface a lighting preference. Low cost.
- Claude calls `get_live_state(["light.*"])` to see what's on, then calls smart home tool. No deliberate memory recall needed.
- Total memory overhead: ~50ms for involuntary search.

**"Good morning"**
- Involuntary: surfaces recent notable events, morning routine, briefing preferences.
- Claude calls integration tools (calendar, weather, health) in parallel — same as today.
- May not need deliberate recall — involuntary gave enough.

**"What happened with the front door last week?"**
- Involuntary: surfaces a few front door episodic entries (top-K).
- Claude calls `recall_memories("front door events", types=["episodic"], since_days_ago=7, limit=20)` for comprehensive answer.

**"Do that thing we talked about yesterday"**
- Involuntary: weak signal, minimal results.
- Claude reads conversation history (in base context), identifies referent, then calls specific tools if needed.

### 7.6 Telemetry

Every request logs:
- Involuntary recall: embedding time, search time, results count, similarity scores, entries injected
- Deliberate recall: which memory tools Claude called, their latency, result sizes
- Total prompt tokens at each stage (estimated)
- Feeds research pipeline for analysis of retrieval quality and context utilization

---

## 8. Librarian Upgrade — Contextual Consolidation

### 8.1 Revised Pipeline

| Step | Status | Description |
|------|--------|-------------|
| 1. Drain scratchpad | Existing | Atomic RENAME, crash-safe. Unchanged. |
| 2. Extract episodic entries | Enhanced | Add heuristic significance scoring. Generate template semantic keys. |
| 3. Write to hot store | Enhanced | Write to RedisVectorStore with dual embeddings, significance, metadata. Index in `idx:context`. |
| 4. Update semantic memory | Enhanced | **Conflict-aware merge** (see 8.2). Generate semantic keys for updated sections. |
| 5. Refine significance + semantic keys | New | LLM scores all recent entries, rewrites semantic keys. Batched with step 4. |
| 6. Contextual decay | New | **Significance-based migration** (see 8.3). |
| 7. Pattern detection | New | **Routine candidate creation** (see Section 9). |

### 8.2 Semantic Conflict Resolution

Currently the Librarian appends to `learned.md` with no awareness of existing content.

**Upgraded flow:**

1. Librarian reads current `learned.md` and relevant preference files
2. Passes existing content + new observations to Claude (same consolidation call)
3. Prompt instructs Claude to:
   - **Confirm** — new observation matches existing knowledge. Skip.
   - **Contradict** — new observation conflicts with existing fact. Check episodic evidence.
   - **New** — entirely new information. Add.
4. For contradictions: if N >= 5 consistent observations over M >= 14 days supporting the new pattern (matching expanded vision spec Section 4, line 214), update with provenance: `[updated YYYY-MM-DD: was "X", now "Y", source: N observations over M days]`. Otherwise mark tentative: `[tentative: observed N times over M days, watching]`. Thresholds configurable via `CONFLICT_MIN_OBSERVATIONS` and `CONFLICT_MIN_DAYS` in `AlfredConfig`.
5. Old contradicted values archived with `[archived]` tag — Alfred can recall "you used to..."

**LLM call structure:** The Librarian consolidation uses two LLM calls per cycle (up from the current one):

1. **Analysis call:** Entity extraction + significance scoring + semantic key generation for new entries. This is the existing call, expanded with structured output fields.
2. **Consolidation call:** Semantic conflict resolution (read existing learned.md + compare against new observations) + pattern detection (analyze episodic history for routines). This requires different context (existing memory state) and is complex enough to warrant its own call.

Splitting into two calls improves reliability — each call has a focused task with clear structured output. The cost is one additional Claude call per Librarian cycle (default: hourly), which is minimal.

### 8.3 Contextual Decay (Significance-Based Migration)

Replaces the time-based stub (`_apply_decay()` returning 0).

During each consolidation cycle, the Librarian evaluates hot entries for migration to cold:

```
For each hot entry:
  migration_pressure = (
    age_factor(days_old)                       # older = higher pressure
    - significance.overall * 2.0               # high significance resists
    - retrieval_recency(last_retrieved) * 1.5  # recently retrieved resists
    - retrieval_frequency(count) * 1.0         # frequently retrieved resists
  )

  if migration_pressure > DECAY_MIGRATION_THRESHOLD:
    compress_if_needed(entry)
    archive_to_cold(entry)
    remove_from_hot(entry)
```

**Key behaviors:**
- Mundane `state_changed` with no retrievals: migrates after ~1-2 cycles
- Security alert (significance=0.9): stays hot for weeks
- Frequently retrieved entry: stays hot indefinitely
- `DECAY_MIGRATION_THRESHOLD` configurable in `AlfredConfig`

**Replaces:** The existing `DecayScheduler` class (`core/memory/episodic/decay.py`) with its four-tier time-based classification (hot/warm/cold/archive) is deprecated and removed. The significance-based model replaces all four tiers with a continuous migration pressure score. The two-store architecture (hot Redis + cold SQLite) remains, but migration between them is driven by significance, not age. The existing `AlfredConfig` fields `EPISODIC_HOT_DAYS` and `EPISODIC_COMPRESS_DAYS` are deprecated and removed alongside `DecayScheduler`.

**Decay formula curves:** All functions in the migration pressure formula return 0.0-1.0:
- `age_factor(days)` — linear: `min(days / 30.0, 1.0)` (caps at 30 days)
- `retrieval_recency(last_retrieved)` — exponential decay: `exp(-days_since_last / 7.0)` (half-life ~5 days)
- `retrieval_frequency(count)` — logarithmic: `min(log2(count + 1) / 5.0, 1.0)` (caps at 31 retrievals)

### 8.4 Compression at Cold Migration

When multiple related entries migrate together (same entity cluster, same day), the Librarian compresses them into a single summary before writing to cold:

- Group low-significance entries by entity + date
- LLM summarizes each group: "Lights turned on in kitchen 14 times on March 20" instead of 14 entries
- Summary inherits the highest significance score from the group
- Original individual entries ARE preserved in cold storage alongside the summary, but marked with a `compressed_into` field pointing to the summary entry ID. This ensures detailed information remains searchable ("what happened with the front door on March 20?") while the summary provides efficient context for broader queries. The individual entries are excluded from involuntary recall (summary surfaces instead) but available via deliberate recall with specific filters.
- Part of the same batched LLM consolidation call

---

## 9. Pattern Detection — Routine Candidates

Completes the Pillar 5 lifecycle: fluid intelligence -> crystallized intelligence.

### 9.1 Detection

During consolidation step 7, the Librarian queries episodic entries for recurring patterns:

1. Group entries by time-of-day + entity cluster across multiple days
2. Pass candidates to Claude: "Here are action sequences that have repeated N times over M days. Describe each pattern as a routine and rate confidence that this is an intentional habit vs coincidence."
3. Claude returns candidate routines with trigger patterns and step descriptions

**Minimum evidence thresholds (configurable in `AlfredConfig`):**
- At least 3 occurrences over at least 7 days
- Confidence threshold for candidate creation: 0.6

### 9.2 Candidate Creation

Detected patterns become `RoutineSpec` entries:

```python
RoutineSpec(
    name="evening_movie_prep",
    trigger_pattern="Around 8 PM on weekday evenings",
    steps=[
        RoutineStep(description="Dim living room lights to 30%"),
        RoutineStep(description="Turn on TV"),
    ],
    confidence=0.72,
    learned_from=["ep-abc123", "ep-def456", "ep-ghi789"],
    state="candidate",
)
```

`RoutineStep.action` (`ActionPayload`) is `None` at candidate stage. Populated at promotion time.

### 9.3 Suggestion Flow

Candidates don't act autonomously. The Conscious Engine checks for routine suggestions as part of involuntary recall:

1. Routine candidates are indexed in the unified context index (type `routine`). Involuntary recall can surface them when the query + time-of-day matches a trigger pattern embedding.
2. Additionally, on each request, the engine checks if any candidate's `trigger_pattern` matches the current time window (e.g., "Around 8 PM" matches 7:30-8:30 PM). This is a simple time-range check on structured fields, not embedding search.
3. If a candidate surfaces through either mechanism and hasn't been suggested recently (checked via `last_suggested` timestamp on `RoutineSpec` — minimum 24 hours between suggestions for the same routine), Alfred suggests once: *"Sir, I've noticed you tend to start a film around 8. Shall I prepare the usual?"*
3. **Accepted** -> `state="active"`, `ActionPayload` populated, optionally promoted to Trigger Engine
4. **Rejected** -> `state="archived"`, never suggested again
5. **Ignored** (no response within session) -> stays candidate, confidence decays by 0.05 per Librarian cycle. Below 0.3 -> archived

### 9.4 Hit Tracking (Active Routines)

For active routines, the Librarian checks each cycle:

- Pattern occurred -> `last_hit` updated, `consecutive_misses` reset to 0
- Pattern didn't occur -> `consecutive_misses += 1`
- 3 consecutive misses -> `state="dormant"`
- Dormant + 30 days no activity -> `state="archived"`
- Archived routines remain readable ("you used to...") but never acted on
- When a routine transitions to `archived` or `rejected`, its entry in the unified context index (`idx:context`) is deleted to prevent stale results in involuntary recall. The YAML file on disk is retained for deliberate recall.

---

## 10. Eval Coverage

Scoped to metrics that directly validate the memory system.

### 10.1 MemoryRetrievalPrecision — Upgrade

Current: keyword overlap stub. New: LLM-as-judge.

1. Run conscious eval scenario (e.g., "good morning" briefing)
2. Capture involuntary recall results (what was injected into context)
3. Capture Claude's response
4. LLM-as-judge: "Which of these injected memories were actually referenced or used in the response?"
5. Returns precision ratio: used / injected

This is the primary metric for whether two-stage retrieval is pulling the right context.

### 10.2 ProactivityRelevanceScore — Upgrade

Current: hardcoded 0.5 stub. New: LLM-as-judge.

With pattern detection and routine suggestions operational, this metric matters. "Was this proactive suggestion relevant and useful given the user's context and patterns?"

### 10.3 SemanticKeyQuality — New Metric

Validates the dual-embedding approach:

1. For a set of test queries, search using `embedding_content` only -> results A
2. Search using `embedding_semantic` only -> results B
3. Human-judged or LLM-judged relevance comparison
4. Reports whether semantic keys improve retrieval, and by how much

Determines if the dual-embedding cost is justified or if one field dominates.

### 10.4 E2E Demo Script

`evals/e2e/demo_good_morning.py` — currently missing (CLI crashes at import).

Implement as full pipeline test: user request -> involuntary recall -> Claude reasoning -> memory tool calls -> response. Validates the entire memory system end-to-end with real (or mocked) infrastructure.

### 10.5 Mock Integration Injection

YAML scenarios have `mock_integrations` fields that are parsed but never injected into the Conscious Engine during live evals. Wire them so controlled integration responses are used during eval runs.

---

## 11. Schema Changes

### 11.1 EpisodicEntry (Updated)

```python
class SignificanceScore(BaseModel):
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
    significance: SignificanceScore      # replaces valence
    semantic_key: str = ""               # retrieval-optimized rewrite
    retrieval_count: int = 0             # incremented on recall
    last_retrieved: datetime | None = None
    compressed_into: str | None = None   # if compressed, points to summary entry ID


class EpisodicResult(BaseModel):
    """Result from episodic memory recall (hot or cold)."""
    entry: EpisodicEntry
    score: float                         # combined relevance score
    source_store: Literal["hot", "cold"]


# MemoryResult is an alias for SearchResult (Section 3.3) — used by the
# recall_memories tool to return results from the unified context index.
# The tool handler converts SearchResult -> MemoryResult for the function
# call response format.
MemoryResult = SearchResult
```

### 11.2 New Configuration Fields

Added to `AlfredConfig`:

```python
# Embedding
EMBEDDING_MODEL: str = "google/embeddinggemma-300m"
EMBEDDING_DIM: int = 768

# Significance weights
SIGNIFICANCE_WEIGHT_SAFETY: float = 0.35
SIGNIFICANCE_WEIGHT_NOVELTY: float = 0.25
SIGNIFICANCE_WEIGHT_PERSONAL: float = 0.25
SIGNIFICANCE_WEIGHT_EMOTIONAL: float = 0.15

# Decay
DECAY_MIGRATION_THRESHOLD: float = 1.0

# Involuntary recall
INVOLUNTARY_RECALL_LIMIT: int = 10
INVOLUNTARY_RECALL_THRESHOLD: float = 0.5  # minimum similarity score (0.3 is too low for 768-dim)

# Semantic conflict resolution
CONFLICT_MIN_OBSERVATIONS: int = 5   # matches expanded vision spec
CONFLICT_MIN_DAYS: int = 14          # matches expanded vision spec

# Pattern detection
PATTERN_MIN_OCCURRENCES: int = 3
PATTERN_MIN_DAYS: int = 7
PATTERN_CONFIDENCE_THRESHOLD: float = 0.6
ROUTINE_DECAY_PER_CYCLE: float = 0.05
ROUTINE_ARCHIVE_THRESHOLD: float = 0.3
ROUTINE_SUGGESTION_COOLDOWN_HOURS: int = 24  # minimum hours between re-suggesting same routine
```

### 11.3 RoutineSpec Updates

The existing `RoutineSpec` schema gains one new field:

```python
class RoutineSpec(BaseModel):
    # ... existing fields unchanged ...
    last_suggested: datetime | None = None  # throttles cross-session re-suggestion
```

### 11.3 New Redis Keys

Added to `shared/streams.py`:

```python
CONTEXT_INDEX = "idx:context"           # RediSearch index for unified context
CONTEXT_PREFIX = "ctx:"                 # Hash prefix for context entries
ENTITY_FREQUENCY_KEY = "alfred:entity:freq"  # Sorted set for novelty scoring
```

---

## 12. Vertical Slice Build Order

Implementation follows a vertical slice approach — each step delivers a testable, working capability before expanding breadth.

1. **Prerequisites** — redis-stack migration, EmbeddingGemma-300M swap, `EmbeddingProvider` + `VectorStore` abstractions
2. **Unified context index** — RediSearch index creation, `RedisVectorStore` implementation, dual-embedding write path
3. **Cold storage upgrade** — `SqliteVecStore` with `sqlite-vec`, dual-embedding cold storage, schema migration v2
   - *Steps 2 and 3 are independent and can be parallelized*
4. **Episodic search** — `EpisodicMemory` class with hot + cold recall, retrieval tracking. Deprecates `EpisodicStore` and `EpisodicSearch` (both superseded by `EpisodicMemory` + `VectorStore` abstractions). During transition, `EpisodicMemory` wraps `SqliteVecStore` for cold reads so existing cold data remains accessible.
   - *Depends on steps 2 and 3*
5. **Significance model** — `SignificanceScore` schema, heuristic scoring at write time, entity frequency tracking
   - *Independent of step 4 — can be parallelized with it*
6. **Two-stage context assembly** — broken into sub-steps: (a) add involuntary recall to `process_request` without changing assembler interface, (b) implement `MemoryFeature` and register memory tools via `ToolRegistry`, (c) refactor `ContextAssembler` to new interface, remove HA state from base context. Migrate all `ContextAssembler` callers (engine, evals) atomically in sub-step (c).
   - *Depends on steps 4 and 5*
7. **Librarian upgrade** — two-call consolidation (analysis + consolidation), LLM-refined significance + semantic keys, semantic conflict resolution, contextual decay with compression
   - *Depends on steps 5 and 6*
8. **Pattern detection** — routine candidate creation, suggestion flow, hit tracking, lifecycle management
   - *Depends on step 7*
9. **Eval coverage** — MemoryRetrievalPrecision upgrade, ProactivityRelevanceScore upgrade, SemanticKeyQuality metric, e2e demo, mock integration injection
   - *Can begin after step 6, run in parallel with steps 7-8*

---

## 13. What This Spec Does NOT Cover

Deferred to future phases or backlog:

- **WebAuthn / voice enrollment** — security, not memory (D1, D2)
- **Channel rate limiting** — production hardening (D10)
- **Streaming TTS** — channel optimization (D11)
- **Redis-down fallback** — resilience (D12)
- **Runtime config hot-reload** — infrastructure (D13)
- **Nested OTel spans** — observability depth (D14)
- **Logging sinks** — infrastructure (D15)
- **Reflex via DomainRouter** — System 1 routing (D20)
- **Priority-tier context trimming** — added to backlog as future optimization if telemetry shows need
- **DeepEval framework integration** — metrics are standalone Python classes; DeepEval coupling deferred
- **System 2 observing System 1** — Phase 4 feature (D8)
- **Anomaly detection** — Phase 4 feature
- **Integration-driven push notifications** — Phase 4 feature
