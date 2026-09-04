"""Tests for SqliteVecStore."""

from __future__ import annotations

import logging
import os
import sqlite3
import struct
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
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


# ---------------------------------------------------------------------------
# Dimension guard tests
#
# These run against a real sqlite-vec database rather than a stub: the width the
# guard reads comes out of sqlite_master's stored DDL, and a hand-written DDL
# string would pass even if vec0 recorded something else entirely.
# ---------------------------------------------------------------------------


def _vec_sql(db_path: str) -> dict[str, str]:
    """The stored DDL of both vec0 tables, keyed by table name."""
    raw = sqlite3.connect(db_path)
    try:
        rows = raw.execute(
            "SELECT name, sql FROM sqlite_master WHERE name IN"
            " ('vec_episodic_content', 'vec_episodic_semantic')"
        ).fetchall()
    finally:
        raw.close()
    return {name: sql for name, sql in rows}


def _loadable_path() -> str:
    """sqlite-vec's extension path, imported lazily.

    sqlite-vec ships in the ``memory`` extra rather than the core dependencies, so a
    module-level import would turn "these tests skip" into "this module fails to
    collect" wherever the extra is absent — which would quietly falsify the
    ``pytest.skip`` guards below.
    """
    import sqlite_vec

    path: str = sqlite_vec.loadable_path()
    return path


def _setup_on_file(db_path: str, script: str) -> None:
    """Arrange test state on the file directly. Setup only — not the operator path.

    This loads the extension in-process, which an operator at a stock ``sqlite3``
    prompt does not get for free. Anything claiming to prove a *recovery* must go
    through ``_run_prescribed_recovery`` instead.
    """
    raw = sqlite3.connect(db_path)
    try:
        raw.enable_load_extension(True)
        raw.load_extension(_loadable_path())
        raw.enable_load_extension(False)
        raw.executescript(script)
        raw.commit()
    finally:
        raw.close()


def _prescribed_command(message: str) -> str:
    """The shell command the error message tells the operator to run, verbatim.

    Deliberately a `python -c` line and not a `sqlite3` one: neither real
    environment can run the CLI form. The host has /usr/bin/sqlite3 but no
    sqlite_vec module; Alfred's image has the module but ships no sqlite3 binary.
    Only a dev venv has both, which is precisely where such guidance gets
    "verified" while stranding every actual operator.
    """
    lines = [ln.strip() for ln in message.split("\n") if ln.strip().startswith("python -c ")]
    assert len(lines) == 1, f"expected exactly one python -c command, got {lines!r}"
    return lines[0]


def _run_in_shell(command: str) -> subprocess.CompletedProcess[str]:
    """Run a command through a real shell, with this interpreter first on PATH.

    The prescription asks for "an interpreter that has Alfred's dependencies"; in
    the test environment that is the venv running pytest, not the bare `python`.
    """
    env = dict(os.environ)
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env['PATH']}"
    return subprocess.run(command, shell=True, capture_output=True, text=True, env=env, check=False)


def _run_prescribed_recovery(message: str) -> subprocess.CompletedProcess[str]:
    """Run the message's own command, unmodified, as an operator would.

    Nothing is pre-loaded here: the whole point is that ``DROP TABLE`` on a vec0
    table needs the extension, and guidance that only works with it already loaded
    strands the operator.
    """
    return _run_in_shell(_prescribed_command(message))


