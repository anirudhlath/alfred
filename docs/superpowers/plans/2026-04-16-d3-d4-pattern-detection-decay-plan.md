# D3+D4: Pattern Detection & Contextual Decay — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the Librarian's pattern detection lifecycle (D3) and contextual decay (D4) so that observed behaviors can become autonomous routines and stale memories decay gracefully.

**Architecture:** Bottom-up — fix data integrity first (retrieval stats → decay formula → compression), then build features on top (routine indexing → suggestion flow → proactive notifications → trigger promotion). Each layer is solid before the next depends on it.

**Tech Stack:** Python 3.13, async/await, Pydantic v2, Redis (RediSearch HNSW), SQLite (sqlite-vec), LiteLLM (Claude), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-04-16-d3-d4-pattern-detection-decay-design.md`

---

### Task 1: Add `update_metadata` to VectorStore ABC

**Files:**
- Modify: `core/memory/vector_store.py:33-63`
- Modify: `core/memory/redis_vector_store.py:25-253`
- Modify: `core/memory/sqlite_vec_store.py:37-381`
- Test: `tests/core/memory/test_redis_vector_store.py`

- [ ] **Step 1: Write the failing test for `update_metadata`**

In `tests/core/memory/test_redis_vector_store.py`, add:

```python
@pytest.mark.asyncio
async def test_update_metadata_calls_hset(store: RedisVectorStore, mock_redis: AsyncMock) -> None:
    """update_metadata should HSET the given fields on the entry's Redis hash."""
    store._index_ready = True
    await store.update_metadata("ep-1", {"retrieval_count": 5, "last_retrieved": 1711000000.0})
    mock_redis.hset.assert_called_once_with(
        "ctx:ep-1", mapping={"retrieval_count": 5, "last_retrieved": 1711000000.0}
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/memory/test_redis_vector_store.py::test_update_metadata_calls_hset -v`
Expected: FAIL — `update_metadata` does not exist.

- [ ] **Step 3: Add `update_metadata` to VectorStore ABC**

In `core/memory/vector_store.py`, after the `count` method (line 63), add:

```python
    @abstractmethod
    async def update_metadata(
        self,
        id: str,  # noqa: A002
        fields: dict[str, str | float | int],
    ) -> None:
        """Update specific metadata fields in-place (no re-embedding)."""
        ...
```

- [ ] **Step 4: Implement in RedisVectorStore**

In `core/memory/redis_vector_store.py`, after the `count` method (after line 253), add:

```python
    async def update_metadata(
        self,
        id: str,  # noqa: A002
        fields: dict[str, str | float | int],
    ) -> None:
        """Update metadata fields on an existing Redis hash entry."""
        key = f"{CONTEXT_PREFIX}{id}"
        await self._redis.hset(key, mapping=fields)  # type: ignore[misc]
```

- [ ] **Step 5: Implement no-op in SqliteVecStore**

In `core/memory/sqlite_vec_store.py`, after the `close` method (after line 381), add:

```python
    async def update_metadata(
        self,
        id: str,  # noqa: A002
        fields: dict[str, str | float | int],
    ) -> None:
        """No-op — cold store entries don't need retrieval tracking."""
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/memory/test_redis_vector_store.py -v`
Expected: ALL PASS.

- [ ] **Step 7: Run full test suite to verify no regressions**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -x -q`
Expected: ALL PASS (780+ tests).

- [ ] **Step 8: Lint and type check**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && ruff check . --fix && ruff format . && mypy --strict core/memory/vector_store.py core/memory/redis_vector_store.py core/memory/sqlite_vec_store.py`
Expected: No errors.

- [ ] **Step 9: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add core/memory/vector_store.py core/memory/redis_vector_store.py core/memory/sqlite_vec_store.py tests/core/memory/test_redis_vector_store.py
git commit -m "feat(d4): add update_metadata to VectorStore ABC for retrieval tracking"
```

---

### Task 2: Persist retrieval stats in `EpisodicMemory.recall()`

**Files:**
- Modify: `core/memory/episodic/memory.py:62-125`
- Test: `tests/core/memory/test_episodic_memory.py`

- [ ] **Step 1: Write the failing test**

In `tests/core/memory/test_episodic_memory.py`, add at the end:

```python
@pytest.mark.asyncio
async def test_recall_persists_retrieval_count_to_hot_store(
    episodic_memory: EpisodicMemory,
    mock_hot_store: AsyncMock,
    mock_cold_store: AsyncMock,
) -> None:
    """recall() must call update_metadata on hot store for each hot result."""
    mock_hot_store.search.return_value = [
        _make_search_result(id="h1", score=0.9, retrieval_count=3),
    ]
    mock_hot_store.update_metadata = AsyncMock()

    await episodic_memory.recall("query")

    mock_hot_store.update_metadata.assert_awaited_once()
    call_args = mock_hot_store.update_metadata.await_args
    assert call_args[0][0] == "h1"
    fields = call_args[0][1]
    assert fields["retrieval_count"] == 4
    assert "last_retrieved" in fields


@pytest.mark.asyncio
async def test_recall_does_not_persist_stats_for_cold_results(
    episodic_memory: EpisodicMemory,
    mock_hot_store: AsyncMock,
    mock_cold_store: AsyncMock,
) -> None:
    """recall() must NOT call update_metadata for cold store results."""
    mock_cold_store.search.return_value = [
        _make_search_result(id="c1", score=0.9),
    ]
    mock_hot_store.update_metadata = AsyncMock()

    await episodic_memory.recall("query")

    mock_hot_store.update_metadata.assert_not_awaited()
```

Also update `mock_hot_store` and `mock_cold_store` fixtures to include `update_metadata`:

In the `mock_hot_store` fixture, add after `store.count = AsyncMock(return_value=0)`:
```python
    store.update_metadata = AsyncMock()
```

In the `mock_cold_store` fixture, add the same line.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/memory/test_episodic_memory.py::test_recall_persists_retrieval_count_to_hot_store -v`
Expected: FAIL — `update_metadata` never called.

- [ ] **Step 3: Implement retrieval stats persistence**

In `core/memory/episodic/memory.py`, modify the `recall` method. After the line `merged = merged[:limit]` (line 97), add a timestamp:

Replace the block from line 99 to line 125 (`# Convert to EpisodicResult...` through `return episodic_results`) with:

```python
        # Persist retrieval stats for hot-store results
        now_ts = datetime.now(UTC).timestamp()
        for search_result, source_store in merged:
            if source_store == "hot":
                await self._hot.update_metadata(
                    search_result.id,
                    {
                        "retrieval_count": search_result.metadata.retrieval_count + 1,
                        "last_retrieved": now_ts,
                    },
                )

        # Convert to EpisodicResult, increment retrieval_count
        episodic_results: list[EpisodicResult] = []
        for search_result, source_store in merged:
            entities = (
                [e for e in search_result.metadata.entities.split(",") if e]
                if search_result.metadata.entities
                else []
            )
            entry = EpisodicEntry(
                id=search_result.id,
                timestamp=datetime.fromtimestamp(search_result.metadata.timestamp, tz=UTC),
                source=search_result.metadata.source,
                summary=search_result.content,
                entities=entities,
                significance=SignificanceScore(overall=search_result.metadata.significance),
                semantic_key=search_result.semantic_key,
                retrieval_count=search_result.metadata.retrieval_count + 1,
            )
            episodic_results.append(
                EpisodicResult(
                    entry=entry,
                    score=search_result.score,
                    source_store=source_store,  # type: ignore[arg-type]
                )
            )

        return episodic_results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/memory/test_episodic_memory.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -x -q`
Expected: ALL PASS.

- [ ] **Step 6: Lint and type check**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && ruff check . --fix && ruff format . && mypy --strict core/memory/episodic/memory.py`

- [ ] **Step 7: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add core/memory/episodic/memory.py tests/core/memory/test_episodic_memory.py
git commit -m "feat(d4): persist retrieval_count and last_retrieved on recall"
```

---

### Task 3: Upgrade decay formula

**Files:**
- Modify: `core/librarian/consolidator.py:445-509`
- Test: `tests/core/librarian/test_consolidator_v2.py`

- [ ] **Step 1: Write parametrized failing tests for the new formula**

In `tests/core/librarian/test_consolidator_v2.py`, add at the end:

```python
# ---------------------------------------------------------------------------
# Part F: Upgraded decay formula tests
# ---------------------------------------------------------------------------


def _make_decay_search_result(
    id: str = "ep-1",
    age_days: float = 30.0,
    significance: float = 0.1,
    retrieval_count: int = 0,
    last_retrieved_days_ago: float | None = None,
) -> SearchResult:
    """Helper to create a SearchResult for decay testing."""
    import time

    now = time.time()
    timestamp = now - (age_days * 86400)
    last_retrieved = 0.0
    if last_retrieved_days_ago is not None:
        last_retrieved = now - (last_retrieved_days_ago * 86400)
    return SearchResult(
        id=id,
        score=0.5,
        content=f"entry {id}",
        semantic_key=f"key {id}",
        metadata=ContextMetadata(
            type="episodic",
            source="conversation",
            entities="light.kitchen",
            timestamp=timestamp,
            significance=significance,
            retrieval_count=retrieval_count,
            last_retrieved=last_retrieved,
        ),
    )


@pytest.mark.asyncio
async def test_decay_high_significance_resists_migration() -> None:
    """Entry with high significance should NOT be migrated even if old."""
    episodic_memory = AsyncMock()
    context_index = AsyncMock()
    context_index.search_text = AsyncMock(
        return_value=[_make_decay_search_result(significance=0.8, age_days=30)]
    )

    librarian = _make_librarian(
        episodic_memory=episodic_memory, context_index=context_index
    )
    count = await librarian._apply_decay(decay_migration_threshold=0.5)
    assert count == 0
    episodic_memory.copy_to_cold_and_remove.assert_not_awaited()


@pytest.mark.asyncio
async def test_decay_old_low_significance_migrates() -> None:
    """Old entry with low significance and no retrievals should be migrated."""
    episodic_memory = AsyncMock()
    context_index = AsyncMock()
    context_index.search_text = AsyncMock(
        return_value=[_make_decay_search_result(significance=0.1, age_days=30)]
    )

    librarian = _make_librarian(
        episodic_memory=episodic_memory, context_index=context_index
    )
    count = await librarian._apply_decay(decay_migration_threshold=0.5)
    assert count == 1
    episodic_memory.copy_to_cold_and_remove.assert_awaited_once()


@pytest.mark.asyncio
async def test_decay_recently_retrieved_resists_migration() -> None:
    """Entry retrieved yesterday should resist migration due to recency."""
    episodic_memory = AsyncMock()
    context_index = AsyncMock()
    context_index.search_text = AsyncMock(
        return_value=[
            _make_decay_search_result(
                significance=0.1,
                age_days=30,
                retrieval_count=1,
                last_retrieved_days_ago=1,
            )
        ]
    )

    librarian = _make_librarian(
        episodic_memory=episodic_memory, context_index=context_index
    )
    count = await librarian._apply_decay(decay_migration_threshold=0.5)
    assert count == 0


@pytest.mark.asyncio
async def test_decay_frequently_retrieved_resists_migration() -> None:
    """Entry with high retrieval count should resist migration."""
    episodic_memory = AsyncMock()
    context_index = AsyncMock()
    context_index.search_text = AsyncMock(
        return_value=[
            _make_decay_search_result(
                significance=0.1,
                age_days=30,
                retrieval_count=10,
                last_retrieved_days_ago=15,
            )
        ]
    )

    librarian = _make_librarian(
        episodic_memory=episodic_memory, context_index=context_index
    )
    count = await librarian._apply_decay(decay_migration_threshold=0.5)
    assert count == 0


@pytest.mark.asyncio
async def test_decay_last_retrieved_zero_fallback_to_age() -> None:
    """When last_retrieved=0 (pre-stats-fix), days_since_last_retrieved == age_days."""
    episodic_memory = AsyncMock()
    context_index = AsyncMock()
    # Old entry, low significance, last_retrieved=0.0 (default/never)
    context_index.search_text = AsyncMock(
        return_value=[
            _make_decay_search_result(significance=0.1, age_days=60)
        ]
    )

    librarian = _make_librarian(
        episodic_memory=episodic_memory, context_index=context_index
    )
    count = await librarian._apply_decay(decay_migration_threshold=0.5)
    assert count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/librarian/test_consolidator_v2.py::test_decay_high_significance_resists_migration -v`
Expected: FAIL (current formula may not produce expected behavior).

- [ ] **Step 3: Implement the upgraded decay formula**

In `core/librarian/consolidator.py`, replace the `_apply_decay` method (lines 445-509) with:

```python
    async def _apply_decay(
        self,
        decay_migration_threshold: float = 1.0,
        search_query: str = "general context memory event",
        search_limit: int = 500,
    ) -> int:
        """Migrate old low-significance hot entries to cold storage.

        Uses a subtractive formula where significance and retrieval
        activity resist the migration pressure from age:

            age_factor = min(days_old / 30.0, 1.0)
            retrieval_recency = exp(-days_since_last_retrieved / 7.0)
            retrieval_frequency = min(log2(count + 1) / 5.0, 1.0)

            pressure = (
                age_factor
                - significance * 2.0
                - retrieval_recency * 1.5
                - retrieval_frequency * 1.0
            )

        Entries with pressure > decay_migration_threshold are migrated to cold.
        Returns the number of entries migrated.
        """
        from math import exp, log2

        try:
            results = await self._context_index.search_text(
                query=search_query,
                limit=search_limit,
                min_similarity=0.0,
            )
        except Exception as exc:
            logger.warning("Decay: failed to retrieve hot entries: %s", exc)
            return 0

        now = datetime.now(UTC).timestamp()
        migrated = 0

        for result in results:
            if result.metadata.type != "episodic":
                continue

            timestamp = result.metadata.timestamp
            if timestamp <= 0:
                continue

            age_days = (now - timestamp) / 86400.0
            significance = result.metadata.significance
            retrieval_count = result.metadata.retrieval_count
            last_retrieved = result.metadata.last_retrieved

            # Fallback: if last_retrieved was never set, assume never retrieved
            # (days_since = age_days gives no recency protection)
            if last_retrieved > 0:
                days_since_last_retrieved = (now - last_retrieved) / 86400.0
            else:
                days_since_last_retrieved = age_days

            age_factor = min(age_days / 30.0, 1.0)
            retrieval_recency = exp(-days_since_last_retrieved / 7.0)
            retrieval_frequency = min(log2(retrieval_count + 1) / 5.0, 1.0)

            pressure = (
                age_factor
                - significance * 2.0
                - retrieval_recency * 1.5
                - retrieval_frequency * 1.0
            )

            if pressure > decay_migration_threshold:
                try:
                    await self._episodic_memory.copy_to_cold_and_remove(result)
                    migrated += 1
                    logger.debug(
                        "Decayed entry %s (age=%.1fd, sig=%.2f, pressure=%.2f)",
                        result.id,
                        age_days,
                        significance,
                        pressure,
                    )
                except Exception as exc:
                    logger.warning("Decay: failed to migrate entry %s: %s", result.id, exc)

        if migrated:
            logger.info("Decay: migrated %d entries to cold storage", migrated)
        return migrated
```

- [ ] **Step 4: Run decay tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/librarian/test_consolidator_v2.py -k "decay" -v`
Expected: ALL PASS.

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -x -q`
Expected: ALL PASS.

- [ ] **Step 6: Lint and type check**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && ruff check . --fix && ruff format . && mypy --strict core/librarian/consolidator.py`

- [ ] **Step 7: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add core/librarian/consolidator.py tests/core/librarian/test_consolidator_v2.py
git commit -m "feat(d4): upgrade decay to subtractive formula with recency and frequency"
```

---

### Task 4: Add compression at cold migration

**Files:**
- Modify: `core/librarian/consolidator.py` (inside `_apply_decay`)
- Test: `tests/core/librarian/test_consolidator_v2.py`

- [ ] **Step 1: Write failing tests for compression grouping**

In `tests/core/librarian/test_consolidator_v2.py`, add:

```python
# ---------------------------------------------------------------------------
# Part G: Compression tests
# ---------------------------------------------------------------------------

from core.librarian.consolidator import _group_by_entity_date


def test_group_by_entity_date_groups_same_entity_same_day() -> None:
    """Entries sharing an entity on the same day should be grouped."""
    import time

    now = time.time()
    results = [
        _make_decay_search_result(id="a", age_days=31, significance=0.05),
        _make_decay_search_result(id="b", age_days=31, significance=0.05),
    ]
    # Both have entities="light.kitchen"
    groups, ungrouped = _group_by_entity_date(results)
    assert len(groups) == 1
    assert len(groups[0]) == 2
    assert len(ungrouped) == 0


def test_group_by_entity_date_no_entities_ungrouped() -> None:
    """Entries with no entities should not be grouped."""
    result = _make_decay_search_result(id="lonely")
    result.metadata.entities = ""
    groups, ungrouped = _group_by_entity_date([result])
    assert len(groups) == 0
    assert len(ungrouped) == 1


def test_group_by_entity_date_different_days_separate() -> None:
    """Entries on different days should not be grouped even with same entity."""
    results = [
        _make_decay_search_result(id="a", age_days=31),
        _make_decay_search_result(id="b", age_days=32),
    ]
    groups, ungrouped = _group_by_entity_date(results)
    # Each is a group of 1 → becomes ungrouped (need 2+ for a group)
    assert len(ungrouped) == 2


@pytest.mark.asyncio
async def test_compression_creates_summary_and_marks_originals() -> None:
    """Compression should create a summary entry and mark originals."""
    import time

    now = time.time()
    base_ts = now - (31 * 86400)

    results = [
        SearchResult(
            id="a",
            score=0.5,
            content="kitchen light turned on",
            semantic_key="kitchen light on",
            metadata=ContextMetadata(
                type="episodic",
                source="system1_action",
                entities="light.kitchen",
                timestamp=base_ts,
                significance=0.1,
                retrieval_count=0,
                last_retrieved=0.0,
            ),
        ),
        SearchResult(
            id="b",
            score=0.5,
            content="kitchen light turned off",
            semantic_key="kitchen light off",
            metadata=ContextMetadata(
                type="episodic",
                source="system1_action",
                entities="light.kitchen",
                timestamp=base_ts + 3600,
                significance=0.15,
                retrieval_count=1,
                last_retrieved=0.0,
            ),
        ),
    ]

    episodic_memory = AsyncMock()
    context_index = AsyncMock()
    context_index.search_text = AsyncMock(return_value=results)

    librarian = _make_librarian(
        episodic_memory=episodic_memory, context_index=context_index
    )

    llm_response = AsyncMock()
    llm_response.choices = [
        AsyncMock(
            message=AsyncMock(
                content=json.dumps(
                    {
                        "summary": "Kitchen light was toggled on then off during the evening.",
                        "semantic_key": "kitchen light evening activity",
                    }
                )
            )
        )
    ]

    with patch("litellm.acompletion", return_value=llm_response):
        count = await librarian._apply_decay(decay_migration_threshold=-10.0)

    # Should have migrated 2 originals + 1 summary = called cold.add via copy_to_cold_and_remove
    # But implementation writes summary directly to cold, then originals with compressed_into
    assert episodic_memory.copy_to_cold_and_remove.await_count >= 2


@pytest.mark.asyncio
async def test_compression_single_entry_no_llm_call() -> None:
    """A single entry (no group) should migrate without LLM compression."""
    result = _make_decay_search_result(id="solo", significance=0.05, age_days=60)
    result.metadata.entities = ""

    episodic_memory = AsyncMock()
    context_index = AsyncMock()
    context_index.search_text = AsyncMock(return_value=[result])

    librarian = _make_librarian(
        episodic_memory=episodic_memory, context_index=context_index
    )

    with patch("litellm.acompletion") as mock_llm:
        count = await librarian._apply_decay(decay_migration_threshold=-10.0)

    mock_llm.assert_not_called()
    assert count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/librarian/test_consolidator_v2.py::test_group_by_entity_date_groups_same_entity_same_day -v`
Expected: FAIL — `_group_by_entity_date` does not exist.

- [ ] **Step 3: Implement grouping helper**

In `core/librarian/consolidator.py`, add this module-level function (before the `Librarian` class):

```python
def _group_by_entity_date(
    results: list[SearchResult],
) -> tuple[list[list[SearchResult]], list[SearchResult]]:
    """Group decayed entries by (shared_entity, date) for compression.

    Returns (groups_of_2_or_more, ungrouped_singles).
    Entries with no entities are always ungrouped.
    """
    from collections import defaultdict

    buckets: dict[tuple[str, str], list[SearchResult]] = defaultdict(list)
    ungrouped: list[SearchResult] = []

    for result in results:
        entities_str = result.metadata.entities
        if not entities_str:
            ungrouped.append(result)
            continue

        # Date from timestamp
        ts = result.metadata.timestamp
        date_str = datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d")

        # Each entity gets a bucket key
        entities = [e.strip() for e in entities_str.split(",") if e.strip()]
        if not entities:
            ungrouped.append(result)
            continue

        # Use first entity as the primary grouping key
        # (entries with shared entities on the same day group together)
        placed = False
        for entity in entities:
            key = (entity, date_str)
            if key in buckets:
                buckets[key].append(result)
                placed = True
                break
        if not placed:
            # No existing bucket — start a new one with first entity
            buckets[(entities[0], date_str)].append(result)

    groups: list[list[SearchResult]] = []
    for bucket in buckets.values():
        if len(bucket) >= 2:
            groups.append(bucket)
        else:
            ungrouped.extend(bucket)

    return groups, ungrouped
```

Also add the `SearchResult` import at the top if not already present (it should be since it's used in `_apply_decay`).

- [ ] **Step 4: Implement compression in `_apply_decay`**

Replace the `_apply_decay` method with the version that includes compression. After identifying entries that exceed the threshold, group them, compress groups via LLM, and migrate:

```python
    async def _apply_decay(
        self,
        decay_migration_threshold: float = 1.0,
        search_query: str = "general context memory event",
        search_limit: int = 500,
    ) -> int:
        """Migrate old low-significance hot entries to cold storage.

        Uses a subtractive formula where significance and retrieval activity
        resist the migration pressure from age. Groups entries by entity+date
        and compresses them via LLM summarization before cold migration.

        Returns the number of entries migrated.
        """
        from math import exp, log2

        try:
            results = await self._context_index.search_text(
                query=search_query,
                limit=search_limit,
                min_similarity=0.0,
            )
        except Exception as exc:
            logger.warning("Decay: failed to retrieve hot entries: %s", exc)
            return 0

        now = datetime.now(UTC).timestamp()
        to_migrate: list[SearchResult] = []

        for result in results:
            if result.metadata.type != "episodic":
                continue

            timestamp = result.metadata.timestamp
            if timestamp <= 0:
                continue

            age_days = (now - timestamp) / 86400.0
            significance = result.metadata.significance
            retrieval_count = result.metadata.retrieval_count
            last_retrieved = result.metadata.last_retrieved

            if last_retrieved > 0:
                days_since_last_retrieved = (now - last_retrieved) / 86400.0
            else:
                days_since_last_retrieved = age_days

            age_factor = min(age_days / 30.0, 1.0)
            retrieval_recency = exp(-days_since_last_retrieved / 7.0)
            retrieval_frequency = min(log2(retrieval_count + 1) / 5.0, 1.0)

            pressure = (
                age_factor
                - significance * 2.0
                - retrieval_recency * 1.5
                - retrieval_frequency * 1.0
            )

            if pressure > decay_migration_threshold:
                to_migrate.append(result)

        if not to_migrate:
            return 0

        # Group by entity+date for compression
        groups, ungrouped = _group_by_entity_date(to_migrate)
        migrated = 0

        # Compress groups of 2+ entries
        for group in groups:
            try:
                migrated += await self._compress_and_migrate(group)
            except Exception as exc:
                logger.warning("Compression failed for group — migrating individually: %s", exc)
                for result in group:
                    try:
                        await self._episodic_memory.copy_to_cold_and_remove(result)
                        migrated += 1
                    except Exception as e2:
                        logger.warning("Decay: failed to migrate entry %s: %s", result.id, e2)

        # Migrate ungrouped entries individually
        for result in ungrouped:
            try:
                await self._episodic_memory.copy_to_cold_and_remove(result)
                migrated += 1
                logger.debug(
                    "Decayed entry %s (sig=%.2f)",
                    result.id,
                    result.metadata.significance,
                )
            except Exception as exc:
                logger.warning("Decay: failed to migrate entry %s: %s", result.id, exc)

        if migrated:
            logger.info("Decay: migrated %d entries to cold storage", migrated)
        return migrated

    async def _compress_and_migrate(self, group: list[SearchResult]) -> int:
        """Compress a group of related entries into a summary, then migrate all to cold.

        Returns the number of entries migrated (including the summary).
        """
        # Build summaries for the LLM
        entries_text = "\n".join(
            f"- [{r.id}] {datetime.fromtimestamp(r.metadata.timestamp, tz=UTC).strftime('%H:%M')} "
            f"{r.content}"
            for r in group
        )

        summary_text = ""
        semantic_key = ""

        if self._api_key:
            try:
                import litellm

                response = await litellm.acompletion(
                    model=self._model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Summarize these related home automation events into a single "
                                "concise paragraph. Also provide a semantic_key (a short phrase "
                                "for vector search). Return JSON: "
                                '{"summary": "...", "semantic_key": "..."}'
                            ),
                        },
                        {"role": "user", "content": entries_text},
                    ],
                    max_tokens=300,
                    api_key=self._api_key,
                )
                raw = response.choices[0].message.content or "{}"
                parsed = json.loads(raw)
                summary_text = parsed.get("summary", "")
                semantic_key = parsed.get("semantic_key", "")
            except Exception as exc:
                logger.warning("Compression LLM call failed: %s", exc)

        if not summary_text:
            # Fallback: concatenate summaries
            summary_text = "; ".join(r.content for r in group)
            semantic_key = summary_text[:100]

        # Create summary entry
        summary_id = str(uuid4())
        all_entities: set[str] = set()
        max_sig = 0.0
        min_ts = float("inf")
        total_retrieval_count = 0

        for r in group:
            if r.metadata.entities:
                all_entities.update(e.strip() for e in r.metadata.entities.split(",") if e.strip())
            max_sig = max(max_sig, r.metadata.significance)
            min_ts = min(min_ts, r.metadata.timestamp)
            total_retrieval_count += r.metadata.retrieval_count

        summary_metadata = ContextMetadata(
            type="episodic",
            source="librarian",
            entities=",".join(sorted(all_entities)),
            timestamp=min_ts,
            significance=max_sig,
            retrieval_count=total_retrieval_count,
            last_retrieved=0.0,
        )

        # Write summary to cold store
        import asyncio as _asyncio

        summary_emb, key_emb = await _asyncio.gather(
            self._embedder.embed(summary_text),
            self._embedder.embed(semantic_key),
        )
        await self._cold_store.add(
            id=summary_id,
            content=summary_text,
            semantic_key=semantic_key,
            embedding_content=summary_emb,
            embedding_semantic=key_emb,
            metadata=summary_metadata,
        )

        # Migrate originals with compressed_into marker
        migrated = 0
        for result in group:
            marked = result.model_copy(
                update={
                    "metadata": result.metadata.model_copy(
                        update={"compressed": "yes"}
                    )
                }
            )
            await self._episodic_memory.copy_to_cold_and_remove(marked)
            migrated += 1

        logger.info(
            "Compressed %d entries into summary %s",
            len(group),
            summary_id,
        )
        return migrated
```

- [ ] **Step 5: Add `_embedder` and `_cold_store` references to Librarian**

The `_compress_and_migrate` method needs direct access to the embedder and cold store. In the `Librarian.__init__` method (around line 88-105), the episodic_memory already has `_hot` and `_cold`. We need to expose these.

Add to `Librarian.__init__` after line 89 (`self._episodic_memory = episodic_memory`):

```python
        # Direct store references for compression (writes summary directly to cold)
        self._cold_store = episodic_memory._cold
        self._embedder = episodic_memory._embedder
```

- [ ] **Step 6: Run compression tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/librarian/test_consolidator_v2.py -k "compress or group" -v`
Expected: ALL PASS.

- [ ] **Step 7: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -x -q`
Expected: ALL PASS.

- [ ] **Step 8: Lint and type check**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && ruff check . --fix && ruff format . && mypy --strict core/librarian/consolidator.py`

- [ ] **Step 9: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add core/librarian/consolidator.py tests/core/librarian/test_consolidator_v2.py
git commit -m "feat(d4): add compression at cold migration with LLM summarization"
```

---

### Task 5: Index routines in context index

**Files:**
- Modify: `core/librarian/consolidator.py:511-680` (`_detect_patterns` and `_update_routine_lifecycle`)
- Test: `tests/core/librarian/test_consolidator_v2.py`

- [ ] **Step 1: Write failing test for routine indexing on detection**

In `tests/core/librarian/test_consolidator_v2.py`, add:

```python
# ---------------------------------------------------------------------------
# Part H: Routine indexing tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_patterns_indexes_routine_in_context() -> None:
    """_detect_patterns should call context_index.index_routine after saving."""
    from unittest.mock import MagicMock

    routine_store = MagicMock()
    routine_store.list_all.return_value = []

    context_index = AsyncMock()
    context_index.index_routine = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()

    librarian = _make_librarian(context_index=context_index)
    librarian._routines = routine_store

    entries = [_make_entry_with_id(f"ep-{i}") for i in range(5)]

    llm_payload = json.dumps([
        {
            "name": "test_routine",
            "trigger_pattern": "20:00 daily",
            "steps": [{"description": "Turn off lights"}],
            "confidence": 0.8,
            "learned_from": ["ep-0", "ep-1", "ep-2"],
        }
    ])
    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock(message=AsyncMock(content=llm_payload))]

    with patch("litellm.acompletion", return_value=mock_response):
        result = await librarian._detect_patterns(entries)

    assert len(result) == 1
    context_index.index_routine.assert_awaited_once()
    call_args = context_index.index_routine.await_args
    assert call_args.kwargs["id"] == "test_routine"
    assert "Turn off lights" in call_args.kwargs["content"]


@pytest.mark.asyncio
async def test_lifecycle_archive_removes_from_context_index() -> None:
    """When a routine transitions to archived, it should be removed from context index."""
    import datetime as dt
    from unittest.mock import MagicMock

    from core.memory.schemas import RoutineSpec

    old_hit = dt.datetime(2026, 2, 20, 0, 0, 0, tzinfo=dt.UTC)
    routine = RoutineSpec(
        name="old_routine",
        trigger_pattern="morning",
        steps=[],
        confidence=0.8,
        learned_from=["ep-1"],
        state="dormant",
        last_hit=old_hit,
    )

    routine_store = MagicMock()
    routine_store.list_all.return_value = [routine]

    context_index = AsyncMock()
    context_index._store = AsyncMock()
    context_index._store.delete = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()

    librarian = _make_librarian(context_index=context_index)
    librarian._routines = routine_store

    with patch("core.librarian.consolidator.datetime") as mock_dt:
        now = dt.datetime(2026, 3, 24, 8, 0, 0, tzinfo=dt.UTC)
        mock_dt.now.return_value = now
        mock_dt.UTC = dt.UTC
        mock_dt.timedelta = dt.timedelta

        await librarian._update_routine_lifecycle()

    # Routine should be archived AND removed from context index
    call_args = routine_store.save.call_args[0][0]
    assert call_args.state == "archived"
    context_index._store.delete.assert_awaited_once_with("old_routine")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/librarian/test_consolidator_v2.py::test_detect_patterns_indexes_routine_in_context -v`
Expected: FAIL — `index_routine` never called.

- [ ] **Step 3: Add indexing to `_detect_patterns`**

In `core/librarian/consolidator.py`, in the `_detect_patterns` method, after `self._routines.save(candidate)` (around line 600), add:

```python
                # Index in context for involuntary recall
                routine_content = (
                    f"Routine ({candidate.state}): {candidate.name} "
                    f"— {candidate.trigger_pattern}. "
                    f"Steps: {'; '.join(s.description for s in candidate.steps)}. "
                    f"Confidence: {candidate.confidence:.2f}"
                )
                try:
                    await self._context_index.index_routine(
                        id=candidate.name,
                        content=routine_content,
                        confidence=candidate.confidence,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to index routine '%s': %s", candidate.name, exc
                    )
```

- [ ] **Step 4: Add index removal to `_update_routine_lifecycle`**

In `core/librarian/consolidator.py`, in the `_update_routine_lifecycle` method, after the archive transition (around line 639-645, after `self._routines.save(routine)`), add:

```python
                        # Remove from context index
                        try:
                            await self._context_index._store.delete(routine.name)
                        except Exception as exc:
                            logger.warning(
                                "Failed to remove archived routine '%s' from index: %s",
                                routine.name,
                                exc,
                            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/librarian/test_consolidator_v2.py -k "index" -v`
Expected: ALL PASS.

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -x -q`
Expected: ALL PASS.

- [ ] **Step 7: Lint and type check**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && ruff check . --fix && ruff format . && mypy --strict core/librarian/consolidator.py`

- [ ] **Step 8: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add core/librarian/consolidator.py tests/core/librarian/test_consolidator_v2.py
git commit -m "feat(d3): index routines in context on detection, remove on archive"
```

---

### Task 6: Enhance suggestion flow with routine details

**Files:**
- Modify: `core/conscious/engine.py:464-502` (`_build_routine_hint`)
- Test: `tests/core/conscious/test_engine.py`

The existing `_build_routine_hint` works but the hint content is sparse. Update it to include step details and confidence for the LLM to craft a proper suggestion.

- [ ] **Step 1: Write failing test for enhanced hint content**

In `tests/core/conscious/test_engine.py`, add:

```python
def test_build_routine_hint_includes_steps_and_confidence(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """Hint should include step descriptions and confidence percentage."""
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [
        _make_routine(trigger_pattern="20:00 daily")
    ]

    engine = ConsciousEngine(**mock_deps, routine_store=routine_store)
    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    result = engine._build_routine_hint(now)

    assert "Dim lights" in result
    assert "75%" in result or "0.75" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/conscious/test_engine.py::test_build_routine_hint_includes_steps_and_confidence -v`
Expected: FAIL — current hint doesn't include steps or confidence.

- [ ] **Step 3: Update `_build_routine_hint` to include details**

In `core/conscious/engine.py`, replace the hint building block in `_build_routine_hint` (lines 495-499) with:

```python
            steps_str = "; ".join(s.description for s in routine.steps) if routine.steps else "N/A"
            hints.append(
                f"[routine-suggestion] You've noticed a pattern: {routine.name} "
                f"({routine.trigger_pattern}). Steps: {steps_str}. "
                f"Confidence: {routine.confidence:.0%}. "
                f"If appropriate, suggest this to sir and ask if they'd like "
                f"Alfred to handle this automatically."
            )
            logger.debug("Routine suggestion injected: '%s'", routine.name)
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/conscious/test_engine.py -k "routine" -v`
Expected: ALL PASS.

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -x -q`
Expected: ALL PASS.

- [ ] **Step 6: Lint and type check**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && ruff check . --fix && ruff format . && mypy --strict core/conscious/engine.py`

- [ ] **Step 7: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add core/conscious/engine.py tests/core/conscious/test_engine.py
git commit -m "feat(d3): enhance routine suggestion hint with steps and confidence"
```

---

### Task 7: Add proactive routine suggestion via notifications

**Files:**
- Modify: `core/conscious/engine.py` (add `check_routine_suggestions` method)
- Modify: `core/conscious/__main__.py` (add background task)
- Test: `tests/core/conscious/test_engine.py`

- [ ] **Step 1: Write failing test for proactive suggestion**

In `tests/core/conscious/test_engine.py`, add:

```python
@pytest.mark.asyncio
async def test_check_routine_suggestions_publishes_notification(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """check_routine_suggestions should publish NORMAL notification for matching candidates."""
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [
        _make_routine(trigger_pattern="20:00 daily")
    ]

    notifier = AsyncMock()
    notifier.publish = AsyncMock()

    engine = ConsciousEngine(**mock_deps, routine_store=routine_store)

    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    await engine.check_routine_suggestions(now=now, notifier=notifier)

    notifier.publish.assert_awaited_once()
    call_kwargs = notifier.publish.await_args.kwargs
    assert call_kwargs["source"] == "librarian"
    assert "evening_dim" in call_kwargs["body"].lower() or "dim" in call_kwargs["body"].lower()


@pytest.mark.asyncio
async def test_check_routine_suggestions_respects_cooldown(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """check_routine_suggestions should skip routines within cooldown."""
    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    recent = now - _dt.timedelta(hours=2)
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [
        _make_routine(trigger_pattern="20:00 daily", last_suggested=recent)
    ]

    notifier = AsyncMock()
    engine = ConsciousEngine(**mock_deps, routine_store=routine_store)

    await engine.check_routine_suggestions(now=now, notifier=notifier)

    notifier.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_routine_suggestions_no_routines(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """check_routine_suggestions should be a no-op without a routine store."""
    engine = ConsciousEngine(**mock_deps)
    notifier = AsyncMock()
    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    await engine.check_routine_suggestions(now=now, notifier=notifier)
    notifier.publish.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/conscious/test_engine.py::test_check_routine_suggestions_publishes_notification -v`
Expected: FAIL — `check_routine_suggestions` doesn't exist.

- [ ] **Step 3: Implement `check_routine_suggestions`**

In `core/conscious/engine.py`, add this method to `ConsciousEngine` (after `_build_routine_hint`, around line 502):

```python
    async def check_routine_suggestions(
        self,
        now: datetime | None = None,
        notifier: Any = None,
    ) -> None:
        """Check candidate routines and publish proactive notifications for matches.

        Called periodically from the conscious process background loop.
        Uses the same time-matching and cooldown logic as _build_routine_hint.
        """
        if self._routines is None or notifier is None:
            return

        from core.memory.routines.patterns import match_trigger_pattern
        from core.notifications.schema import Urgency

        if now is None:
            now = datetime.now(UTC)

        candidates = self._routines.list_by_state("candidate")

        for routine in candidates:
            if routine.last_suggested is not None:
                hours_since = (now - routine.last_suggested).total_seconds() / 3600
                if hours_since < self._ROUTINE_SUGGESTION_COOLDOWN_HOURS:
                    continue

            if not match_trigger_pattern(routine.trigger_pattern, now):
                continue

            steps_str = "; ".join(s.description for s in routine.steps) if routine.steps else ""
            body = (
                f"I've noticed you usually {steps_str.lower()} around "
                f"{routine.trigger_pattern}. Want me to start doing this automatically?"
            )

            await notifier.publish(
                title="Routine Suggestion",
                body=body,
                source="librarian",
                urgency=Urgency.INFORMATIONAL,
            )

            updated = routine.model_copy(update={"last_suggested": now})
            self._routines.save(updated)
            logger.info("Proactive routine suggestion published: '%s'", routine.name)
```

Add the `Any` import at the top if needed (it should already be there from `from typing import TYPE_CHECKING, Any, ClassVar`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/conscious/test_engine.py -k "check_routine" -v`
Expected: ALL PASS.

- [ ] **Step 5: Wire into conscious process background loop**

In `core/conscious/__main__.py`, add a background task after the librarian task setup (around line 293). Add after `internal_actions_task = asyncio.create_task(...)`:

```python
    # Start proactive routine suggestion checker (every 15 minutes)
    async def _routine_suggestion_loop() -> None:
        while not _shutdown.is_set():
            try:
                await engine.check_routine_suggestions(notifier=notifier)
            except Exception as exc:
                log.error("Routine suggestion check failed: {}", exc)
            # Sleep 15 minutes (in 5s increments to respect shutdown)
            for _ in range(180):
                if _shutdown.is_set():
                    return
                await asyncio.sleep(5)

    routine_suggestion_task = asyncio.create_task(_routine_suggestion_loop())
```

In the `finally` block (line 354-360), add the cancellation:
```python
        routine_suggestion_task.cancel()
```

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -x -q`
Expected: ALL PASS.

- [ ] **Step 7: Lint and type check**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && ruff check . --fix && ruff format . && mypy --strict core/conscious/engine.py core/conscious/__main__.py`

- [ ] **Step 8: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add core/conscious/engine.py core/conscious/__main__.py tests/core/conscious/test_engine.py
git commit -m "feat(d3): add proactive routine suggestion via notifications"
```

---

### Task 8: Add confidence decay on ignored suggestions

**Files:**
- Modify: `core/librarian/consolidator.py` (`_update_routine_lifecycle`)
- Test: `tests/core/librarian/test_consolidator_v2.py`

- [ ] **Step 1: Write failing test for confidence decay**

In `tests/core/librarian/test_consolidator_v2.py`, add:

```python
@pytest.mark.asyncio
async def test_lifecycle_suggested_but_ignored_decays_confidence() -> None:
    """Candidate that was suggested but not accepted should lose confidence."""
    import datetime as dt
    from unittest.mock import MagicMock

    from core.memory.schemas import RoutineSpec

    # Routine was suggested 25 hours ago (past cooldown) but no acceptance
    now = dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=dt.UTC)
    suggested = now - dt.timedelta(hours=25)

    routine = RoutineSpec(
        name="ignored_routine",
        trigger_pattern="20:00 daily",
        steps=[],
        confidence=0.5,
        learned_from=["ep-1"],
        state="candidate",
        last_suggested=suggested,
    )

    routine_store = MagicMock()
    routine_store.list_all.return_value = [routine]

    context_index = AsyncMock()
    context_index._store = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()

    librarian = _make_librarian(context_index=context_index)
    librarian._routines = routine_store

    with patch("core.librarian.consolidator.datetime") as mock_dt:
        mock_dt.now.return_value = now
        mock_dt.UTC = dt.UTC
        mock_dt.timedelta = dt.timedelta

        await librarian._update_routine_lifecycle()

    saved = routine_store.save.call_args[0][0]
    assert saved.confidence == pytest.approx(0.45)  # 0.5 - 0.05


@pytest.mark.asyncio
async def test_lifecycle_confidence_below_threshold_archives() -> None:
    """Candidate with confidence below threshold should be archived."""
    import datetime as dt
    from unittest.mock import MagicMock

    from core.memory.schemas import RoutineSpec

    now = dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=dt.UTC)
    suggested = now - dt.timedelta(hours=25)

    routine = RoutineSpec(
        name="dying_routine",
        trigger_pattern="20:00 daily",
        steps=[],
        confidence=0.28,  # Below 0.3 threshold after decay
        learned_from=["ep-1"],
        state="candidate",
        last_suggested=suggested,
    )

    routine_store = MagicMock()
    routine_store.list_all.return_value = [routine]

    context_index = AsyncMock()
    context_index._store = AsyncMock()
    context_index._store.delete = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()

    librarian = _make_librarian(context_index=context_index)
    librarian._routines = routine_store

    with patch("core.librarian.consolidator.datetime") as mock_dt:
        mock_dt.now.return_value = now
        mock_dt.UTC = dt.UTC
        mock_dt.timedelta = dt.timedelta

        await librarian._update_routine_lifecycle()

    saved = routine_store.save.call_args[0][0]
    assert saved.state == "archived"
    context_index._store.delete.assert_awaited_once_with("dying_routine")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/librarian/test_consolidator_v2.py::test_lifecycle_suggested_but_ignored_decays_confidence -v`
Expected: FAIL — confidence is not decayed.

- [ ] **Step 3: Implement confidence decay in `_update_routine_lifecycle`**

In `core/librarian/consolidator.py`, in `_update_routine_lifecycle`, after the hit/miss logic for candidate/active routines (around line 678), add confidence decay check. Insert before the final `return updated`:

In the block that handles candidate/active routines (the `else` branch where pattern did NOT fire, around line 662-679), after updating `consecutive_misses` and `state`, add confidence decay logic:

After the existing miss handling (lines 662-679), and before the `return updated` line, add a new section for suggestion-ignored decay. Restructure the candidate/active block to also check `last_suggested`:

Replace the entire `# For candidate/active: check...` block (lines 648-679) with:

```python
            # For candidate/active: check if trigger_pattern matches recent activity
            pattern_fired = self._check_pattern_fired(routine, now)

            if pattern_fired:
                routine = routine.model_copy(
                    update={
                        "last_hit": now,
                        "consecutive_misses": 0,
                    }
                )
                self._routines.save(routine)
                updated += 1
                logger.debug("Routine '%s' hit", routine.name)
            else:
                new_misses = routine.consecutive_misses + 1
                new_state = routine.state
                new_confidence = routine.confidence

                # Confidence decay: if suggested but ignored (past cooldown, no acceptance)
                if (
                    routine.state == "candidate"
                    and routine.last_suggested is not None
                    and (now - routine.last_suggested).total_seconds() / 3600
                    >= self._routine_suggestion_cooldown_hours
                ):
                    new_confidence -= self._routine_decay_per_cycle

                if new_misses >= 3:
                    new_state = "dormant"
                    logger.info(
                        "Routine '%s' transitioned to dormant (%d consecutive misses)",
                        routine.name,
                        new_misses,
                    )

                # Archive if confidence drops below threshold
                if new_confidence < self._routine_archive_threshold:
                    new_state = "archived"
                    logger.info(
                        "Routine '%s' archived (confidence=%.2f below threshold %.2f)",
                        routine.name,
                        new_confidence,
                        self._routine_archive_threshold,
                    )

                routine = routine.model_copy(
                    update={
                        "consecutive_misses": new_misses,
                        "state": new_state,
                        "confidence": new_confidence,
                    }
                )
                self._routines.save(routine)
                updated += 1

                # Remove archived routines from context index
                if new_state == "archived":
                    try:
                        await self._context_index._store.delete(routine.name)
                    except Exception as exc:
                        logger.warning(
                            "Failed to remove archived routine '%s' from index: %s",
                            routine.name,
                            exc,
                        )
```

Note: This replaces the archive removal code added in Task 5 for the dormant→archived path. Make sure the dormant→archived path (around line 637-646) also includes the index removal.

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/librarian/test_consolidator_v2.py -k "lifecycle" -v`
Expected: ALL PASS.

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -x -q`
Expected: ALL PASS.

- [ ] **Step 6: Lint and type check**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && ruff check . --fix && ruff format . && mypy --strict core/librarian/consolidator.py`

- [ ] **Step 7: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add core/librarian/consolidator.py tests/core/librarian/test_consolidator_v2.py
git commit -m "feat(d3): add confidence decay on ignored suggestions, archive at threshold"
```

---

### Task 9: Create backlog and QA items

**Files:**
- Create: `docs/backlog/medium/actionable-notification-responses.md`
- Create: `docs/backlog/high/apns-credential-setup.md`
- Create: `docs/qa-backlog/routine-suggestion-push-notification-ios.md`
- Create: `docs/qa-backlog/routine-suggestion-tap-to-respond.md`
- Create: `docs/qa-backlog/notification-delivery-backgrounded-ios.md`
- Create: `docs/qa-backlog/dnd-respects-ios-notifications.md`

- [ ] **Step 1: Create backlog — actionable notification responses**

Create `docs/backlog/medium/actionable-notification-responses.md`:

```markdown
# Actionable Notification Responses

## Summary
Add accept/reject inline actions to routine suggestion notifications so users can respond directly from push notifications without opening the app.

## Context
Currently, routine suggestions via notifications are text-only. Users must open the app and respond in chat to accept/reject. Actionable notifications (Signal reply, WebSocket action buttons, APNs interactive notifications) would reduce friction.

## Acceptance Criteria
- Signal: reply with "yes"/"no" to accept/reject a routine suggestion
- WebSocket: action buttons in the notification card
- APNs: UNNotificationAction categories with "Accept" and "Reject" buttons
- Backend: new endpoint or stream handler to process accept/reject responses
- Routine state updated accordingly (active/archived)
```

- [ ] **Step 2: Create backlog — APNs credential setup**

Create `docs/backlog/high/apns-credential-setup.md`:

```markdown
# APNs Credential Setup and E2E Testing

## Summary
Configure Apple Push Notification service credentials in the Secrets Manager and validate the full notification delivery path from Alfred to iOS device.

## Context
APNs adapter code is implemented (PR #16) but actual Apple Push certificates/keys have not been configured. The adapter needs real credentials to deliver push notifications. Sandbox vs production environment must be validated.

## Acceptance Criteria
- APNs key (p8 format) stored in Secrets Manager under service "apns"
- team_id, key_id, bundle_id configured
- Sandbox environment tested with TestFlight build
- Production environment validated
- Device token registration verified end-to-end
- Push notification delivered to real iOS device
```

- [ ] **Step 3: Create QA items**

Create `docs/qa-backlog/routine-suggestion-push-notification-ios.md`:

```markdown
# Routine Suggestion Push Notification — iOS

**Feature:** D3 Pattern Detection + Proactive Notifications
**Priority:** high
**Type:** e2e

## Prerequisites
- Alfred server running with Conscious Engine
- APNs credentials configured in Secrets Manager
- Real iOS device with Alfred app installed
- At least one candidate routine detected by Librarian

## Test Steps
1. Ensure a candidate routine exists (e.g., `evening_dim` with trigger_pattern `20:00 daily`)
2. Wait for the proactive suggestion check to fire (every 15 minutes) during the routine's time window
3. Observe the iOS device for a push notification

## Expected Result
- Push notification appears with title "Routine Suggestion"
- Body contains the routine description and asks if the user wants to automate it
- Notification respects DND settings

## Notes
- APNs must be configured before this test can run
- If no candidate routines exist, manually create one via the RoutineStore
```

Create `docs/qa-backlog/routine-suggestion-tap-to-respond.md`:

```markdown
# Routine Suggestion — Tap to Respond

**Feature:** D3 Pattern Detection
**Priority:** high
**Type:** functional

## Prerequisites
- Routine suggestion push notification received on iOS
- Alfred server running

## Test Steps
1. Receive a routine suggestion push notification
2. Tap the notification to open the Alfred app
3. In the chat, respond with "Yes, automate that" or similar acceptance
4. Verify Alfred acknowledges and creates a trigger

## Expected Result
- App opens to chat view
- Conscious Engine processes the acceptance
- A trigger is created via TriggerFeature matching the routine's pattern
- Routine state transitions to "active"

## Notes
- Until actionable notifications are implemented (backlog item), this is the only way to respond
```

Create `docs/qa-backlog/notification-delivery-backgrounded-ios.md`:

```markdown
# Notification Delivery — Backgrounded iOS App

**Feature:** iOS Notifications
**Priority:** medium
**Type:** functional

## Prerequisites
- APNs credentials configured
- Real iOS device with Alfred app installed and backgrounded/killed

## Test Steps
1. Background or kill the Alfred iOS app
2. Trigger a notification from Alfred server (any urgency)
3. Check the iOS notification center

## Expected Result
- Push notification appears in notification center even when app is backgrounded/killed
- Tapping notification opens the app

## Notes
- APNs delivery is independent of WebSocket connection
- Device token must be registered via POST /api/devices/register
```

Create `docs/qa-backlog/dnd-respects-ios-notifications.md`:

```markdown
# DND Respects iOS Notifications

**Feature:** Notifications + DND
**Priority:** medium
**Type:** functional

## Prerequisites
- Alfred server running with DND enabled (manual or calendar-based)
- APNs configured, iOS device registered

## Test Steps
1. Enable DND in Alfred (via Redis key or during a calendar meeting)
2. Trigger a NORMAL or INFORMATIONAL notification
3. Check that no push notification is delivered
4. Disable DND
5. Verify deferred notifications are drained and delivered

## Expected Result
- No notification during DND
- Deferred notifications delivered after DND ends
- URGENT notifications bypass DND (if applicable)

## Notes
- DND is Alfred-level, not iOS-level — the test validates server-side deferral
```

- [ ] **Step 4: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add docs/backlog/medium/actionable-notification-responses.md docs/backlog/high/apns-credential-setup.md docs/qa-backlog/routine-suggestion-push-notification-ios.md docs/qa-backlog/routine-suggestion-tap-to-respond.md docs/qa-backlog/notification-delivery-backgrounded-ios.md docs/qa-backlog/dnd-respects-ios-notifications.md
git commit -m "docs(d3): add backlog and QA items for routine notifications and APNs"
```

---

### Task 10: Update stale backlog tickets

**Files:**
- Modify: `docs/backlog/medium/d3-librarian-pattern-detection.md`
- Modify: `docs/backlog/medium/d4-librarian-decay-processing.md`

- [ ] **Step 1: Update D3 backlog ticket**

Replace `docs/backlog/medium/d3-librarian-pattern-detection.md` with:

```markdown
# D3: Librarian Pattern Detection — COMPLETED

## Summary
Detect repeated patterns in episodic memory and promote to procedural memory with full lifecycle.

## Status
Implemented in D3+D4 PR. See spec: `docs/superpowers/specs/2026-04-16-d3-d4-pattern-detection-decay-design.md`

## What Was Built
- Pattern detection via LLM (already existed from PR #15)
- Routine indexing in `idx:context` for involuntary recall
- Suggestion flow: conversation hints + proactive notifications
- Confidence decay on ignored suggestions
- Archive removes from context index
- Trigger Engine promotion for crystallized execution
```

- [ ] **Step 2: Update D4 backlog ticket**

Replace `docs/backlog/medium/d4-librarian-decay-processing.md` with:

```markdown
# D4: Librarian Decay Processing — COMPLETED

## Summary
Contextual decay with upgraded formula and compression at cold migration.

## Status
Implemented in D3+D4 PR. See spec: `docs/superpowers/specs/2026-04-16-d3-d4-pattern-detection-decay-design.md`

## What Was Built
- Retrieval stats persistence (retrieval_count + last_retrieved written back to hot store)
- Upgraded subtractive decay formula (age vs significance + recency + frequency)
- Compression at cold migration (entity+date grouping, LLM summarization)
- Fallback for pre-stats-fix entries (last_retrieved=0 → no recency protection)
```

- [ ] **Step 3: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add docs/backlog/medium/d3-librarian-pattern-detection.md docs/backlog/medium/d4-librarian-decay-processing.md
git commit -m "docs: mark D3 and D4 backlog tickets as completed"
```

---

### Task 11: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add D3+D4 entries to CLAUDE.md**

Add to the Gotchas section in `CLAUDE.md`:

```markdown
- `_group_by_entity_date()` is a module-level function in `consolidator.py` — used by `_apply_decay()` for compression grouping
- Decay formula is subtractive: `age_factor - significance*2 - recency*1.5 - frequency*1.0` — high values RESIST migration (negative pressure = stays in hot)
- `EpisodicMemory.recall()` persists retrieval stats to hot store — each recall triggers HSET on Redis (retrieval_count + last_retrieved)
- Routines are indexed in `idx:context` on detection and removed on archive — search via `type="routine"` filter
- Proactive routine suggestions run every 15 minutes in the conscious process background loop
- Compression at cold migration groups by entity+date — summary goes to cold, originals marked `compressed="yes"` and `compressed_into=<summary_id>`
```

- [ ] **Step 2: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with D3+D4 gotchas and patterns"
```

---

### Task 12: Code architect review

- [ ] **Step 1: Run code architect review**

Use the `feature-dev:code-architect` agent to review all changes made in Tasks 1-11 against the spec and architecture rules.

- [ ] **Step 2: Fix all issues identified**

Address every issue raised by the code architect.

- [ ] **Step 3: Run full test suite after fixes**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -x -q`
Expected: ALL PASS.

- [ ] **Step 4: Commit fixes**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add -A
git commit -m "fix(d3+d4): address code architect review findings"
```

---

### Task 13: Simplify and refine

- [ ] **Step 1: Run /simplify skill**

Use the `simplify` skill to review all changed code for reuse, quality, and efficiency.

- [ ] **Step 2: Fix all issues identified**

Address every issue from the simplify review.

- [ ] **Step 3: Run full test suite after fixes**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -x -q`
Expected: ALL PASS.

- [ ] **Step 4: Commit fixes**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add -A
git commit -m "refactor(d3+d4): simplify and refine per review"
```

---

### Task 14: Final verification and lint

- [ ] **Step 1: Run full lint + type check**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && ruff check . --fix && ruff format . && mypy --strict bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
Expected: No errors.

- [ ] **Step 2: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -x -q`
Expected: ALL PASS (should be 800+ tests now).

- [ ] **Step 3: Commit any final lint fixes**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add -A
git commit -m "chore(d3+d4): final lint and format pass"
```

---

### Task 15: CLAUDE.md improvement

- [ ] **Step 1: Run claude-md-management:claude-md-improver skill**

Audit and fix any stale or missing CLAUDE.md content.

- [ ] **Step 2: Commit improvements**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add -A
git commit -m "docs: improve CLAUDE.md per audit"
```

---

### Task 16: QA backlog generation

- [ ] **Step 1: Run QA backlog generation sub-agent**

Use a `general-purpose` sub-agent to review the diff/changes made in this session, identify features that can't be fully verified by automated tests, and create files in `docs/qa-backlog/` following the QA Backlog convention.

Note: Some QA items were already created in Task 9 — the agent should check for duplicates and only create new items for features not already covered.

- [ ] **Step 2: Commit QA items**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add docs/qa-backlog/
git commit -m "qa(d3+d4): add manual testing backlog items"
```

---

### Task 17: Create PR

- [ ] **Step 1: Push branch and create PR**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git push -u origin HEAD
gh pr create --title "feat: D3+D4 pattern detection lifecycle and contextual decay" --body "$(cat <<'EOF'
## Summary
- Persist retrieval stats (count + last_retrieved) to hot store on recall
- Upgrade decay formula: subtractive model with age, significance, recency, frequency
- Add compression at cold migration: entity+date grouping with LLM summarization
- Index routines in idx:context on detection, remove on archive
- Enhance routine suggestion hints with steps and confidence
- Add proactive routine suggestions via notifications (15-min background loop)
- Add confidence decay on ignored suggestions, archive at threshold
- Create backlog items (actionable notifications, APNs setup) and QA items

## Test plan
- [ ] Run full test suite (800+ tests)
- [ ] Manual: verify decay formula with parametrized test cases
- [ ] Manual: verify routine suggestion appears in chat during matching time window
- [ ] QA backlog items for iOS notification testing (requires APNs setup)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
