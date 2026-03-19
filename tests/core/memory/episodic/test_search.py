"""Tests for episodic memory search."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.memory.episodic.search import EpisodicSearch
from core.memory.schemas import EpisodicEntry


def _make_entry(
    entry_id: str, summary: str, days_ago: int = 0, entities: list[str] | None = None
) -> EpisodicEntry:
    return EpisodicEntry(
        id=entry_id,
        timestamp=datetime.now(UTC) - timedelta(days=days_ago),
        source="conversation",
        summary=summary,
        entities=entities or [],
        valence="neutral",
    )


@pytest.fixture
def mock_store() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_embedder() -> MagicMock:
    embedder = MagicMock()
    embedder.embed.return_value = b"\x00" * 384 * 4
    embedder.cosine_similarity.return_value = 0.8
    return embedder


def test_filter_by_entity(mock_store: AsyncMock, mock_embedder: MagicMock) -> None:
    search = EpisodicSearch(store=mock_store, embedder=mock_embedder)
    entries = [
        _make_entry("ep-1", "weather query", entities=["weather"]),
        _make_entry("ep-2", "door sensor", entities=["door"]),
    ]
    filtered = search.filter_by_entity(entries, "weather")
    assert len(filtered) == 1
    assert filtered[0].id == "ep-1"


@pytest.mark.asyncio
async def test_search_cold_returns_ranked_results(
    mock_store: AsyncMock, mock_embedder: MagicMock
) -> None:
    """Verifies search_cold fetches candidates, scores them, and returns ranked."""
    candidates = [
        _make_entry("ep-1", "weather forecast", days_ago=0),
        _make_entry("ep-2", "front door alert", days_ago=5),
    ]
    mock_store.query_cold.return_value = candidates
    mock_store.get_cold_embedding.return_value = b"\x00" * 384 * 4
    # Make the embedder return different similarities for ranking
    mock_embedder.cosine_similarity.side_effect = [0.9, 0.3]

    search = EpisodicSearch(store=mock_store, embedder=mock_embedder)
    results = await search.search_cold("what was the weather?", limit=2)

    assert len(results) == 2
    # ep-1 should rank higher (0.9 similarity + recent)
    assert results[0].id == "ep-1"
    assert results[1].id == "ep-2"
    mock_embedder.embed.assert_called_once_with("what was the weather?")


@pytest.mark.asyncio
async def test_search_cold_skips_entry_without_embedding(
    mock_store: AsyncMock, mock_embedder: MagicMock
) -> None:
    """Entries with no embedding in cold storage are excluded from results."""
    candidates = [
        _make_entry("ep-1", "has embedding"),
        _make_entry("ep-2", "no embedding"),
    ]
    mock_store.query_cold.return_value = candidates
    # First entry has embedding, second returns None
    mock_store.get_cold_embedding.side_effect = [b"\x00" * 384 * 4, None]
    mock_embedder.cosine_similarity.return_value = 0.8

    search = EpisodicSearch(store=mock_store, embedder=mock_embedder)
    results = await search.search_cold("query", limit=10)

    assert len(results) == 1
    assert results[0].id == "ep-1"


@pytest.mark.asyncio
async def test_search_cold_empty_candidates(
    mock_store: AsyncMock, mock_embedder: MagicMock
) -> None:
    """Empty candidate set returns empty results."""
    mock_store.query_cold.return_value = []

    search = EpisodicSearch(store=mock_store, embedder=mock_embedder)
    results = await search.search_cold("anything", limit=10)

    assert results == []
    mock_embedder.embed.assert_not_called()


@pytest.mark.asyncio
async def test_search_cold_respects_limit(mock_store: AsyncMock, mock_embedder: MagicMock) -> None:
    """Results are trimmed to the requested limit."""
    candidates = [_make_entry(f"ep-{i}", f"entry {i}") for i in range(10)]
    mock_store.query_cold.return_value = candidates
    mock_store.get_cold_embedding.return_value = b"\x00" * 384 * 4
    mock_embedder.cosine_similarity.return_value = 0.5

    search = EpisodicSearch(store=mock_store, embedder=mock_embedder)
    results = await search.search_cold("query", limit=3)

    assert len(results) == 3
