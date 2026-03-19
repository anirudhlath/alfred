"""EpisodicStore — hot (Redis Stream) + cold (SQLite) episodic memory."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
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
        await self._redis.xadd(EPISODIC_STREAM, data)  # type: ignore[arg-type]
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
        query = (
            f"SELECT id, timestamp, source, summary, entities, valence "
            f"FROM episodic_entries {where} ORDER BY timestamp DESC LIMIT ?"
        )
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
        if row is None:
            return None
        result: bytes = row[0]
        return result

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
