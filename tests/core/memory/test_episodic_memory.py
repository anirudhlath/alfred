"""Tests for EpisodicMemory — unified hot+cold semantic search."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from core.memory.episodic.memory import EpisodicMemory
from core.memory.schemas import EpisodicEntry, EpisodicResult, SignificanceScore
from core.memory.vector_store import ContextMetadata, SearchResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    id: str = "entry-1",  # noqa: A002
    summary: str = "User asked about the weather.",
    semantic_key: str = "",
    source: str = "conversation",
    entities: list[str] | None = None,
    retrieval_count: int = 0,
) -> EpisodicEntry:
    return EpisodicEntry(
        id=id,
        timestamp=datetime(2026, 3, 24, 12, 0, 0, tzinfo=UTC),
        source=source,
        summary=summary,
        entities=entities or [],
        significance=SignificanceScore(overall=0.5),
        semantic_key=semantic_key,
        retrieval_count=retrieval_count,
    )


def _make_search_result(
    id: str = "entry-1",  # noqa: A002
    score: float = 0.9,
    content: str = "User asked about the weather.",
    semantic_key: str = "weather query",
    source: str = "conversation",
    entities: str = "",
    timestamp: float = datetime(2026, 3, 24, 12, 0, 0, tzinfo=UTC).timestamp(),
    significance: float = 0.5,
    retrieval_count: int = 0,
    type_: str = "episodic",
) -> SearchResult:
    return SearchResult(
        id=id,
        score=score,
        content=content,
        semantic_key=semantic_key,
        metadata=ContextMetadata(
            type=type_,
            source=source,
            entities=entities,
            timestamp=timestamp,
            significance=significance,
            retrieval_count=retrieval_count,
        ),
    )


def _ranked_store(*results: SearchResult) -> AsyncMock:
    """A store that answers as a real one does: its best ``limit`` results, best first."""
    ranked = sorted(results, key=lambda r: r.score, reverse=True)
    store = AsyncMock()
    store.search = AsyncMock(side_effect=lambda *_, limit, **__: ranked[:limit])
    return store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_hot_store() -> AsyncMock:
    store = AsyncMock()
    store.search = AsyncMock(return_value=[])
    store.add = AsyncMock()
    store.delete = AsyncMock()
    store.exists = AsyncMock(return_value=False)
    store.count = AsyncMock(return_value=0)
    store.update_metadata = AsyncMock()
    return store


@pytest.fixture
def mock_cold_store() -> AsyncMock:
    store = AsyncMock()
    store.search = AsyncMock(return_value=[])
    store.add = AsyncMock()
    store.delete = AsyncMock()
    store.exists = AsyncMock(return_value=False)
    store.count = AsyncMock(return_value=0)
    store.update_metadata = AsyncMock()
    return store


@pytest.fixture
def episodic_memory(
    mock_hot_store: AsyncMock, mock_cold_store: AsyncMock, mock_embedder: AsyncMock
) -> EpisodicMemory:
    return EpisodicMemory(hot=mock_hot_store, cold=mock_cold_store, embedder=mock_embedder)


# ---------------------------------------------------------------------------
# write() tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_calls_embed_twice(
    episodic_memory: EpisodicMemory, mock_embedder: AsyncMock
) -> None:
    """write() must call embedder.embed() exactly twice (content + semantic_key)."""
    entry = _make_entry(summary="lights on in kitchen", semantic_key="kitchen lights")
    sig = SignificanceScore(overall=0.7)

    await episodic_memory.write(entry, sig)

    assert mock_embedder.embed.await_count == 2


@pytest.mark.asyncio
async def test_write_embeds_summary_and_semantic_key(
    episodic_memory: EpisodicMemory, mock_embedder: AsyncMock
) -> None:
    """write() embeds entry.summary as content and entry.semantic_key as the key."""
    entry = _make_entry(summary="door locked", semantic_key="front door lock")
    await episodic_memory.write(entry, SignificanceScore(overall=0.5))

    embed_calls = [c.args[0] for c in mock_embedder.embed.await_args_list]
    assert "door locked" in embed_calls
    assert "front door lock" in embed_calls


@pytest.mark.asyncio
async def test_write_falls_back_to_summary_when_semantic_key_empty(
    episodic_memory: EpisodicMemory, mock_embedder: AsyncMock
) -> None:
    """When semantic_key is empty, write() uses summary for both embeddings."""
    entry = _make_entry(summary="motion detected", semantic_key="")
    await episodic_memory.write(entry, SignificanceScore(overall=0.4))

    embed_calls = [c.args[0] for c in mock_embedder.embed.await_args_list]
    assert embed_calls.count("motion detected") == 2


@pytest.mark.asyncio
async def test_write_calls_hot_store_add(
    episodic_memory: EpisodicMemory, mock_hot_store: AsyncMock
) -> None:
    """write() must call hot_store.add() exactly once."""
    entry = _make_entry()
    await episodic_memory.write(entry, SignificanceScore(overall=0.6))

    mock_hot_store.add.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_does_not_call_cold_store(
    episodic_memory: EpisodicMemory, mock_cold_store: AsyncMock
) -> None:
    """write() must NOT touch the cold store."""
    entry = _make_entry()
    await episodic_memory.write(entry, SignificanceScore(overall=0.6))

    mock_cold_store.add.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_sets_significance_on_entry(
    episodic_memory: EpisodicMemory,
) -> None:
    """write() sets entry.significance to the provided SignificanceScore."""
    entry = _make_entry()
    sig = SignificanceScore(overall=0.88, safety=0.5, novelty=0.7)
    await episodic_memory.write(entry, sig)

    assert entry.significance == sig


@pytest.mark.asyncio
async def test_write_passes_correct_id_to_hot_store(
    episodic_memory: EpisodicMemory, mock_hot_store: AsyncMock
) -> None:
    """write() passes the entry id to hot_store.add()."""
    entry = _make_entry(id="unique-id-42")
    await episodic_memory.write(entry, SignificanceScore(overall=0.5))

    kwargs = mock_hot_store.add.await_args.kwargs
    assert kwargs["id"] == "unique-id-42"


# ---------------------------------------------------------------------------
# recall() tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_searches_both_stores(
    episodic_memory: EpisodicMemory,
    mock_hot_store: AsyncMock,
    mock_cold_store: AsyncMock,
) -> None:
    """recall() must search hot and cold stores."""
    await episodic_memory.recall("weather")

    mock_hot_store.search.assert_awaited_once()
    mock_cold_store.search.assert_awaited_once()


@pytest.mark.asyncio
async def test_recall_returns_empty_when_no_results(
    episodic_memory: EpisodicMemory,
) -> None:
    """recall() returns empty list when both stores return nothing."""
    results = await episodic_memory.recall("anything")
    assert results == []


@pytest.mark.asyncio
async def test_recall_returns_episodic_results(
    episodic_memory: EpisodicMemory, mock_hot_store: AsyncMock
) -> None:
    """recall() returns list of EpisodicResult instances."""
    mock_hot_store.search.return_value = [_make_search_result()]

    results = await episodic_memory.recall("weather")

    assert len(results) == 1
    assert isinstance(results[0], EpisodicResult)


@pytest.mark.asyncio
async def test_recall_deduplicates_same_id_keeps_highest_score(
    episodic_memory: EpisodicMemory,
    mock_hot_store: AsyncMock,
    mock_cold_store: AsyncMock,
) -> None:
    """When same id appears in both stores, keep the one with higher score."""
    mock_hot_store.search.return_value = [_make_search_result(id="dup", score=0.6)]
    mock_cold_store.search.return_value = [_make_search_result(id="dup", score=0.9)]

    results = await episodic_memory.recall("query")

    assert len(results) == 1
    assert results[0].score == 0.9
    assert results[0].source_store == "cold"


@pytest.mark.asyncio
async def test_recall_deduplicates_keeps_hot_when_higher(
    episodic_memory: EpisodicMemory,
    mock_hot_store: AsyncMock,
    mock_cold_store: AsyncMock,
) -> None:
    """When hot score is higher than cold, keep the hot entry."""
    mock_hot_store.search.return_value = [_make_search_result(id="dup", score=0.95)]
    mock_cold_store.search.return_value = [_make_search_result(id="dup", score=0.7)]

    results = await episodic_memory.recall("query")

    assert len(results) == 1
    assert results[0].score == 0.95
    assert results[0].source_store == "hot"


@pytest.mark.asyncio
async def test_recall_merges_distinct_ids_from_both_stores(
    episodic_memory: EpisodicMemory,
    mock_hot_store: AsyncMock,
    mock_cold_store: AsyncMock,
) -> None:
    """Distinct ids from hot and cold are merged into a single result list."""
    mock_hot_store.search.return_value = [_make_search_result(id="hot-only", score=0.8)]
    mock_cold_store.search.return_value = [_make_search_result(id="cold-only", score=0.7)]

    results = await episodic_memory.recall("query")

    ids = {r.entry.id for r in results}
    assert ids == {"hot-only", "cold-only"}


@pytest.mark.asyncio
async def test_recall_sorts_by_score_descending(
    episodic_memory: EpisodicMemory,
    mock_hot_store: AsyncMock,
    mock_cold_store: AsyncMock,
) -> None:
    """recall() returns results sorted by score descending."""
    mock_hot_store.search.return_value = [
        _make_search_result(id="a", score=0.5),
        _make_search_result(id="b", score=0.9),
    ]
    mock_cold_store.search.return_value = [
        _make_search_result(id="c", score=0.7),
    ]

    results = await episodic_memory.recall("query")

    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_recall_respects_limit(
    episodic_memory: EpisodicMemory,
    mock_hot_store: AsyncMock,
    mock_cold_store: AsyncMock,
) -> None:
    """recall() returns at most `limit` results."""
    mock_hot_store.search.return_value = [
        _make_search_result(id=f"h{i}", score=float(i) / 10) for i in range(5)
    ]
    mock_cold_store.search.return_value = [
        _make_search_result(id=f"c{i}", score=float(i) / 10 + 0.01) for i in range(5)
    ]

    results = await episodic_memory.recall("query", limit=3)

    assert len(results) <= 3


@pytest.mark.asyncio
async def test_recall_increments_retrieval_count(
    episodic_memory: EpisodicMemory, mock_hot_store: AsyncMock
) -> None:
    """Returned entries have retrieval_count incremented by 1."""
    mock_hot_store.search.return_value = [_make_search_result(retrieval_count=5)]

    results = await episodic_memory.recall("query")

    assert results[0].entry.retrieval_count == 6


@pytest.mark.asyncio
async def test_recall_with_empty_query_works(
    episodic_memory: EpisodicMemory,
) -> None:
    """recall() with empty string query does not raise."""
    results = await episodic_memory.recall("")
    assert results == []


@pytest.mark.asyncio
async def test_recall_leaves_the_type_filter_out_of_the_store_searches(
    episodic_memory: EpisodicMemory,
    mock_hot_store: AsyncMock,
    mock_cold_store: AsyncMock,
) -> None:
    """Neither store can filter by type. The hot store's ``type`` field is TEXT and its
    filters are TAG queries, which RediSearch rejects ("Expected a TAG field"): the store
    logs the error and answers [], so every hot memory vanished. The cold store ignores
    filters altogether. recall() applies ``types`` to the merge instead."""
    await episodic_memory.recall("query", types=["episodic", "conversation"])

    for store in (mock_hot_store, mock_cold_store):
        assert store.search.await_args.kwargs.get("filters") is None


@pytest.mark.asyncio
async def test_recall_no_filters_when_types_none(
    episodic_memory: EpisodicMemory,
    mock_hot_store: AsyncMock,
    mock_cold_store: AsyncMock,
) -> None:
    """recall() passes filters=None when types argument is not given."""
    await episodic_memory.recall("query")

    for store in (mock_hot_store, mock_cold_store):
        kwargs = store.search.await_args.kwargs
        assert kwargs.get("filters") is None


@pytest.mark.asyncio
async def test_recall_filters_by_since(
    episodic_memory: EpisodicMemory,
    mock_hot_store: AsyncMock,
    mock_cold_store: AsyncMock,
) -> None:
    """recall() excludes entries older than `since`."""
    old_ts = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    new_ts = datetime(2026, 3, 24, tzinfo=UTC).timestamp()

    mock_hot_store.search.return_value = [
        _make_search_result(id="old", score=0.9, timestamp=old_ts),
        _make_search_result(id="new", score=0.8, timestamp=new_ts),
    ]

    since = datetime(2026, 2, 1, tzinfo=UTC)
    results = await episodic_memory.recall("query", since=since)

    ids = {r.entry.id for r in results}
    assert "old" not in ids
    assert "new" in ids


@pytest.mark.asyncio
async def test_recall_source_store_label_is_correct(
    episodic_memory: EpisodicMemory,
    mock_hot_store: AsyncMock,
    mock_cold_store: AsyncMock,
) -> None:
    """EpisodicResult.source_store matches which store the result came from."""
    mock_hot_store.search.return_value = [_make_search_result(id="h1", score=0.8)]
    mock_cold_store.search.return_value = [_make_search_result(id="c1", score=0.7)]

    results = await episodic_memory.recall("query")

    store_by_id = {r.entry.id: r.source_store for r in results}
    assert store_by_id["h1"] == "hot"
    assert store_by_id["c1"] == "cold"


@pytest.mark.asyncio
async def test_recall_entities_parsed_correctly(
    episodic_memory: EpisodicMemory, mock_hot_store: AsyncMock
) -> None:
    """Entities stored as comma-separated string are parsed back to list."""
    mock_hot_store.search.return_value = [
        _make_search_result(entities="kitchen_light,motion_sensor")
    ]

    results = await episodic_memory.recall("query")

    assert results[0].entry.entities == ["kitchen_light", "motion_sensor"]


@pytest.mark.asyncio
async def test_recall_empty_entities_string_returns_empty_list(
    episodic_memory: EpisodicMemory, mock_hot_store: AsyncMock
) -> None:
    """Empty entities string results in empty list on the returned entry."""
    mock_hot_store.search.return_value = [_make_search_result(entities="")]

    results = await episodic_memory.recall("query")

    assert results[0].entry.entities == []


@pytest.mark.asyncio
async def test_recall_fails_when_the_cold_store_does(
    episodic_memory: EpisodicMemory,
    mock_hot_store: AsyncMock,
    mock_cold_store: AsyncMock,
) -> None:
    """A half-empty answer reads as "no such memory", and nothing would say the archive
    had stopped answering — so a cold failure fails the recall (admin search 503s)."""
    mock_hot_store.search.return_value = [_make_search_result(id="h1")]
    mock_cold_store.search.side_effect = RuntimeError("cold store latched")

    with pytest.raises(RuntimeError, match="latched"):
        await episodic_memory.recall("query")


# ---------------------------------------------------------------------------
# recall() — type and time filters apply before the limit (issue #311)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_finds_the_type_asked_for_below_the_limit(mock_embedder: AsyncMock) -> None:
    hot = _ranked_store(
        _make_search_result(id="pref-1", score=0.9, type_="semantic"),
        _make_search_result(id="pref-2", score=0.8, type_="semantic"),
        _make_search_result(id="routine", score=0.7, type_="routine"),
        _make_search_result(id="boiler-failed", score=0.6),
        _make_search_result(id="boiler-serviced", score=0.5),
    )
    memory = EpisodicMemory(hot=hot, cold=_ranked_store(), embedder=mock_embedder)

    results = await memory.recall("boiler", limit=2, types=["episodic"])

    assert [(r.entry.id, r.source_store) for r in results] == [
        ("boiler-failed", "hot"),
        ("boiler-serviced", "hot"),
    ]


@pytest.mark.asyncio
async def test_recall_finds_recent_memories_below_older_ones(mock_embedder: AsyncMock) -> None:
    now = datetime.now(UTC)
    old = (now - timedelta(days=40)).timestamp()
    recent = (now - timedelta(days=1)).timestamp()
    hot = _ranked_store(
        _make_search_result(id="old-1", score=0.9, timestamp=old),
        _make_search_result(id="old-2", score=0.8, timestamp=old),
        _make_search_result(id="recent-hot", score=0.7, timestamp=recent),
        _make_search_result(id="old-3", score=0.5, timestamp=old),
    )
    cold = _ranked_store(
        _make_search_result(id="old-cold", score=0.85, timestamp=old),
        _make_search_result(id="recent-cold", score=0.4, timestamp=recent),
    )
    memory = EpisodicMemory(hot=hot, cold=cold, embedder=mock_embedder)

    results = await memory.recall("q", limit=2, since=now - timedelta(days=7))

    assert [(r.entry.id, r.source_store) for r in results] == [
        ("recent-hot", "hot"),
        ("recent-cold", "cold"),
    ]


@pytest.mark.asyncio
async def test_recall_leaves_the_cold_store_out_when_the_types_exclude_episodic(
    mock_embedder: AsyncMock,
) -> None:
    """Decay moves only episodic memories, so the cold store holds nothing else."""
    hot = _ranked_store(_make_search_result(id="routine", score=0.6, type_="routine"))
    cold = _ranked_store(_make_search_result(id="cold", score=0.9))
    memory = EpisodicMemory(hot=hot, cold=cold, embedder=mock_embedder)

    results = await memory.recall("q", types=["routine"])

    assert [r.entry.id for r in results] == ["routine"]
    cold.search.assert_not_awaited()


@pytest.mark.asyncio
async def test_recall_records_only_the_hot_matches_it_returns(mock_embedder: AsyncMock) -> None:
    """Hot is read past ``pref`` to reach ``kept``; neither the filtered-out entry nor
    the one cut by the limit counts as used."""
    hot = _ranked_store(
        _make_search_result(id="pref", score=0.9, type_="semantic"),
        _make_search_result(id="kept", score=0.6, retrieval_count=2),
        _make_search_result(id="cut", score=0.5),
    )
    cold = _ranked_store(_make_search_result(id="moved", score=0.4))
    memory = EpisodicMemory(hot=hot, cold=cold, embedder=mock_embedder)

    results = await memory.recall("q", limit=1, types=["episodic"])

    assert [(r.entry.id, r.entry.retrieval_count) for r in results] == [("kept", 3)]
    updated = [c.args for c in hot.update_metadata.await_args_list]
    assert [(memory_id, fields["retrieval_count"]) for memory_id, fields in updated] == [
        ("kept", 3)
    ]
    cold.update_metadata.assert_not_awaited()


# ---------------------------------------------------------------------------
# migrate_to_cold() tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migrate_to_cold_deletes_from_hot(
    episodic_memory: EpisodicMemory, mock_hot_store: AsyncMock
) -> None:
    """migrate_to_cold() always calls delete on the hot store (no-op if missing)."""
    await episodic_memory.migrate_to_cold("entry-1")

    mock_hot_store.delete.assert_awaited_once_with("entry-1")


@pytest.mark.asyncio
async def test_migrate_to_cold_does_not_touch_cold_store(
    episodic_memory: EpisodicMemory,
    mock_hot_store: AsyncMock,
    mock_cold_store: AsyncMock,
) -> None:
    """migrate_to_cold() never calls cold_store.add() or cold_store.delete()."""
    await episodic_memory.migrate_to_cold("entry-1")

    mock_cold_store.add.assert_not_awaited()
    mock_cold_store.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# copy_to_cold_and_remove() tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_copy_to_cold_moves_the_vectors_hot_already_holds(
    episodic_memory: EpisodicMemory,
    mock_hot_store: AsyncMock,
    mock_cold_store: AsyncMock,
    mock_embedder: AsyncMock,
) -> None:
    """The same model embedded the same text on the way in — embedding it again buys
    nothing and costs two model calls per migrated memory."""
    content, semantic = [1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]
    mock_hot_store.embeddings = AsyncMock(return_value=(content, semantic))
    result = _make_search_result(id="m1")

    await episodic_memory.copy_to_cold_and_remove(result)

    mock_hot_store.embeddings.assert_awaited_once_with("m1")
    mock_embedder.embed.assert_not_awaited()
    added = mock_cold_store.add.await_args.kwargs
    assert (added["embedding_content"], added["embedding_semantic"]) == (content, semantic)
    mock_hot_store.delete.assert_awaited_once_with("m1")


@pytest.mark.asyncio
async def test_copy_to_cold_embeds_when_hot_cannot_hand_its_vectors_back(
    episodic_memory: EpisodicMemory,
    mock_hot_store: AsyncMock,
    mock_cold_store: AsyncMock,
    mock_embedder: AsyncMock,
) -> None:
    mock_hot_store.embeddings = AsyncMock(return_value=None)
    result = _make_search_result(id="m1", content="the door opened", semantic_key="door")

    await episodic_memory.copy_to_cold_and_remove(result)

    assert [c.args[0] for c in mock_embedder.embed.await_args_list] == ["the door opened", "door"]
    added = mock_cold_store.add.await_args.kwargs
    assert added["embedding_content"] == added["embedding_semantic"] == [0.1, 0.2, 0.3, 0.4]
    mock_hot_store.delete.assert_awaited_once_with("m1")


# ---------------------------------------------------------------------------
# recall() retrieval stats persistence tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_persists_retrieval_count_to_hot_store(
    episodic_memory: EpisodicMemory,
    mock_hot_store: AsyncMock,
    mock_cold_store: AsyncMock,
) -> None:
    """recall() must call update_metadata on hot store for each hot result."""
    mock_hot_store.search.return_value = [
        _make_search_result(id="h1", score=0.9, retrieval_count=3),
    ]
    mock_hot_store.update_metadata = AsyncMock()

    await episodic_memory.recall("query")

    mock_hot_store.update_metadata.assert_awaited_once()
    call_args = mock_hot_store.update_metadata.await_args
    assert call_args[0][0] == "h1"
    fields = call_args[0][1]
    assert fields["retrieval_count"] == 4
    assert "last_retrieved" in fields


@pytest.mark.asyncio
async def test_recall_does_not_persist_stats_for_cold_results(
    episodic_memory: EpisodicMemory,
    mock_hot_store: AsyncMock,
    mock_cold_store: AsyncMock,
) -> None:
    """recall() must NOT call update_metadata for cold store results."""
    mock_cold_store.search.return_value = [
        _make_search_result(id="c1", score=0.9),
    ]
    mock_hot_store.update_metadata = AsyncMock()

    await episodic_memory.recall("query")

    mock_hot_store.update_metadata.assert_not_awaited()


# ---------------------------------------------------------------------------
# recall() update_stats=False tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_update_stats_false_skips_hot_store_write(
    episodic_memory: EpisodicMemory,
    mock_hot_store: AsyncMock,
    mock_cold_store: AsyncMock,
) -> None:
    """When update_stats=False, recall() must NOT call update_metadata at all."""
    mock_hot_store.search.return_value = [
        _make_search_result(id="h1", score=0.9, retrieval_count=2),
    ]
    mock_hot_store.update_metadata = AsyncMock()

    await episodic_memory.recall("query", update_stats=False)

    mock_hot_store.update_metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_recall_update_stats_true_by_default_persists_hot_stats(
    episodic_memory: EpisodicMemory,
    mock_hot_store: AsyncMock,
) -> None:
    """Default recall() (update_stats=True) still calls update_metadata for hot results."""
    mock_hot_store.search.return_value = [
        _make_search_result(id="h1", score=0.9, retrieval_count=1),
    ]
    mock_hot_store.update_metadata = AsyncMock()

    await episodic_memory.recall("query")

    mock_hot_store.update_metadata.assert_awaited_once()
    call_args = mock_hot_store.update_metadata.await_args
    assert call_args[0][0] == "h1"
    assert call_args[0][1]["retrieval_count"] == 2
