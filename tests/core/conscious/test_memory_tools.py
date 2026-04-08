"""Tests for memory tools (deliberate recall)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from core.conscious.memory_tools import (
    MEMORY_TOOL_PREFIX,
    MEMORY_TOOLS_MANIFEST,
    dispatch_memory_tool,
)
from core.memory.vector_store import ContextMetadata, SearchResult


def _make_search_result(
    content: str = "test",
    type_: str = "semantic",
    source: str = "test.md",
    score: float = 0.85,
    timestamp: float = 0.0,
) -> SearchResult:
    return SearchResult(
        id="sr-1",
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


class TestMemoryToolsManifest:
    def test_prefix_constant(self) -> None:
        assert MEMORY_TOOL_PREFIX == "memory_"

    def test_manifest_has_recall_and_live_state(self) -> None:
        names = [t["function"]["name"] for t in MEMORY_TOOLS_MANIFEST]
        assert "memory_recall_memories" in names
        assert "memory_get_live_state" in names

    def test_recall_memories_requires_query(self) -> None:
        recall = next(
            t for t in MEMORY_TOOLS_MANIFEST if t["function"]["name"] == "memory_recall_memories"
        )
        assert "query" in recall["function"]["parameters"]["required"]


class TestDispatchMemoryTool:
    @pytest.mark.asyncio
    async def test_recall_memories_basic(self) -> None:
        context_index = AsyncMock()
        context_index.search_text.return_value = [
            _make_search_result("Sir prefers dim lighting", type_="semantic", score=0.9),
        ]

        context_reader = AsyncMock()

        result_json = await dispatch_memory_tool(
            "memory_recall_memories",
            {"query": "lighting preferences"},
            context_index=context_index,
            context_reader=context_reader,
        )

        result = json.loads(result_json)
        assert result["count"] == 1
        assert result["memories"][0]["content"] == "Sir prefers dim lighting"
        assert result["memories"][0]["type"] == "semantic"
        assert result["memories"][0]["score"] == 0.9

        context_index.search_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_recall_memories_filter_by_type(self) -> None:
        context_index = AsyncMock()
        context_index.search_text.return_value = [
            _make_search_result("episodic entry", type_="episodic"),
            _make_search_result("semantic entry", type_="semantic"),
        ]

        result_json = await dispatch_memory_tool(
            "memory_recall_memories",
            {"query": "test", "types": ["episodic"]},
            context_index=context_index,
            context_reader=AsyncMock(),
        )

        result = json.loads(result_json)
        assert result["count"] == 1
        assert result["memories"][0]["type"] == "episodic"

    @pytest.mark.asyncio
    async def test_recall_memories_filter_by_time(self) -> None:
        import time

        now = time.time()
        old_ts = now - 86400 * 30  # 30 days ago
        recent_ts = now - 86400 * 1  # 1 day ago

        context_index = AsyncMock()
        context_index.search_text.return_value = [
            _make_search_result("old entry", timestamp=old_ts),
            _make_search_result("recent entry", timestamp=recent_ts),
        ]

        result_json = await dispatch_memory_tool(
            "memory_recall_memories",
            {"query": "test", "since_days_ago": 7},
            context_index=context_index,
            context_reader=AsyncMock(),
        )

        result = json.loads(result_json)
        assert result["count"] == 1
        assert result["memories"][0]["content"] == "recent entry"

    @pytest.mark.asyncio
    async def test_get_live_state(self) -> None:
        context_reader = AsyncMock()
        context_reader.get_entity_states.return_value = [
            {"entity_id": "light.living_room", "state": "on"},
        ]

        result_json = await dispatch_memory_tool(
            "memory_get_live_state",
            {"entities": ["light.*"]},
            context_index=AsyncMock(),
            context_reader=context_reader,
        )

        result = json.loads(result_json)
        assert len(result["entities"]) == 1
        assert result["entities"][0]["entity_id"] == "light.living_room"
        context_reader.get_entity_states.assert_called_once_with(patterns=["light.*"])

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self) -> None:
        result_json = await dispatch_memory_tool(
            "memory_unknown",
            {},
            context_index=AsyncMock(),
            context_reader=AsyncMock(),
        )

        result = json.loads(result_json)
        assert "error" in result
        assert "Unknown memory tool" in result["error"]
