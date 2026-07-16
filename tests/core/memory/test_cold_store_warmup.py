"""Warmup ordering must not bypass cold-store schema creation.

A startup warmup step may open the SQLite connection before any real
read/write happens; schema creation/migration must still run on first use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.memory.sqlite_vec_store import SqliteVecStore

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_connect_before_use_still_creates_schema(tmp_path: Path) -> None:
    store = SqliteVecStore(db_path=str(tmp_path / "cold.db"), dim=8)

    await store._connect()  # what a warmup task does first

    assert await store.count() == 0  # must not raise "no such table"
