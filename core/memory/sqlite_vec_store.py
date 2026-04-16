"""SqliteVecStore — VectorStore implementation backed by SQLite + sqlite-vec."""

from __future__ import annotations

import json
import logging
import sqlite3
import struct
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

from core.memory.vector_store import ContextMetadata, SearchResult, VectorStore

if TYPE_CHECKING:
    from core.memory.embedding_provider import EmbeddingProvider

logger = logging.getLogger(__name__)

_SCHEMA_V1_PATH = Path(__file__).parent / "episodic" / "schema.sql"
_MIGRATION_V2_PATH = Path(__file__).parent / "episodic" / "migrations" / "v2.sql"

# Default significance JSON for data-migration of pre-v2 entries.
_DEFAULT_SIGNIFICANCE = (
    '{"overall": 0.3, "safety": 0.0, "novelty": 0.0,'
    ' "personal": 0.0, "emotional": 0.0, "source": "heuristic"}'
)


def _pack(embedding: list[float]) -> bytes:
    """Pack float list into raw little-endian float32 bytes for vec0 MATCH."""
    n = len(embedding)
    return struct.pack(f"<{n}f", *embedding)


class SqliteVecStore(VectorStore):
    """VectorStore backed by SQLite with sqlite-vec KNN search.

    Uses two vec0 virtual tables (``vec_episodic_content`` and
    ``vec_episodic_semantic``) that mirror rowids in ``episodic_entries``.
    ``search()`` queries both tables and merges results by max score per id.

    If sqlite-vec extension cannot be loaded the store degrades gracefully:
    ``add``/``delete``/``exists``/``count`` still work, but ``search`` falls
    back to a full-table sequential scan (slower but correct).

    Schema migration from v1 → v2 happens automatically on first connection
    via ``_ensure_schema()``.  If the database already contains rows the data
    migration step embeds each existing summary so the vec0 tables are
    consistent from the start.
    """

    def __init__(
        self,
        db_path: str | Path,
        dim: int = 768,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self._db_path = str(db_path)
        self._dim = dim
        self._embedder = embedder
        self._db: aiosqlite.Connection | None = None
        self._vec_ready: bool = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _connect(self) -> aiosqlite.Connection:
        """Open (or return cached) aiosqlite connection with vec0 loaded."""
        if self._db is not None:
            return self._db
        db = await aiosqlite.connect(self._db_path)
        await db.execute("PRAGMA journal_mode=WAL")
        # Attempt to load sqlite-vec extension
        try:
            import sqlite_vec

            await db.enable_load_extension(True)
            await db.load_extension(sqlite_vec.loadable_path())
            await db.enable_load_extension(False)
            self._vec_ready = True
            logger.debug("sqlite-vec extension loaded (dim=%d)", self._dim)
        except Exception as exc:
            logger.warning(
                "sqlite-vec unavailable — falling back to full scan: %s", exc, exc_info=True
            )
            self._vec_ready = False
        self._db = db
        return db

    async def _ensure_schema(self) -> None:
        """Run schema migrations up to v2 if needed."""
        db = await self._connect()

        # Ensure base v1 schema exists (idempotent CREATE IF NOT EXISTS)
        schema_v1 = _SCHEMA_V1_PATH.read_text()
        await db.executescript(schema_v1)
        await db.commit()

        # Check current version
        cursor = await db.execute("SELECT version FROM schema_version LIMIT 1")
        row = await cursor.fetchone()
        version: int = row[0] if row else 1

        if version < 2:
            await self._migrate_v2(db)

    async def _migrate_v2(self, db: aiosqlite.Connection) -> None:
        """Apply v2 migration DDL, then back-fill vec0 tables for existing rows."""
        # Check which columns already exist to make ALTER TABLE idempotent
        # (SQLite ALTER TABLE ADD COLUMN doesn't support IF NOT EXISTS)
        cursor = await db.execute("PRAGMA table_info(episodic_entries)")
        existing_cols = {row[1] for row in await cursor.fetchall()}

        v2_columns = {
            "significance": "TEXT DEFAULT '{}'",
            "semantic_key": "TEXT DEFAULT ''",
            "compressed_into": "TEXT DEFAULT NULL",
        }
        for col_name, col_def in v2_columns.items():
            if col_name not in existing_cols:
                await db.execute(
                    f"ALTER TABLE episodic_entries ADD COLUMN {col_name} {col_def}"
                )

        # vec0 virtual tables (only if extension loaded)
        if self._vec_ready:
            await db.executescript(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_episodic_semantic "
                f"USING vec0(embedding float[{self._dim}]);\n"
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_episodic_content "
                f"USING vec0(embedding float[{self._dim}]);"
            )

        await db.execute("UPDATE schema_version SET version = 2 WHERE version = 1")
        await db.commit()
        logger.info("Applied schema migration v1 → v2 (dim=%d)", self._dim)

        # Data migration: back-fill vec0 tables for existing entries
        if not self._vec_ready or self._embedder is None:
            return

        cursor = await db.execute("SELECT id, summary, entities, rowid FROM episodic_entries")
        rows = list(await cursor.fetchall())
        if not rows:
            return

        logger.info("Data migration: embedding %d existing episodic entries", len(rows))
        for row in rows:
            entry_id: str = row[0]
            summary: str = row[1]
            entities_raw: str = row[2]
            rowid: int = row[3]

            try:
                entities: list[str] = json.loads(entities_raw) if entities_raw else []
            except (json.JSONDecodeError, TypeError):
                entities = []

            # Content embedding from summary
            content_emb = await self._embedder.embed(summary)

            # Semantic key: template from source + entities
            if entities:
                semantic_key = f"episodic event involving {', '.join(entities)}"
            else:
                semantic_key = "episodic event"
            semantic_emb = await self._embedder.embed(semantic_key)

            content_bytes = _pack(content_emb)
            semantic_bytes = _pack(semantic_emb)

            await db.execute(
                "INSERT OR REPLACE INTO vec_episodic_content(rowid, embedding) VALUES (?, ?)",
                (rowid, content_bytes),
            )
            await db.execute(
                "INSERT OR REPLACE INTO vec_episodic_semantic(rowid, embedding) VALUES (?, ?)",
                (rowid, semantic_bytes),
            )

            # Also set default significance on the entry
            await db.execute(
                "UPDATE episodic_entries SET significance = ?, semantic_key = ? WHERE id = ?",
                (_DEFAULT_SIGNIFICANCE, semantic_key, entry_id),
            )

        await db.commit()
        logger.info("Data migration complete: %d entries embedded", len(rows))

    async def _get_db(self) -> aiosqlite.Connection:
        """Return initialized database connection, running migrations first."""
        if self._db is None:
            await self._ensure_schema()
        assert self._db is not None
        return self._db

    # ------------------------------------------------------------------
    # Rowid coordination helper
    # ------------------------------------------------------------------

    async def _rowid_for_id(self, db: aiosqlite.Connection, id: str) -> int | None:  # noqa: A002
        cursor = await db.execute("SELECT rowid FROM episodic_entries WHERE id = ?", (id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        result: int = row[0]
        return result

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
        db = await self._get_db()

        significance_json = json.dumps({"overall": metadata.significance})

        try:
            # Transactional write: metadata row + both vec0 tables in one commit
            await db.execute(
                """INSERT OR REPLACE INTO episodic_entries
                   (id, timestamp, source, summary, entities, valence, embedding,
                    significance, semantic_key, compressed_into)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    id,
                    metadata.timestamp,
                    metadata.source,
                    content,
                    metadata.entities,
                    "neutral",
                    b"",  # raw embedding blob not used by vec0 path
                    significance_json,
                    semantic_key,
                    metadata.compressed if metadata.compressed else None,
                ),
            )

            rowid = await self._rowid_for_id(db, id)
            if rowid is not None and self._vec_ready:
                content_bytes = _pack(embedding_content)
                semantic_bytes = _pack(embedding_semantic)
                await db.execute(
                    "INSERT OR REPLACE INTO vec_episodic_content(rowid, embedding) VALUES (?, ?)",
                    (rowid, content_bytes),
                )
                await db.execute(
                    "INSERT OR REPLACE INTO vec_episodic_semantic(rowid, embedding) VALUES (?, ?)",
                    (rowid, semantic_bytes),
                )

            await db.commit()
        except Exception:
            await db.rollback()
            raise

    async def search(
        self,
        query_embedding: list[float],
        limit: int,
        filters: dict[str, str | float | int] | None = None,
        min_similarity: float = 0.0,
    ) -> list[SearchResult]:
        db = await self._get_db()

        if self._vec_ready:
            return await self._knn_search(db, query_embedding, limit, min_similarity)
        else:
            return await self._full_scan_search(db, limit, min_similarity)

    async def _knn_search(
        self,
        db: aiosqlite.Connection,
        query_embedding: list[float],
        limit: int,
        min_similarity: float,
    ) -> list[SearchResult]:
        """KNN search via vec0 virtual tables, merging content + semantic results."""
        query_bytes = _pack(query_embedding)

        async def _query_table(table: str) -> list[tuple[int, float]]:
            try:
                cursor = await db.execute(
                    f"SELECT rowid, distance FROM {table}"
                    f" WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                    (query_bytes, limit),
                )
                rows = await cursor.fetchall()
                return [(int(r[0]), float(r[1])) for r in rows]
            except Exception as exc:
                logger.warning("KNN query on %s failed: %s", table, exc)
                return []

        content_hits, semantic_hits = (
            await _query_table("vec_episodic_content"),
            await _query_table("vec_episodic_semantic"),
        )

        # Merge: keep max similarity per rowid (vec0 distance = 0 identical, ≥0)
        rowid_score: dict[int, float] = {}
        for rowid, distance in (*content_hits, *semantic_hits):
            similarity = 1.0 - distance
            if similarity > rowid_score.get(rowid, -1.0):
                rowid_score[rowid] = similarity

        if not rowid_score:
            return []

        # Filter by min_similarity
        rowid_score = {r: s for r, s in rowid_score.items() if s >= min_similarity}
        if not rowid_score:
            return []

        # Fetch metadata for matched rowids
        placeholders = ",".join("?" * len(rowid_score))
        cursor = await db.execute(
            f"SELECT rowid, id, timestamp, source, summary, entities,"
            f" significance, semantic_key, compressed_into"
            f" FROM episodic_entries WHERE rowid IN ({placeholders})",
            list(rowid_score.keys()),
        )
        rows = list(await cursor.fetchall())

        results = [_row_to_search_result(row, rowid_score[int(row[0])]) for row in rows]
        return sorted(results, key=lambda r: r.score, reverse=True)[:limit]

    async def _full_scan_search(
        self,
        db: aiosqlite.Connection,
        limit: int,
        min_similarity: float,
    ) -> list[SearchResult]:
        """Fallback full-table scan when sqlite-vec is unavailable."""
        cursor = await db.execute(
            "SELECT rowid, id, timestamp, source, summary, entities,"
            " significance, semantic_key, compressed_into"
            " FROM episodic_entries ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = list(await cursor.fetchall())
        results = [_row_to_search_result(row, 0.5) for row in rows]
        return [r for r in results if r.score >= min_similarity]

    async def delete(self, id: str) -> None:  # noqa: A002
        db = await self._get_db()

        rowid = await self._rowid_for_id(db, id)

        try:
            await db.execute("DELETE FROM episodic_entries WHERE id = ?", (id,))
            if rowid is not None and self._vec_ready:
                await db.execute("DELETE FROM vec_episodic_content WHERE rowid = ?", (rowid,))
                await db.execute("DELETE FROM vec_episodic_semantic WHERE rowid = ?", (rowid,))
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    async def exists(self, id: str) -> bool:  # noqa: A002
        db = await self._get_db()
        cursor = await db.execute("SELECT 1 FROM episodic_entries WHERE id = ? LIMIT 1", (id,))
        row = await cursor.fetchone()
        return row is not None

    async def count(self) -> int:
        db = await self._get_db()
        cursor = await db.execute("SELECT COUNT(*) FROM episodic_entries")
        row = await cursor.fetchone()
        if row is None:
            return 0
        result: int = row[0]
        return result

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _row_to_search_result(row: sqlite3.Row, score: float) -> SearchResult:
    """Convert a DB row to a SearchResult.

    Row columns: (rowid, id, timestamp, source, summary, entities,
                  significance, semantic_key, compressed_into)
    """
    (
        _,
        id_,
        timestamp,
        source,
        summary,
        entities_raw,
        significance_raw,
        semantic_key,
        compressed_into,
    ) = row

    try:
        raw_sig = str(significance_raw) if significance_raw else ""
        significance_data: dict[str, object] = (
            json.loads(raw_sig) if raw_sig and raw_sig != "{}" else {}
        )
    except (json.JSONDecodeError, TypeError):
        significance_data = {}

    overall = float(str(significance_data.get("overall", "0.5")))

    compressed = str(compressed_into) if compressed_into else ""

    metadata = ContextMetadata(
        type="episodic",
        source=str(source),
        entities=str(entities_raw) if entities_raw else "",
        timestamp=float(str(timestamp)),
        significance=overall,
        retrieval_count=0,
        last_retrieved=0.0,
        compressed=compressed,
    )

    return SearchResult(
        id=str(id_),
        score=score,
        content=str(summary),
        semantic_key=str(semantic_key) if semantic_key else "",
        metadata=metadata,
    )
