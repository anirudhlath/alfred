"""Tests for SqliteVecStore."""

from __future__ import annotations

import struct
from unittest.mock import AsyncMock, patch

import pytest

from core.memory.sqlite_vec_store import SqliteVecStore, _pack
from core.memory.vector_store import ContextMetadata

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _meta(
    source: str = "conversation",
    significance: float = 0.5,
    timestamp: float = 1_711_000_000.0,
) -> ContextMetadata:
    return ContextMetadata(
        type="episodic",
        source=source,
        entities='["light.kitchen"]',
        timestamp=timestamp,
        significance=significance,
        retrieval_count=0,
    )


def _emb(val: float = 0.1, dim: int = 4) -> list[float]:
    """Return a dim-length embedding with first element = val."""
    base = [val] + [0.0] * (dim - 1)
    return base


# ---------------------------------------------------------------------------
# _pack helper
# ---------------------------------------------------------------------------


def test_pack_produces_float32_bytes() -> None:
    result = _pack([1.0, 2.0, 3.0, 4.0])
    unpacked = struct.unpack("<4f", result)
    assert list(unpacked) == pytest.approx([1.0, 2.0, 3.0, 4.0])


def test_pack_length() -> None:
    result = _pack([0.1, 0.2, 0.3, 0.4])
    assert len(result) == 16  # 4 floats * 4 bytes


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def store(tmp_path: object) -> SqliteVecStore:
    """SqliteVecStore with in-memory DB and sqlite-vec loaded."""
    s = SqliteVecStore(db_path=":memory:", dim=4)
    await s._ensure_schema()
    return s


@pytest.fixture
async def store_no_vec(tmp_path: object) -> SqliteVecStore:
    """SqliteVecStore without sqlite-vec extension (fallback mode)."""
    s = SqliteVecStore(db_path=":memory:", dim=4)
    # Force schema init but then disable vec
    with patch("sqlite_vec.loadable_path", side_effect=ImportError("no sqlite_vec")):
        await s._ensure_schema()
    # Manually mark vec as unavailable after schema init
    s._vec_ready = False
    return s


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schema_v2_creates_vec_tables(store: SqliteVecStore) -> None:
    """After ensure_schema the vec0 virtual tables must exist."""
    db = store._db
    assert db is not None
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'vec_%'"
    )
    table_names = {row[0] for row in await cursor.fetchall()}
    assert "vec_episodic_content" in table_names
    assert "vec_episodic_semantic" in table_names


@pytest.mark.asyncio
async def test_schema_v2_adds_significance_column(store: SqliteVecStore) -> None:
    db = store._db
    assert db is not None
    cursor = await db.execute("PRAGMA table_info(episodic_entries)")
    columns = {row[1] for row in await cursor.fetchall()}
    assert "significance" in columns
    assert "semantic_key" in columns
    assert "compressed_into" in columns


@pytest.mark.asyncio
async def test_schema_version_is_2(store: SqliteVecStore) -> None:
    db = store._db
    assert db is not None
    cursor = await db.execute("SELECT version FROM schema_version")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 2


# ---------------------------------------------------------------------------
# add / exists / count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_entry_exists(store: SqliteVecStore) -> None:
    await store.add(
        id="ep-1",
        content="the kitchen light turned on",
        semantic_key="light event involving light.kitchen",
        embedding_content=_emb(0.1),
        embedding_semantic=_emb(0.2),
        metadata=_meta(),
    )
    assert await store.exists("ep-1") is True


@pytest.mark.asyncio
async def test_add_nonexistent_entry(store: SqliteVecStore) -> None:
    assert await store.exists("ep-999") is False


@pytest.mark.asyncio
async def test_count_reflects_adds(store: SqliteVecStore) -> None:
    assert await store.count() == 0
    await store.add(
        id="ep-1",
        content="a",
        semantic_key="k1",
        embedding_content=_emb(),
        embedding_semantic=_emb(),
        metadata=_meta(),
    )
    await store.add(
        id="ep-2",
        content="b",
        semantic_key="k2",
        embedding_content=_emb(0.5),
        embedding_semantic=_emb(0.5),
        metadata=_meta(),
    )
    assert await store.count() == 2


