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
            id="ep-1",
            timestamp=datetime.now(UTC),
            source="conv",
            summary="weather",
            entities=["weather"],
            valence="neutral",
        ),
        EpisodicEntry(
            id="ep-2",
            timestamp=datetime.now(UTC),
            source="conv",
            summary="door",
            entities=["door"],
            valence="neutral",
        ),
    ]
    filtered = search.filter_by_entity(entries, "weather")
    assert len(filtered) == 1
    assert filtered[0].id == "ep-1"