async def _cold_store_at(db_path: str, dim: int) -> bool:
    """Build a cold store at ``dim``; return whether vec0 was actually available."""
    store = SqliteVecStore(db_path, dim=dim)
    try:
        await store._ensure_schema()
        return store._vec_ready
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_schema_rejects_a_dimension_mismatch(tmp_path: Path) -> None:
    """An existing vec0 table at another width must fail loudly at schema time."""
    db_path = str(tmp_path / "cold.db")
    # _vec_ready is only set once _connect() has run, so it must be read from the
    # store that built the schema — reading it off a freshly constructed store
    # would always be False and skip this test unconditionally.
    if not await _cold_store_at(db_path, 384):
        pytest.skip("sqlite-vec extension unavailable — nothing to guard")
    assert "float[384]" in _vec_sql(db_path)["vec_episodic_content"]

    reopened = SqliteVecStore(db_path, dim=1024)
    try:
        with pytest.raises(RuntimeError, match="dim=384") as excinfo:
            await reopened._ensure_schema()
        assert reopened._schema_ready is False
    finally:
        await reopened.close()

    message = str(excinfo.value)
    # Ordered, so swapping the two widths in the message fails here: the table is
    # the one at 384, the configured model is the one at 1024.
    assert "vec_episodic_content built with dim=384" in message
    assert "produces dim=1024" in message


@pytest.mark.asyncio
async def test_schema_accepts_a_matching_dimension(tmp_path: Path) -> None:
    db_path = str(tmp_path / "cold.db")
    await _cold_store_at(db_path, 384)

    reopened = SqliteVecStore(db_path, dim=384)
    try:
        await reopened._ensure_schema()
        assert reopened._schema_ready is True
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_dimension_mismatch_recovery_message_is_one_an_operator_can_run(
    tmp_path: Path,
) -> None:
    """The prescribed command must run as written, in a shell, and actually fix it.

    ``_migrate_v2``'s back-fill re-embeds existing rows only when the store was
    constructed with an embedder, and no service constructs it with one, so the
    rebuilt tables come up empty. Promising a re-embed here would be a lie.

    Executed through a real shell rather than a pre-loaded connection: a recovery
    that only works with sqlite-vec already loaded is one the operator — who has
    just stopped Alfred — cannot perform.
    """
    db_path = str(tmp_path / "cold.db")
    if not await _cold_store_at(db_path, 384):
        pytest.skip("sqlite-vec extension unavailable — nothing to guard")

    reopened = SqliteVecStore(db_path, dim=1024)
    try:
        with pytest.raises(RuntimeError) as excinfo:
            await reopened._ensure_schema()
    finally:
        await reopened.close()
    message = str(excinfo.value)

    assert "DROP TABLE vec_episodic_content" in message
    assert "DROP TABLE vec_episodic_semantic" in message
    assert "UPDATE schema_version SET version = 1" in message
    assert "EMPTY" in message
    assert "Do NOT delete the sqlite file" in message
    # The blast radius is the whole recall, not the cold half — say so.
    assert "not just its cold half" in message
    assert "503" in message
    # It must tell the operator to load the extension, since DROP TABLE on a vec0
    # table cannot be parsed without it.
    assert "sqlite_vec.load(db)" in message
    assert "no such module: vec0" in message
    # It must name *this* store's file rather than a placeholder: in the container
    # the real path is /data/episodic_cold.db, which no operator should have to guess.
    assert db_path in _prescribed_command(message)
    # It must say the recovery is safe against a running deployment *and why*, or the
    # operator stops Alfred and hits the no-sqlite-vec-on-the-host wall instead.
    assert "You do NOT need to stop Alfred first" in message
    assert "already raises before it touches" in message
    assert "The restart is not optional" in message
    assert "conscious, memory-ingestor, librarian, channels/admin" in message
    # Both stores were built at the old width, so one fix is never the whole job.
    assert "Expect to do this twice" in message

    result = _run_prescribed_recovery(message)
    assert result.returncode == 0, f"prescribed recovery failed: {result.stderr}"
    assert _vec_sql(db_path) == {}, "recovery should have dropped both vec0 tables"

    recovered = SqliteVecStore(db_path, dim=1024)
    try:
        await recovered._ensure_schema()
        assert recovered._schema_ready is True
    finally:
        await recovered.close()
    assert "float[1024]" in _vec_sql(db_path)["vec_episodic_content"]
    assert "float[1024]" in _vec_sql(db_path)["vec_episodic_semantic"]


