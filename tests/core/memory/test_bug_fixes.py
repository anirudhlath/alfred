"""Regression tests for memory system bug fixes.

Covers: dim default, FT.SEARCH RETURN count, compressed TAG field,
empty-tag filter, copy-before-delete ordering, search_text embedding,
pattern matching, cache sharing, model load logging, config fields.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

# ---------------------------------------------------------------------------
# Async iterator helper
# ---------------------------------------------------------------------------


class AsyncIteratorMock:
    def __init__(self, items: list[object]) -> None:
        self._items = iter(items)

    def __aiter__(self) -> AsyncIteratorMock:
        return self

    async def __anext__(self) -> object:
        try:
            return next(self._items)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


# ---------------------------------------------------------------------------
# Test 1: RedisVectorStore default dim matches AlfredConfig
# ---------------------------------------------------------------------------


def test_redis_vector_store_default_dim_matches_config() -> None:
    """Default dim should be 768 to match AlfredConfig.embedding_dim."""
    from core.memory.redis_vector_store import RedisVectorStore
    from shared.config import AlfredConfig

    store = RedisVectorStore(redis=AsyncMock())
    config = AlfredConfig()
    assert store._dim == config.embedding_dim == 768


# ---------------------------------------------------------------------------
# Test 2: FT.SEARCH RETURN count matches field count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ft_search_return_count_matches_fields() -> None:
    """The RETURN count in FT.SEARCH must match the number of field names."""
    import inspect

    from core.memory import redis_vector_store

    source = inspect.getsource(redis_vector_store)
    # RETURN 11 means 11 field names follow before SORTBY
    assert '"11"' in source, "RETURN count should be 11 to match 11 field names"


# ---------------------------------------------------------------------------
# Test 3: compressed field is TAG (not TEXT) in schema
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compressed_field_is_tag_type() -> None:
    """compressed must be TAG (not TEXT) for filter syntax to work."""
    import inspect

    from core.memory import redis_vector_store

    source = inspect.getsource(redis_vector_store)
    lines = source.split("\n")
    for i, line in enumerate(lines):
        if '"compressed"' in line:
            next_line = lines[i + 1] if i + 1 < len(lines) else ""
            assert '"TAG"' in next_line, f"compressed field should be TAG, got: {next_line}"
            break
    else:
        pytest.fail("compressed field not found in schema")


# ---------------------------------------------------------------------------
# Test 4: Empty-string filter generates negative match
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_string_filter_generates_negative_match() -> None:
    """Filtering compressed='' should produce -@compressed:{yes} not @compressed:{}."""
    from core.memory.redis_vector_store import RedisVectorStore

    mock_redis = AsyncMock()
    mock_redis.execute_command = AsyncMock(return_value=[0])  # empty results
    store = RedisVectorStore(redis=mock_redis, dim=4)
    store._index_ready = True

    await store.search([0.1, 0.2, 0.3, 0.4], limit=5, filters={"compressed": ""})

    call_args = mock_redis.execute_command.call_args_list
    for call in call_args:
        args = call[0]
        if args[0] == "FT.SEARCH":
            query = args[2]
            assert "-@compressed:{yes}" in query, f"Expected negative match, got: {query}"
            assert "@compressed:{}" not in query, f"Should not use empty tag: {query}"
            return

    pytest.fail("FT.SEARCH was not called")


# ---------------------------------------------------------------------------
# Test 5: copy_to_cold_and_remove writes to cold before deleting from hot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_copy_to_cold_writes_before_delete() -> None:
    """copy_to_cold_and_remove must write to cold BEFORE deleting from hot."""
    from core.memory.episodic.memory import EpisodicMemory
    from core.memory.vector_store import ContextMetadata, SearchResult

    hot = AsyncMock()
    cold = AsyncMock()
    embedder = AsyncMock()
    embedder.embed = AsyncMock(return_value=[0.1, 0.2, 0.3, 0.4])

    mem = EpisodicMemory(hot=hot, cold=cold, embedder=embedder)

    result = SearchResult(
        id="ep-1",
        score=0.9,
        content="test content",
        semantic_key="test key",
        metadata=ContextMetadata(
            type="episodic",
            source="conversation",
            entities="light.kitchen",
            timestamp=1711000000.0,
            significance=0.5,
            retrieval_count=0,
        ),
    )

    # Track call order
    call_order: list[str] = []
    cold.add = AsyncMock(side_effect=lambda **kw: call_order.append("cold.add"))
    hot.delete = AsyncMock(side_effect=lambda entry_id: call_order.append("hot.delete"))

    await mem.copy_to_cold_and_remove(result)

    assert call_order == ["cold.add", "hot.delete"], (
        f"cold.add must come before hot.delete, got: {call_order}"
    )


# ---------------------------------------------------------------------------
# Test 6: search_text embeds query internally
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_text_embeds_internally() -> None:
    """search_text should embed the query and call search — callers don't need embedder."""
    from core.memory.context_index import ContextIndexManager

    mock_store = AsyncMock()
    mock_store.search = AsyncMock(return_value=[])
    mock_embedder = AsyncMock()
    mock_embedder.embed = AsyncMock(return_value=[0.1, 0.2, 0.3, 0.4])

    ctx = ContextIndexManager(store=mock_store, embedder=mock_embedder)

    results = await ctx.search_text("lighting preferences", limit=5)

    mock_embedder.embed.assert_awaited_once_with("lighting preferences")
    mock_store.search.assert_awaited_once()
    assert results == []