@pytest.mark.asyncio
async def test_add_idempotent(store: SqliteVecStore) -> None:
    """INSERT OR REPLACE — adding same id twice keeps count at 1."""
    for _ in range(2):
        await store.add(
            id="ep-dup",
            content="same content",
            semantic_key="same key",
            embedding_content=_emb(),
            embedding_semantic=_emb(),
            metadata=_meta(),
        )
    assert await store.count() == 1


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_removes_entry(store: SqliteVecStore) -> None:
    await store.add(
        id="ep-del",
        content="to be deleted",
        semantic_key="key",
        embedding_content=_emb(),
        embedding_semantic=_emb(),
        metadata=_meta(),
    )
    assert await store.exists("ep-del") is True
    await store.delete("ep-del")
    assert await store.exists("ep-del") is False


@pytest.mark.asyncio
async def test_delete_removes_from_vec_tables(store: SqliteVecStore) -> None:
    """After delete the vec0 tables must also have no row for that rowid."""
    await store.add(
        id="ep-vec-del",
        content="vec delete test",
        semantic_key="key",
        embedding_content=_emb(0.3),
        embedding_semantic=_emb(0.4),
        metadata=_meta(),
    )
    db = store._db
    assert db is not None

    # Grab rowid before deletion
    cursor = await db.execute("SELECT rowid FROM episodic_entries WHERE id = ?", ("ep-vec-del",))
    row = await cursor.fetchone()
    assert row is not None
    rowid: int = row[0]

    await store.delete("ep-vec-del")

    cursor = await db.execute("SELECT rowid FROM vec_episodic_content WHERE rowid = ?", (rowid,))
    assert await cursor.fetchone() is None

    cursor = await db.execute("SELECT rowid FROM vec_episodic_semantic WHERE rowid = ?", (rowid,))
    assert await cursor.fetchone() is None


@pytest.mark.asyncio
async def test_delete_nonexistent_does_not_raise(store: SqliteVecStore) -> None:
    """Deleting a missing id is a no-op."""
    await store.delete("ep-missing")  # should not raise


# ---------------------------------------------------------------------------
# KNN search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_returns_results_sorted_by_score(store: SqliteVecStore) -> None:
    """Add two entries with different embeddings; query near one, expect it first."""
    # Use embeddings that are similar (not orthogonal) so both have positive similarity
    emb_a = [1.0, 0.0, 0.0, 0.0]
    emb_b = [0.8, 0.6, 0.0, 0.0]  # ~37° from emb_a → positive cosine similarity

    await store.add(
        id="ep-a",
        content="entry A",
        semantic_key="key A",
        embedding_content=emb_a,
        embedding_semantic=emb_a,
        metadata=_meta(),
    )
    await store.add(
        id="ep-b",
        content="entry B",
        semantic_key="key B",
        embedding_content=emb_b,
        embedding_semantic=emb_b,
        metadata=_meta(),
    )

    # Query near emb_a — ep-a should score higher; both have positive similarity
    results = await store.search(query_embedding=emb_a, limit=2, min_similarity=0.0)
    assert len(results) == 2
    assert results[0].id == "ep-a"
    assert results[0].score >= results[1].score


@pytest.mark.asyncio
async def test_search_min_similarity_filters_low_scores(store: SqliteVecStore) -> None:
    """Entries far from query should be excluded by min_similarity."""
    emb_near = [1.0, 0.0, 0.0, 0.0]
    emb_far = [0.0, 1.0, 0.0, 0.0]

    await store.add(
        id="ep-near",
        content="near",
        semantic_key="near key",
        embedding_content=emb_near,
        embedding_semantic=emb_near,
        metadata=_meta(),
    )
    await store.add(
        id="ep-far",
        content="far",
        semantic_key="far key",
        embedding_content=emb_far,
        embedding_semantic=emb_far,
        metadata=_meta(),
    )

    # cos(90°) = 0 → distance=1 → similarity=0; filter at 0.5 removes ep-far
    results = await store.search(query_embedding=emb_near, limit=10, min_similarity=0.5)
    ids = {r.id for r in results}
    assert "ep-near" in ids
    assert "ep-far" not in ids


@pytest.mark.asyncio
async def test_search_merges_content_and_semantic_by_max_score(
    store: SqliteVecStore,
) -> None:
    """Results from both vec0 tables are merged; max score per id is kept."""
    # Embed so content and semantic embeddings are different,
    # then confirm a single result is returned (not duplicated)
    emb_c = [1.0, 0.0, 0.0, 0.0]
    emb_s = [0.9, 0.1, 0.0, 0.0]

    await store.add(
        id="ep-merge",
        content="merge test",
        semantic_key="merge key",
        embedding_content=emb_c,
        embedding_semantic=emb_s,
        metadata=_meta(),
    )

    results = await store.search(query_embedding=emb_c, limit=5)
    ids = [r.id for r in results]
    # ep-merge must appear exactly once
    assert ids.count("ep-merge") == 1