@pytest.mark.asyncio
async def test_the_recovery_needs_the_extension_load_it_prescribes(tmp_path: Path) -> None:
    """Pins *why* the command carries sqlite_vec.load: without it the drop dead-ends.

    The naive form is derived from the message's own command rather than hand-written,
    so it cannot drift away from what is actually prescribed. If this ever starts
    passing, the load step has become unnecessary and the message should lose it.
    """
    db_path = str(tmp_path / "cold.db")
    if not await _cold_store_at(db_path, 384):
        pytest.skip("sqlite-vec extension unavailable — nothing to guard")

    reopened = SqliteVecStore(db_path, dim=1024)
    try:
        with pytest.raises(RuntimeError) as excinfo:
            await reopened._ensure_schema()
    finally:
        await reopened.close()

    prescribed = _prescribed_command(str(excinfo.value))
    naive = prescribed.replace("db.enable_load_extension(True); sqlite_vec.load(db); ", "")
    assert naive != prescribed, "expected to strip the load step from the prescription"

    result = _run_in_shell(naive)
    assert result.returncode != 0
    assert "no such module: vec0" in result.stderr
    assert _vec_sql(db_path) != {}, "the failed attempt must not have dropped anything"


@pytest.mark.asyncio
async def test_dimension_mismatch_in_the_semantic_table_alone_is_caught(tmp_path: Path) -> None:
    """Both vec0 tables carry a width; checking only one leaves half the hole open."""
    db_path = str(tmp_path / "cold.db")
    if not await _cold_store_at(db_path, 384):
        pytest.skip("sqlite-vec extension unavailable — nothing to guard")
    # Leave only the semantic table behind, at the old width.
    _setup_on_file(db_path, "DROP TABLE vec_episodic_content;")

    reopened = SqliteVecStore(db_path, dim=1024)
    try:
        with pytest.raises(RuntimeError, match="vec_episodic_semantic built with dim=384"):
            await reopened._ensure_schema()
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_dimension_mismatch_is_caught_before_the_v2_migration_runs(tmp_path: Path) -> None:
    """A half-migrated database skips the fast path — the guard must still fire.

    ``CREATE VIRTUAL TABLE IF NOT EXISTS`` is a no-op against the old-width table,
    so running the migration first would commit version=2 over a database the guard
    is about to reject.
    """
    db_path = str(tmp_path / "cold.db")
    if not await _cold_store_at(db_path, 384):
        pytest.skip("sqlite-vec extension unavailable — nothing to guard")
    _setup_on_file(db_path, "UPDATE schema_version SET version = 1;")

    reopened = SqliteVecStore(db_path, dim=1024)
    try:
        with pytest.raises(RuntimeError, match="dim=384"):
            await reopened._ensure_schema()
        db = reopened._db
        assert db is not None
        cursor = await db.execute("SELECT MAX(version) FROM schema_version")
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == 1, "migration must not have run"
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_dimension_mismatch_is_latched(tmp_path: Path) -> None:
    """Once proven, the mismatch holds until restart — no per-operation re-probe.

    Pinned by fixing the database underneath a store that has already raised: a
    guard that re-read sqlite_master would now happily proceed.
    """
    db_path = str(tmp_path / "cold.db")
    if not await _cold_store_at(db_path, 384):
        pytest.skip("sqlite-vec extension unavailable — nothing to guard")

    reopened = SqliteVecStore(db_path, dim=1024)
    try:
        with pytest.raises(RuntimeError, match="dim=384"):
            await reopened._ensure_schema()
        _setup_on_file(
            db_path,
            "DROP TABLE vec_episodic_content;"
            " DROP TABLE vec_episodic_semantic;"
            " UPDATE schema_version SET version = 1;",
        )
        with pytest.raises(RuntimeError, match="dim=384"):
            await reopened._ensure_schema()
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_add_refuses_on_a_dimension_mismatch(tmp_path: Path) -> None:
    """The guard has to reach real callers, not just _ensure_schema()."""
    db_path = str(tmp_path / "cold.db")
    if not await _cold_store_at(db_path, 4):
        pytest.skip("sqlite-vec extension unavailable — nothing to guard")

    reopened = SqliteVecStore(db_path, dim=8)
    try:
        with pytest.raises(RuntimeError, match="dim=4"):
            await reopened.add(
                id="ep-mismatch",
                content="hello",
                semantic_key="key",
                embedding_content=_emb(dim=8),
                embedding_semantic=_emb(dim=8),
                metadata=_meta(),
            )
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_unreadable_vec_dimension_warns_instead_of_passing_silently(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A width the DDL does not expose must not disable the guard in silence."""
    db_path = str(tmp_path / "cold.db")
    if not await _cold_store_at(db_path, 384):
        pytest.skip("sqlite-vec extension unavailable — nothing to guard")
    _setup_on_file(
        db_path,
        "DROP TABLE vec_episodic_content;"
        " DROP TABLE vec_episodic_semantic;"
        " CREATE TABLE vec_episodic_content (rowid INTEGER PRIMARY KEY, embedding BLOB);"
        " CREATE TABLE vec_episodic_semantic (rowid INTEGER PRIMARY KEY, embedding BLOB);",
    )

    reopened = SqliteVecStore(db_path, dim=1024)
    try:
        with caplog.at_level(logging.WARNING, logger="core.memory.sqlite_vec_store"):
            await reopened._ensure_schema()
        assert reopened._schema_ready is True
        assert "dimension guard skipped" in caplog.text
    finally:
        await reopened.close()


@pytest.mark.parametrize(
    ("label", "ddl"),
    [
        ("uppercase", "vec0(embedding FLOAT[384])"),
        ("padded", "vec0(  embedding   float [ 384 ] )"),
        ("second_column", "vec0(other float[8], embedding float[384])"),
    ],
)
@pytest.mark.asyncio
async def test_dimension_is_read_from_ddl_spelling_variants(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    label: str,
    ddl: str,
) -> None:
    """vec0 stores its arguments verbatim, so the guard must read every legal form.

    Each of these creates a working 384-wide table. Asserted through a *mismatch*:
    a variant the regex cannot read falls into the warning branch and silently
    disables the guard, which a "matching width is accepted" test would not catch.
    ``second_column`` also pins the anchor — an unanchored search reads 8 here.
    """
    db_path = str(tmp_path / "cold.db")
    if not await _cold_store_at(db_path, 384):
        pytest.skip("sqlite-vec extension unavailable — nothing to guard")
    _setup_on_file(
        db_path,
        f"DROP TABLE vec_episodic_content; CREATE VIRTUAL TABLE vec_episodic_content USING {ddl};",
    )

    reopened = SqliteVecStore(db_path, dim=1024)
    try:
        with (
            caplog.at_level(logging.WARNING, logger="core.memory.sqlite_vec_store"),
            pytest.raises(RuntimeError, match="vec_episodic_content built with dim=384"),
        ):
            await reopened._ensure_schema()
        assert "dimension guard skipped" not in caplog.text
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_guard_is_skipped_when_the_vec_extension_is_unavailable(
    tmp_path: Path,
) -> None:
    """No vec0 means no vec0 constraint — a full-scan store must still open.

    Without the ``_vec_ready`` early return this hard-fails a store that works
    fine: ``search()`` falls back to ``_full_scan_search`` and never touches the
    vec0 tables, so their declared width is not load-bearing.
    """
    db_path = str(tmp_path / "cold.db")
    if not await _cold_store_at(db_path, 384):
        pytest.skip("sqlite-vec extension unavailable — nothing to guard")

    reopened = SqliteVecStore(db_path, dim=1024)
    # Mirror a build where the extension will not load: _connect() catches it, logs,
    # and leaves _vec_ready False, so the store degrades to sequential scan.
    with patch("sqlite_vec.loadable_path", side_effect=RuntimeError("no extension")):
        try:
            await reopened._ensure_schema()
            assert reopened._vec_ready is False
            assert reopened._schema_ready is True
            assert reopened._dim_mismatch is None
            # And it is genuinely usable: search falls back to a full scan.
            assert await reopened.search(query_embedding=_emb(dim=1024), limit=5) == []
        finally:
            await reopened.close()


@pytest.mark.asyncio
async def test_a_latched_mismatch_makes_every_store_operation_inert(tmp_path: Path) -> None:
    """The message tells the operator not to stop Alfred. This is why that is true.

    Every entry point routes through ``_get_db()``, so the latch stops the two
    operations that never touch vec0 (``exists``, ``count``) just as firmly as the
    vector ones. If any of these started succeeding, the file would no longer be
    quiescent and the "no need to stop Alfred" guidance would be unsafe.
    """
    db_path = str(tmp_path / "cold.db")
    if not await _cold_store_at(db_path, 4):
        pytest.skip("sqlite-vec extension unavailable — nothing to guard")

    store = SqliteVecStore(db_path, dim=8)
    try:
        with pytest.raises(RuntimeError, match="dim=4"):
            await store._ensure_schema()

        with pytest.raises(RuntimeError, match="dim=4"):
            await store.add(
                id="ep-1",
                content="c",
                semantic_key="k",
                embedding_content=_emb(dim=8),
                embedding_semantic=_emb(dim=8),
                metadata=_meta(),
            )
        with pytest.raises(RuntimeError, match="dim=4"):
            await store.search(query_embedding=_emb(dim=8), limit=5)
        with pytest.raises(RuntimeError, match="dim=4"):
            await store.delete("ep-1")
        # The two that read episodic_entries only — no vec0 involvement at all.
        with pytest.raises(RuntimeError, match="dim=4"):
            await store.exists("ep-1")
        with pytest.raises(RuntimeError, match="dim=4"):
            await store.count()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_recovery_succeeds_with_the_admin_reader_attached(tmp_path: Path) -> None:
    """Safe against a live deployment, including the one reader that bypasses the store.

    ``GET /memory/episodic`` without ``?q=`` opens the file itself with aiosqlite
    (``core/channels/admin_api.py:394``) instead of going through SqliteVecStore, so
    the latch does not stop it. It is read-only and never names the vec0 tables, so
    the drop must still succeed with it attached — and the entry must survive.
    """
    db_path = str(tmp_path / "cold.db")
    if not await _cold_store_at(db_path, 4):
        pytest.skip("sqlite-vec extension unavailable — nothing to guard")

    seeded = SqliteVecStore(db_path, dim=4)
    try:
        await seeded.add(
            id="ep-keep",
            content="keep me",
            semantic_key="k",
            embedding_content=_emb(dim=4),
            embedding_semantic=_emb(dim=4),
            metadata=_meta(),
        )
    finally:
        await seeded.close()

    mismatched = SqliteVecStore(db_path, dim=8)
    try:
        with pytest.raises(RuntimeError) as excinfo:
            await mismatched._ensure_schema()
    finally:
        await mismatched.close()

    # Hold the admin-style reader open across the recovery, exactly as that
    # endpoint would while an operator is watching the memory page.
    reader = await aiosqlite.connect(db_path)
    try:
        async with reader.execute("SELECT id FROM episodic_entries") as cur:
            assert [r[0] for r in await cur.fetchall()] == ["ep-keep"]

        result = _run_prescribed_recovery(str(excinfo.value))
        assert result.returncode == 0, f"recovery failed with a reader attached: {result.stderr}"

        async with reader.execute("SELECT id FROM episodic_entries") as cur:
            assert [r[0] for r in await cur.fetchall()] == ["ep-keep"]
    finally:
        await reader.close()

    restarted = SqliteVecStore(db_path, dim=8)
    try:
        assert await restarted.count() == 1
        assert "float[8]" in _vec_sql(db_path)["vec_episodic_content"]
    finally:
        await restarted.close()
