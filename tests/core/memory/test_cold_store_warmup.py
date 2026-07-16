"""Warmup ordering must not bypass cold-store schema creation.

A startup warmup step may open the SQLite connection before any real
read/write happens; schema creation/migration must still run on first use.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import aiosqlite
import pytest

from core.memory.sqlite_vec_store import SqliteVecStore

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_connect_before_use_still_creates_schema(tmp_path: Path) -> None:
    store = SqliteVecStore(db_path=str(tmp_path / "cold.db"), dim=8)

    await store._connect()  # what a warmup task does first

    assert await store.count() == 0  # must not raise "no such table"


@pytest.mark.asyncio
async def test_reensure_schema_keeps_single_version_row(tmp_path: Path) -> None:
    """Two processes share episodic_cold.db; each runs _ensure_schema at warmup.

    Re-running against an already-migrated DB must not re-insert version 1
    (which later collides on UPDATE with 'UNIQUE constraint failed:
    schema_version.version' — seen live 2026-07-15 when conscious and
    memory-ingestor warmed concurrently).
    """
    path = str(tmp_path / "cold.db")

    first = SqliteVecStore(db_path=path, dim=8)
    await first._ensure_schema()
    await first.close()

    second = SqliteVecStore(db_path=path, dim=8)  # second process, same file
    await second._ensure_schema()  # must not raise IntegrityError

    db = await second._get_db()
    cursor = await db.execute("SELECT COUNT(*), MAX(version) FROM schema_version")
    row = await cursor.fetchone()
    assert row is not None
    count, version = row
    assert (count, version) == (1, 2)
    await second.close()


@pytest.mark.asyncio
async def test_ensure_schema_on_migrated_db_needs_no_write_lock(tmp_path: Path) -> None:
    """On an already-migrated DB, _ensure_schema must be read-only.

    Multiple processes warm the shared episodic_cold.db at startup while the
    librarian may hold write transactions — a write-taking ensure produced
    'database is locked' warmup failures (seen live 2026-07-16).
    """
    path = str(tmp_path / "cold.db")

    first = SqliteVecStore(db_path=path, dim=8)
    await first._ensure_schema()
    await first.close()

    # Simulate another process holding the write lock (WAL readers don't block)
    blocker = await aiosqlite.connect(path)
    try:
        await blocker.execute("BEGIN IMMEDIATE")

        second = SqliteVecStore(db_path=path, dim=8)
        await asyncio.wait_for(second._ensure_schema(), timeout=2.0)
        assert second._schema_ready
        await second.close()
    finally:
        await blocker.rollback()
        await blocker.close()
