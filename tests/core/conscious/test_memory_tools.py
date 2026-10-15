"""Tests for memory tools (deliberate recall)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.conscious.memory_tools import get_live_state, recall_memories
from core.memory.context_index import ContextIndexManager
from core.memory.vector_store import ContextMetadata, SearchResult

_DAY = 86400


def _make_search_result(
    content: str = "test",
    type_: str = "semantic",
    source: str = "test.md",
    score: float = 0.85,
    timestamp: float = 0.0,
) -> SearchResult:
    return SearchResult(
        id=content,
        score=score,
        content=content,
        semantic_key=content,
        metadata=ContextMetadata(
            type=type_,
            source=source,
            entities="",
            timestamp=timestamp,
            significance=1.0,
            retrieval_count=0,
        ),
    )


def _ranked_index(*results: SearchResult) -> tuple[ContextIndexManager, AsyncMock]:
    """The real index over a hot store that answers as a real one does: its best
    ``limit`` results, best first, and no more."""
    ranked = sorted(results, key=lambda r: r.score, reverse=True)
    store = AsyncMock()
    store.search = AsyncMock(side_effect=lambda *, limit, **_: ranked[:limit])
    embedder = AsyncMock()
    embedder.embed = AsyncMock(return_value=[0.1, 0.2, 0.3, 0.4])
    return ContextIndexManager(store=store, embedder=embedder), store


async def _recall(index: ContextIndexManager, **params: Any) -> list[str]:
    reply = await recall_memories(index, **params)
    return [memory["content"] for memory in reply["memories"]]


class TestDispatchMemoryTool:
    @pytest.mark.asyncio
    async def test_recall_memories_basic(self) -> None:
        context_index = AsyncMock()
        context_index.recall.return_value = [
            _make_search_result("Sir prefers dim lighting", type_="semantic", score=0.9),
        ]

        result = await recall_memories(context_index, **{"query": "lighting preferences"})
        assert result["count"] == 1
        assert result["memories"][0]["content"] == "Sir prefers dim lighting"
        assert result["memories"][0]["type"] == "semantic"
        assert result["memories"][0]["score"] == 0.9

        context_index.recall.assert_awaited_once_with(
            "lighting preferences", limit=10, types=None, since=None
        )

    @pytest.mark.asyncio
    async def test_recall_memories_hands_its_filters_to_the_search(self) -> None:
        """Filtering what a limited search returned is issue #311 — the search filters."""
        context_index = AsyncMock()
        context_index.recall.return_value = []
        before = datetime.now(UTC)

        await recall_memories(
            context_index,
            **{"query": "boiler", "types": ["episodic"], "since_days_ago": 7, "limit": 3},
        )

        kwargs = context_index.recall.await_args.kwargs
        assert (kwargs["limit"], kwargs["types"]) == (3, ["episodic"])
        week = timedelta(days=7)
        assert before - week <= kwargs["since"] <= datetime.now(UTC) - week

    @pytest.mark.asyncio
    async def test_recall_memories_filter_by_type(self) -> None:
        index, _ = _ranked_index(
            _make_search_result("episodic entry", type_="episodic"),
            _make_search_result("semantic entry", type_="semantic"),
        )

        assert await _recall(index, query="test", types=["episodic"]) == ["episodic entry"]

    @pytest.mark.asyncio
    async def test_recall_memories_filter_by_time(self) -> None:
        now = time.time()
        index, _ = _ranked_index(
            _make_search_result("old entry", type_="episodic", timestamp=now - 30 * _DAY),
            _make_search_result("recent entry", type_="episodic", timestamp=now - _DAY),
        )

        assert await _recall(index, query="test", since_days_ago=7) == ["recent entry"]

    @pytest.mark.asyncio
    async def test_recall_memories_finds_the_type_asked_for_below_the_limit(self) -> None:
        """Issue #311: the best two were preferences, so filtering the best two left
        nothing, though the events asked for ranked just below them."""
        index, _ = _ranked_index(
            _make_search_result("prefers the boiler at 60C", type_="semantic", score=0.9),
            _make_search_result("boiler checks on Mondays", type_="routine", score=0.8),
            _make_search_result("boiler failed", type_="episodic", score=0.6),
            _make_search_result("boiler serviced", type_="episodic", score=0.5),
        )

        recalled = await _recall(index, query="boiler", types=["episodic"], limit=2)

        assert recalled == ["boiler failed", "boiler serviced"]

    @pytest.mark.asyncio
    async def test_recall_memories_finds_recent_ones_below_older_ones(self) -> None:
        now = time.time()
        index, _ = _ranked_index(
            _make_search_result("old 1", type_="episodic", score=0.9, timestamp=now - 40 * _DAY),
            _make_search_result("old 2", type_="episodic", score=0.8, timestamp=now - 40 * _DAY),
            _make_search_result("recent", type_="episodic", score=0.7, timestamp=now - _DAY),
            # Semantic sections and routines are timeless: timestamp 0 passes the filter.
            _make_search_result("timeless", type_="semantic", score=0.6),
            _make_search_result("old 3", type_="episodic", score=0.5, timestamp=now - 40 * _DAY),
        )

        recalled = await _recall(index, query="q", since_days_ago=7, limit=2)

        assert recalled == ["recent", "timeless"]

    @pytest.mark.asyncio
    async def test_recall_memories_counts_only_what_it_returns_as_retrieved(self) -> None:
        """The decay pass reads these stats: a memory the filters dropped was not used."""
        index, store = _ranked_index(
            _make_search_result("a preference", type_="semantic", score=0.9),
            _make_search_result("an event", type_="episodic", score=0.6),
        )

        assert await _recall(index, query="q", types=["episodic"]) == ["an event"]

        assert [c.args[0] for c in store.update_metadata.await_args_list] == ["an event"]

    @pytest.mark.asyncio
    async def test_get_live_state(self) -> None:
        context_reader = AsyncMock()
        context_reader.get_entity_states.return_value = [
            {"entity_id": "light.living_room", "state": "on"},
        ]

        result = await get_live_state(context_reader, ["light.*"])
        assert result["available"] is True
        assert len(result["entities"]) == 1
        assert result["entities"][0]["entity_id"] == "light.living_room"
        context_reader.get_entity_states.assert_called_once_with(patterns=["light.*"])

    @pytest.mark.asyncio
    async def test_get_live_state_says_when_there_is_none(self) -> None:
        context_reader = AsyncMock()
        context_reader.get_entity_states.return_value = None

        assert await get_live_state(context_reader) == {"available": False, "entities": []}