# ---------------------------------------------------------------------------
# Test 7: match_trigger_pattern shared utility
# ---------------------------------------------------------------------------


def test_match_trigger_pattern_hhmm() -> None:
    from core.memory.routines.patterns import match_trigger_pattern

    now = datetime(2026, 3, 24, 20, 0, tzinfo=UTC)
    assert match_trigger_pattern("20:00 daily", now) is True
    assert match_trigger_pattern("14:00 daily", now) is False


def test_match_trigger_pattern_morning() -> None:
    from core.memory.routines.patterns import match_trigger_pattern

    morning = datetime(2026, 3, 24, 8, 0, tzinfo=UTC)
    evening = datetime(2026, 3, 24, 20, 0, tzinfo=UTC)
    assert match_trigger_pattern("morning routine", morning) is True
    assert match_trigger_pattern("morning routine", evening) is False


def test_match_trigger_pattern_unknown_returns_true() -> None:
    from core.memory.routines.patterns import match_trigger_pattern

    now = datetime(2026, 3, 24, 12, 0, tzinfo=UTC)
    assert match_trigger_pattern("some unknown pattern", now) is True


# ---------------------------------------------------------------------------
# Test 8: ContextReader._get_snapshot shares cache between methods
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_reader_shares_cache_between_methods() -> None:
    """get_rendered_context and get_entity_states should share the same cached snapshot."""
    from core.reflex.context_reader import ContextReader

    mock_redis = AsyncMock()
    # scan_iter is a sync method that returns an async iterator — use MagicMock, not AsyncMock
    from unittest.mock import MagicMock

    mock_redis.scan_iter = MagicMock(return_value=AsyncIteratorMock([]))

    reader = ContextReader(redis=mock_redis)

    await reader.get_rendered_context()
    await reader.get_entity_states()

    # scan_iter should only be called ONCE (cached)
    assert mock_redis.scan_iter.call_count == 1


# ---------------------------------------------------------------------------
# Test 9: EmbeddingProvider logs on load failure
# ---------------------------------------------------------------------------


def test_embedding_provider_logs_load_failure(caplog: pytest.LogCaptureFixture) -> None:
    """Failed model load should log an error with the model name."""
    from core.memory.embedding_provider import SentenceTransformerProvider

    provider = SentenceTransformerProvider(model_name="nonexistent/model-xyz-404")
    with caplog.at_level(logging.ERROR), pytest.raises(Exception):  # noqa: B017
        provider.embed_sync("test")
    assert "nonexistent/model-xyz-404" in caplog.text


# ---------------------------------------------------------------------------
# Test 10: ConsciousConfig has involuntary recall fields
# ---------------------------------------------------------------------------


def test_conscious_config_has_recall_fields() -> None:
    """ConsciousConfig should have involuntary_recall_limit and threshold."""
    from core.conscious.engine import ConsciousConfig

    config = ConsciousConfig(model="test")
    assert config.involuntary_recall_limit == 10
    assert config.involuntary_recall_threshold == 0.5
