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