@pytest.mark.asyncio
async def test_search_returns_correct_content_and_metadata(
    store: SqliteVecStore,
) -> None:
    meta = _meta(source="trigger", significance=0.8, timestamp=1_711_111_111.0)
    await store.add(
        id="ep-meta",
        content="lights off at bedtime",
        semantic_key="bedtime event",
        embedding_content=_emb(0.7),
        embedding_semantic=_emb(0.7),
        metadata=meta,
    )
    results = await store.search(query_embedding=_emb(0.7), limit=1)
    assert len(results) == 1
    r = results[0]
    assert r.id == "ep-meta"
    assert r.content == "lights off at bedtime"
    assert r.semantic_key == "bedtime event"
    assert r.metadata.source == "trigger"
    assert r.metadata.timestamp == pytest.approx(1_711_111_111.0)


@pytest.mark.asyncio
async def test_search_empty_store_returns_empty(store: SqliteVecStore) -> None:
    results = await store.search(query_embedding=_emb(), limit=5)
    assert results == []


@pytest.mark.asyncio
async def test_search_respects_limit(store: SqliteVecStore) -> None:
    for i in range(5):
        await store.add(
            id=f"ep-{i}",
            content=f"entry {i}",
            semantic_key=f"key {i}",
            embedding_content=_emb(float(i) * 0.1),
            embedding_semantic=_emb(float(i) * 0.1),
            metadata=_meta(),
        )
    results = await store.search(query_embedding=_emb(0.2), limit=2)
    assert len(results) <= 2


# ---------------------------------------------------------------------------
# Transactional writes (metadata + both vec0 tables)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_inserts_into_both_vec_tables(store: SqliteVecStore) -> None:
    """After add(), both vec0 tables must contain a row for the entry."""
    await store.add(
        id="ep-txn",
        content="transactional write test",
        semantic_key="txn key",
        embedding_content=_emb(0.3),
        embedding_semantic=_emb(0.4),
        metadata=_meta(),
    )
    db = store._db
    assert db is not None

    cursor = await db.execute("SELECT rowid FROM episodic_entries WHERE id = ?", ("ep-txn",))
    row = await cursor.fetchone()
    assert row is not None
    rowid: int = row[0]

    cursor = await db.execute("SELECT rowid FROM vec_episodic_content WHERE rowid = ?", (rowid,))
    assert await cursor.fetchone() is not None

    cursor = await db.execute("SELECT rowid FROM vec_episodic_semantic WHERE rowid = ?", (rowid,))
    assert await cursor.fetchone() is not None


# ---------------------------------------------------------------------------
# Rowid coordination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rowid_matches_between_tables(store: SqliteVecStore) -> None:
    """Rowids in episodic_entries and both vec0 tables must be consistent."""
    await store.add(
        id="ep-rowid",
        content="rowid coordination test",
        semantic_key="rowid key",
        embedding_content=[1.0, 0.0, 0.0, 0.0],
        embedding_semantic=[0.0, 1.0, 0.0, 0.0],
        metadata=_meta(),
    )
    db = store._db
    assert db is not None

    cursor = await db.execute("SELECT rowid FROM episodic_entries WHERE id = ?", ("ep-rowid",))
    row = await cursor.fetchone()
    assert row is not None
    main_rowid: int = row[0]

    for table in ("vec_episodic_content", "vec_episodic_semantic"):
        cursor = await db.execute(f"SELECT rowid FROM {table} WHERE rowid = ?", (main_rowid,))
        assert await cursor.fetchone() is not None, f"{table} missing rowid {main_rowid}"


# ---------------------------------------------------------------------------
# Schema migration from v1 to v2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_v1_to_v2_runs_without_embedder() -> None:
    """v2 migration should succeed even when no embedder is provided (no data rows)."""
    s = SqliteVecStore(db_path=":memory:", dim=4, embedder=None)
    await s._ensure_schema()
    assert s._db is not None
    cursor = await s._db.execute("SELECT version FROM schema_version")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 2
    await s.close()


