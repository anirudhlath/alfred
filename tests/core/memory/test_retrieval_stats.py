"""Deliberate recall must record that a memory was used.

`retrieval_count` and `last_retrieved` feed the Librarian's decay formula — a
memory that gets recalled resists migration to cold storage. Only
`EpisodicMemory.recall` ever wrote them, but the assistant recalls through
`ContextIndexManager`, so in practice both fields stayed 0 forever and the
"memories you use stay hot" mechanism never did anything.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from core.memory.context_index import ContextIndexManager
from core.memory.vector_store import ContextMetadata, SearchResult, record_retrievals


def _result(id_: str, retrieval_count: int = 0) -> SearchResult:
    return SearchResult(
        id=id_,
        score=0.5,
        content="[reflex:state_change] home.light_turn_on(target=Living Room)",
        semantic_key="",
        metadata=ContextMetadata(
            type="episodic",
            source="reflex",
            entities="",
            timestamp=1786592925.6,
            significance=0.147,
            retrieval_count=retrieval_count,
            last_retrieved=0.0,
            compressed="",
        ),
    )


def _index(results: list[SearchResult]) -> tuple[ContextIndexManager, AsyncMock]:
    store = AsyncMock()
    store.search = AsyncMock(return_value=results)
    embedder = AsyncMock()
    embedder.embed = AsyncMock(return_value=[0.1] * 8)
    return ContextIndexManager(store=store, embedder=embedder, semantic_dirs=[]), store


@pytest.mark.asyncio
async def test_record_retrievals_increments_from_the_existing_count() -> None:
    store = AsyncMock()
    await record_retrievals(store, [_result("a", retrieval_count=3)])

    store.update_metadata.assert_awaited_once()
    id_arg, fields = store.update_metadata.await_args.args
    assert id_arg == "a"
    assert fields["retrieval_count"] == 4  # not reset to 1
    assert fields["last_retrieved"] > 0


@pytest.mark.asyncio
async def test_search_text_records_retrieval_when_asked() -> None:
    index, store = _index([_result("a"), _result("b")])

    await index.search_text(query="living room lights", update_stats=True)

    assert {c.args[0] for c in store.update_metadata.await_args_list} == {"a", "b"}


@pytest.mark.asyncio
async def test_search_text_does_not_record_by_default() -> None:
    """The Librarian's decay pass reads these fields — it must not write them."""
    index, store = _index([_result("a")])

    await index.search_text(query="living room lights")

    store.update_metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_recall_tool_opts_in() -> None:
    from core.conscious.memory_tools import dispatch_memory_tool

    index, store = _index([_result("a")])
    raw = await dispatch_memory_tool(
        "memory_recall_memories", {"query": "what do you know about me"}, index, AsyncMock()
    )

    assert json.loads(raw)["count"] == 1
    store.update_metadata.assert_awaited_once()