@pytest.mark.asyncio
async def test_migration_v1_to_v2_backfills_existing_rows() -> None:
    """When v1 rows exist and embedder is provided, data migration embeds them."""
    mock_embedder = AsyncMock()
    mock_embedder.embed = AsyncMock(return_value=[0.1, 0.2, 0.3, 0.4])
    mock_embedder.dimension.return_value = 4

    # Bootstrap a v1 database manually
    import aiosqlite

    db = await aiosqlite.connect(":memory:")
    schema_v1 = (
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);\n"
        "INSERT OR IGNORE INTO schema_version (version) VALUES (1);\n"
        "CREATE TABLE IF NOT EXISTS episodic_entries (\n"
        "  id TEXT PRIMARY KEY, timestamp REAL NOT NULL, source TEXT NOT NULL,\n"
        "  summary TEXT NOT NULL, entities TEXT NOT NULL, valence TEXT NOT NULL,\n"
        "  embedding BLOB\n"
        ");\n"
        "CREATE INDEX IF NOT EXISTS idx_episodic_timestamp ON episodic_entries(timestamp);\n"
        "CREATE INDEX IF NOT EXISTS idx_episodic_source ON episodic_entries(source);\n"
    )
    await db.executescript(schema_v1)
    _legacy_row = (
        "ep-legacy",
        1_711_000_000.0,
        "conversation",
        "legacy entry",
        '["sensor.temp"]',
        "neutral",
    )
    await db.execute(
        "INSERT INTO episodic_entries(id, timestamp, source, summary, entities, valence)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        _legacy_row,
    )
    await db.commit()

    # Create SqliteVecStore that re-uses this in-memory DB
    # We can't easily pass an existing connection, so we test via an on-disk DB
    await db.close()

    # Use a tmp file-based DB to simulate v1 → v2 migration with existing data
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        # Write v1 schema + data
        db = await aiosqlite.connect(db_path)
        await db.executescript(schema_v1)
        await db.execute(
            "INSERT INTO episodic_entries(id, timestamp, source, summary, entities, valence)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            _legacy_row,
        )
        await db.commit()
        await db.close()

        # Now open via SqliteVecStore — should trigger v2 migration
        s = SqliteVecStore(db_path=db_path, dim=4, embedder=mock_embedder)
        await s._ensure_schema()

        assert s._db is not None
        cursor = await s._db.execute("SELECT version FROM schema_version")
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == 2

        # embed should have been called for the legacy entry (content + semantic)
        assert mock_embedder.embed.call_count >= 2

        # vec0 tables should have a row for the legacy entry
        cursor = await s._db.execute("SELECT rowid FROM episodic_entries WHERE id = 'ep-legacy'")
        rowid_row = await cursor.fetchone()
        assert rowid_row is not None
        rowid = rowid_row[0]

        cursor = await s._db.execute(
            "SELECT rowid FROM vec_episodic_content WHERE rowid = ?", (rowid,)
        )
        assert await cursor.fetchone() is not None

        await s.close()
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# Fallback mode (no sqlite-vec)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_search_returns_results(store_no_vec: SqliteVecStore) -> None:
    """Full-table scan fallback should still return entries."""
    # Need to add entries — but vec_ready is False so no vec0 tables exist.
    # We insert directly to bypass the vec0 writes.
    db = store_no_vec._db
    assert db is not None
    await db.execute(
        "INSERT INTO episodic_entries(id, timestamp, source, summary, entities, valence, embedding)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("ep-fallback", 1_711_000_000.0, "conversation", "fallback test", "[]", "neutral", b""),
    )
    await db.commit()

    results = await store_no_vec.search(query_embedding=_emb(), limit=5)
    assert len(results) == 1
    assert results[0].id == "ep-fallback"


@pytest.mark.asyncio
async def test_fallback_search_min_similarity_filters(store_no_vec: SqliteVecStore) -> None:
    """Fallback returns score=0.5; entries below min_similarity should be dropped."""
    db = store_no_vec._db
    assert db is not None
    await db.execute(
        "INSERT INTO episodic_entries(id, timestamp, source, summary, entities, valence, embedding)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("ep-fb2", 1_711_000_000.0, "conversation", "fallback 2", "[]", "neutral", b""),
    )
    await db.commit()

    # Fallback assigns score=0.5; min_similarity=0.6 should exclude it
    results = await store_no_vec.search(query_embedding=_emb(), limit=5, min_similarity=0.6)
    assert results == []


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_clears_db_reference() -> None:
    s = SqliteVecStore(db_path=":memory:", dim=4)
    await s._ensure_schema()
    assert s._db is not None
    await s.close()
    assert s._db is None
