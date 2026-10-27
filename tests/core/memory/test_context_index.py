"""Tests for ContextIndexManager."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from core.memory.context_index import ContextIndexManager
from core.memory.vector_store import ContextMetadata, Range, SearchResult

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(compressed: str = "") -> SearchResult:
    return SearchResult(
        id="test-id",
        score=0.9,
        content="some content",
        semantic_key="some key",
        metadata=ContextMetadata(
            type="episodic",
            source="test",
            entities="",
            timestamp=0.0,
            significance=0.5,
            retrieval_count=0,
            compressed=compressed,
        ),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def manager(mock_vector_store: AsyncMock, mock_embedder: AsyncMock) -> ContextIndexManager:
    return ContextIndexManager(store=mock_vector_store, embedder=mock_embedder)


# ---------------------------------------------------------------------------
# index_episodic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_episodic_calls_store_add(
    manager: ContextIndexManager,
    mock_vector_store: AsyncMock,
    mock_embedder: AsyncMock,
) -> None:
    await manager.index_episodic(
        id="ep:001",
        content="User turned on the kitchen light",
        semantic_key="kitchen light on",
        source="conversation",
        entities=["light.kitchen"],
        timestamp=1711000000.0,
        significance=0.8,
    )

    mock_vector_store.add.assert_awaited_once()
    kwargs = mock_vector_store.add.call_args.kwargs
    assert kwargs["id"] == "ep:001"
    assert kwargs["content"] == "User turned on the kitchen light"
    assert kwargs["semantic_key"] == "kitchen light on"
    assert kwargs["embedding_content"] == [0.1, 0.2, 0.3, 0.4]
    assert kwargs["embedding_semantic"] == [0.1, 0.2, 0.3, 0.4]

    meta: ContextMetadata = kwargs["metadata"]
    assert meta.type == "episodic"
    assert meta.source == "conversation"
    assert meta.entities == "light.kitchen"
    assert meta.timestamp == 1711000000.0
    assert meta.significance == 0.8
    assert meta.retrieval_count == 0
    assert meta.compressed == ""


@pytest.mark.asyncio
async def test_index_episodic_multiple_entities(
    manager: ContextIndexManager,
    mock_vector_store: AsyncMock,
) -> None:
    await manager.index_episodic(
        id="ep:002",
        content="Multiple devices changed",
        semantic_key="devices",
        source="home",
        entities=["light.kitchen", "switch.fan", "sensor.temp"],
        timestamp=0.0,
        significance=0.5,
    )

    meta: ContextMetadata = mock_vector_store.add.call_args.kwargs["metadata"]
    assert meta.entities == "light.kitchen,switch.fan,sensor.temp"


@pytest.mark.asyncio
async def test_index_episodic_falls_back_to_content_when_no_semantic_key(
    manager: ContextIndexManager,
    mock_vector_store: AsyncMock,
    mock_embedder: AsyncMock,
) -> None:
    await manager.index_episodic(
        id="ep:003",
        content="no key provided",
        semantic_key="",
        source="test",
        entities=[],
        timestamp=0.0,
        significance=0.5,
    )

    kwargs = mock_vector_store.add.call_args.kwargs
    assert kwargs["semantic_key"] == "no key provided"
    # embedder called twice with same text
    assert mock_embedder.embed.await_count == 2


# ---------------------------------------------------------------------------
# index_semantic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_semantic_calls_store_add(
    manager: ContextIndexManager,
    mock_vector_store: AsyncMock,
) -> None:
    await manager.index_semantic(
        id="sem:preferences:0",
        content="## Lighting\nUser prefers warm lights",
        source_file="preferences.md",
    )

    mock_vector_store.add.assert_awaited_once()
    kwargs = mock_vector_store.add.call_args.kwargs
    assert kwargs["id"] == "sem:preferences:0"
    assert kwargs["content"] == "## Lighting\nUser prefers warm lights"
    assert kwargs["semantic_key"] == "## Lighting\nUser prefers warm lights"

    meta: ContextMetadata = kwargs["metadata"]
    assert meta.type == "semantic"
    assert meta.source == "preferences.md"
    assert meta.significance == 1.0
    assert meta.compressed == ""


@pytest.mark.asyncio
async def test_index_semantic_uses_same_embedding_for_content_and_key(
    manager: ContextIndexManager,
    mock_vector_store: AsyncMock,
    mock_embedder: AsyncMock,
) -> None:
    await manager.index_semantic(id="sem:0", content="text", source_file="f.md")

    mock_embedder.embed.assert_awaited_once_with("text")
    kwargs = mock_vector_store.add.call_args.kwargs
    assert kwargs["embedding_content"] == kwargs["embedding_semantic"]


# ---------------------------------------------------------------------------
# index_routine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_routine_calls_store_add(
    manager: ContextIndexManager,
    mock_vector_store: AsyncMock,
) -> None:
    await manager.index_routine(
        id="rtn:001",
        content="Every evening user dims lights to 30%",
        confidence=0.9,
    )

    mock_vector_store.add.assert_awaited_once()
    kwargs = mock_vector_store.add.call_args.kwargs
    assert kwargs["id"] == "rtn:001"
    assert kwargs["content"] == "Every evening user dims lights to 30%"

    meta: ContextMetadata = kwargs["metadata"]
    assert meta.type == "routine"
    assert meta.source == "librarian"
    assert meta.significance == 0.9
    assert meta.compressed == ""


# ---------------------------------------------------------------------------
# search — compressed filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_default_excludes_compressed(
    manager: ContextIndexManager,
    mock_vector_store: AsyncMock,
) -> None:
    query = [0.1, 0.2, 0.3, 0.4]
    await manager.search(query_embedding=query)

    mock_vector_store.search.assert_awaited_once_with(
        query_embedding=query,
        limit=10,
        filters={"compressed": ""},
        min_similarity=0.0,
    )


@pytest.mark.asyncio
async def test_search_include_compressed_passes_no_filter(
    manager: ContextIndexManager,
    mock_vector_store: AsyncMock,
) -> None:
    query = [0.1, 0.2, 0.3, 0.4]
    await manager.search(query_embedding=query, include_compressed=True)

    mock_vector_store.search.assert_awaited_once_with(
        query_embedding=query,
        limit=10,
        filters=None,
        min_similarity=0.0,
    )


@pytest.mark.asyncio
async def test_search_passes_through_limit_and_min_similarity(
    manager: ContextIndexManager,
    mock_vector_store: AsyncMock,
) -> None:
    query = [0.1, 0.2, 0.3, 0.4]
    await manager.search(query_embedding=query, limit=5, min_similarity=0.7)

    kwargs = mock_vector_store.search.call_args.kwargs
    assert kwargs["limit"] == 5
    assert kwargs["min_similarity"] == 0.7


@pytest.mark.asyncio
async def test_search_returns_store_results(
    manager: ContextIndexManager,
    mock_vector_store: AsyncMock,
) -> None:
    result = _make_result()
    mock_vector_store.search.return_value = [result]

    results = await manager.search(query_embedding=[0.1, 0.2, 0.3, 0.4])

    assert results == [result]


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_delegates_to_store_delete(
    manager: ContextIndexManager,
    mock_vector_store: AsyncMock,
) -> None:
    await manager.remove("ep:001")
    mock_vector_store.delete.assert_awaited_once_with("ep:001")


@pytest.mark.asyncio
async def test_select_delegates_to_the_store(
    manager: ContextIndexManager,
    mock_vector_store: AsyncMock,
) -> None:
    where = {"significance": Range(below=0.4)}
    mock_vector_store.select.return_value = [_make_result()]

    assert await manager.select(where) == [_make_result()]
    mock_vector_store.select.assert_awaited_once_with(where)


# ---------------------------------------------------------------------------
# reindex_semantic_files
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reindex_semantic_files_indexes_sections(
    tmp_path: Path,
    mock_vector_store: AsyncMock,
    mock_embedder: AsyncMock,
) -> None:
    md = tmp_path / "preferences.md"
    md.write_text("## Lighting\nWarm lights preferred\n\n## Sleep\nBedtime at 22:00\n")

    mgr = ContextIndexManager(
        store=mock_vector_store,
        embedder=mock_embedder,
        semantic_dirs=[tmp_path],
    )
    await mgr.reindex_semantic_files()

    # Two sections indexed
    assert mock_vector_store.add.await_count == 2
    ids = [c.kwargs["id"] for c in mock_vector_store.add.call_args_list]
    assert ids[0] == "sem:preferences:0"
    assert ids[1] == "sem:preferences:1"


@pytest.mark.asyncio
async def test_reindex_semantic_files_uses_correct_source_file(
    tmp_path: Path,
    mock_vector_store: AsyncMock,
    mock_embedder: AsyncMock,
) -> None:
    md = tmp_path / "profile.md"
    md.write_text("## Identity\nAnirudh\n")

    mgr = ContextIndexManager(
        store=mock_vector_store,
        embedder=mock_embedder,
        semantic_dirs=[tmp_path],
    )
    await mgr.reindex_semantic_files()

    meta: ContextMetadata = mock_vector_store.add.call_args.kwargs["metadata"]
    assert meta.source == "profile.md"
    assert meta.type == "semantic"


@pytest.mark.asyncio
async def test_reindex_semantic_files_skips_empty_sections(
    tmp_path: Path,
    mock_vector_store: AsyncMock,
    mock_embedder: AsyncMock,
) -> None:
    md = tmp_path / "sparse.md"
    # File starts with blank lines (preamble section = empty heading + blank body → skipped),
    # followed by two real sections that should be indexed.
    md.write_text("\n\n## A\nContent A\n\n## B\nContent B\n")

    mgr = ContextIndexManager(
        store=mock_vector_store,
        embedder=mock_embedder,
        semantic_dirs=[tmp_path],
    )
    await mgr.reindex_semantic_files()

    # Preamble section (empty heading + blank body) is stripped to "" and skipped.
    # Sections A and B are indexed.
    assert mock_vector_store.add.await_count == 2


@pytest.mark.asyncio
async def test_reindex_semantic_files_skips_nonexistent_dir(
    tmp_path: Path,
    mock_vector_store: AsyncMock,
    mock_embedder: AsyncMock,
) -> None:
    missing = tmp_path / "does_not_exist"
    mgr = ContextIndexManager(
        store=mock_vector_store,
        embedder=mock_embedder,
        semantic_dirs=[missing],
    )
    await mgr.reindex_semantic_files()  # should not raise

    mock_vector_store.add.assert_not_awaited()


@pytest.mark.asyncio
async def test_reindex_semantic_files_multiple_dirs(
    tmp_path: Path,
    mock_vector_store: AsyncMock,
    mock_embedder: AsyncMock,
) -> None:
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    (dir_a / "prefs.md").write_text("## Pref\nValue\n")
    (dir_b / "profile.md").write_text("## Profile\nData\n")

    mgr = ContextIndexManager(
        store=mock_vector_store,
        embedder=mock_embedder,
        semantic_dirs=[dir_a, dir_b],
    )
    await mgr.reindex_semantic_files()

    assert mock_vector_store.add.await_count == 2


# ---------------------------------------------------------------------------
# _parse_markdown_sections
# ---------------------------------------------------------------------------


def test_parse_markdown_sections_splits_on_headings(tmp_path: Path) -> None:
    md = tmp_path / "test.md"
    md.write_text("## Section A\nBody A\n\n## Section B\nBody B\n")
    sections = ContextIndexManager._parse_markdown_sections(md)
    assert len(sections) == 2
    assert sections[0][0] == "## Section A"
    assert "Body A" in sections[0][1]
    assert sections[1][0] == "## Section B"
    assert "Body B" in sections[1][1]


def test_parse_markdown_sections_handles_preamble(tmp_path: Path) -> None:
    md = tmp_path / "test.md"
    md.write_text("Preamble text\n\n## First Heading\nBody\n")
    sections = ContextIndexManager._parse_markdown_sections(md)
    assert len(sections) == 2
    assert sections[0][0] == ""  # empty heading for preamble
    assert "Preamble" in sections[0][1]


def test_parse_markdown_sections_handles_h1_and_h3(tmp_path: Path) -> None:
    md = tmp_path / "test.md"
    md.write_text("# Title\nIntro\n\n### Sub\nDetail\n")
    sections = ContextIndexManager._parse_markdown_sections(md)
    assert sections[0][0] == "# Title"
    assert sections[1][0] == "### Sub"


def test_parse_markdown_sections_empty_file(tmp_path: Path) -> None:
    md = tmp_path / "empty.md"
    md.write_text("")
    sections = ContextIndexManager._parse_markdown_sections(md)
    # Single section with empty heading and empty body
    assert len(sections) == 1
    assert sections[0] == ("", "")


# ---------------------------------------------------------------------------
# recall — deliberate recall across hot and the archive
# ---------------------------------------------------------------------------


def _scored(
    memory_id: str,
    score: float,
    retrieval_count: int = 0,
    *,
    type_: str = "episodic",
    timestamp: float = 0.0,
) -> SearchResult:
    result = _make_result()
    metadata = {"retrieval_count": retrieval_count, "type": type_, "timestamp": timestamp}
    return result.model_copy(
        update={
            "id": memory_id,
            "score": score,
            "metadata": result.metadata.model_copy(update=metadata),
        }
    )


def _store_of(*results: SearchResult) -> AsyncMock:
    """A store that answers as a real one does: its best ``limit`` results, best first."""
    ranked = sorted(results, key=lambda r: r.score, reverse=True)
    store = AsyncMock()
    store.search = AsyncMock(side_effect=lambda *, limit, **_: ranked[:limit])
    return store


def _depths(store: AsyncMock) -> list[int]:
    """The ``limit`` of every search the store was asked for, in order."""
    return [c.kwargs["limit"] for c in store.search.await_args_list]


@pytest.mark.asyncio
async def test_recall_finds_what_decay_moved_to_the_archive(
    mock_vector_store: AsyncMock, mock_embedder: AsyncMock
) -> None:
    archive = AsyncMock()
    archive.search = AsyncMock(return_value=[_scored("moved", 0.9), _scored("both", 0.5)])
    mock_vector_store.search.return_value = [_scored("both", 0.6), _scored("hot", 0.4)]
    manager = ContextIndexManager(mock_vector_store, mock_embedder, archive=archive)

    results = await manager.recall("when did the boiler fail?", limit=2)

    assert [(r.id, r.score) for r in results] == [("moved", 0.9), ("both", 0.6)]
    query = await mock_embedder.embed("when did the boiler fail?")
    hot_call = mock_vector_store.search.await_args.kwargs
    # Deliberate recall reads the whole hot index, compressed entries included.
    assert (hot_call["query_embedding"], hot_call["limit"], hot_call["filters"]) == (query, 2, None)
    assert archive.search.await_args.kwargs["query_embedding"] == query
    assert archive.search.await_args.kwargs["limit"] == 2


@pytest.mark.asyncio
async def test_recall_counts_only_the_hot_hits_it_returns_as_retrieved(
    mock_vector_store: AsyncMock, mock_embedder: AsyncMock
) -> None:
    archive = AsyncMock()
    archive.search = AsyncMock(return_value=[_scored("moved", 0.9)])
    mock_vector_store.search.return_value = [_scored("kept", 0.7, 3), _scored("cut", 0.1)]
    manager = ContextIndexManager(mock_vector_store, mock_embedder, archive=archive)

    await manager.recall("q", limit=2)

    updated = [c.args for c in mock_vector_store.update_metadata.await_args_list]
    assert [(memory_id, fields["retrieval_count"]) for memory_id, fields in updated] == [
        ("kept", 4)
    ]
    archive.update_metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_recall_without_an_archive_is_the_hot_index(
    manager: ContextIndexManager, mock_vector_store: AsyncMock
) -> None:
    mock_vector_store.search.return_value = [_scored("hot", 0.4)]

    assert [r.id for r in await manager.recall("q")] == ["hot"]


@pytest.mark.asyncio
async def test_recall_fails_when_the_archive_does(
    mock_vector_store: AsyncMock, mock_embedder: AsyncMock
) -> None:
    """As EpisodicMemory.recall does: a silently half-empty answer reads as "no such
    memory", and nothing would ever say the archive had stopped answering."""
    archive = AsyncMock()
    archive.search = AsyncMock(side_effect=RuntimeError("cold store latched"))
    manager = ContextIndexManager(mock_vector_store, mock_embedder, archive=archive)

    with pytest.raises(RuntimeError, match="latched"):
        await manager.recall("q")


# ---------------------------------------------------------------------------
# recall — type and time filters apply before the limit (issue #311)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_finds_the_type_asked_for_below_the_limit(mock_embedder: AsyncMock) -> None:
    hot = _store_of(
        _scored("pref-1", 0.9, type_="semantic"),
        _scored("pref-2", 0.8, type_="semantic"),
        _scored("routine", 0.7, type_="routine"),
        _scored("boiler-failed", 0.6),
        _scored("boiler-serviced", 0.5),
    )
    manager = ContextIndexManager(hot, mock_embedder)

    results = await manager.recall("boiler", limit=2, types=["episodic"])

    assert [r.id for r in results] == ["boiler-failed", "boiler-serviced"]


@pytest.mark.asyncio
async def test_recall_finds_recent_memories_below_older_ones(mock_embedder: AsyncMock) -> None:
    now = datetime.now(UTC)
    old = (now - timedelta(days=40)).timestamp()
    hot = _store_of(
        _scored("old-1", 0.9, timestamp=old),
        _scored("old-2", 0.8, timestamp=old),
        _scored("recent", 0.7, timestamp=(now - timedelta(days=1)).timestamp()),
        # Semantic sections and routines carry timestamp 0: timeless, so never too old.
        _scored("timeless", 0.6, type_="semantic"),
        _scored("old-3", 0.5, timestamp=old),
    )
    manager = ContextIndexManager(hot, mock_embedder)

    results = await manager.recall("q", limit=2, since=now - timedelta(days=7))

    assert [r.id for r in results] == ["recent", "timeless"]


@pytest.mark.asyncio
async def test_recall_ranks_an_unread_hot_match_above_a_weaker_archived_one(
    mock_embedder: AsyncMock,
) -> None:
    """Having ``limit`` matches in hand is not enough to stop: the archive's match is
    found first, but hot has not yet read down to a better one."""
    hot = _store_of(
        _scored("pref-1", 0.95, type_="semantic"),
        _scored("pref-2", 0.9, type_="semantic"),
        _scored("hot-match", 0.7),
    )
    archive = _store_of(_scored("cold-match", 0.5))
    manager = ContextIndexManager(hot, mock_embedder, archive=archive)

    results = await manager.recall("q", limit=1, types=["episodic"])

    assert [r.id for r in results] == ["hot-match"]
    assert _depths(hot) == [1, 2, 4]
    # Nothing the archive has not handed back can outrank cold-match, its lowest.
    assert _depths(archive) == [1]


@pytest.mark.asyncio
async def test_recall_without_filters_searches_each_store_once(mock_embedder: AsyncMock) -> None:
    hot = _store_of(*(_scored(f"hot-{i}", 0.9 - i / 10) for i in range(5)))
    archive = _store_of(*(_scored(f"cold-{i}", 0.85 - i / 10) for i in range(5)))
    manager = ContextIndexManager(hot, mock_embedder, archive=archive)

    results = await manager.recall("q", limit=3)

    assert [r.id for r in results] == ["hot-0", "cold-0", "hot-1"]
    assert _depths(hot) == _depths(archive) == [3]


@pytest.mark.asyncio
async def test_recall_reads_on_until_every_store_runs_out(mock_embedder: AsyncMock) -> None:
    """Nothing matches: each search doubles until the store hands back fewer than it
    was asked for, which is the store saying it has nothing else."""
    hot = _store_of(*(_scored(f"pref-{i}", 0.9 - i / 10, type_="semantic") for i in range(5)))
    manager = ContextIndexManager(hot, mock_embedder)

    assert await manager.recall("q", limit=2, types=["routine"]) == []
    assert _depths(hot) == [2, 4, 8]


@pytest.mark.asyncio
async def test_recall_leaves_the_archive_out_when_the_types_exclude_episodic(
    mock_embedder: AsyncMock,
) -> None:
    """Decay moves only episodic memories, so the archive holds nothing else."""
    hot = _store_of(_scored("routine", 0.6, type_="routine"))
    archive = _store_of(_scored("cold", 0.9))
    manager = ContextIndexManager(hot, mock_embedder, archive=archive)

    results = await manager.recall("q", types=["routine"])

    assert [r.id for r in results] == ["routine"]
    archive.search.assert_not_awaited()


@pytest.mark.asyncio
async def test_recall_counts_only_the_matches_it_returns_as_retrieved(
    mock_embedder: AsyncMock,
) -> None:
    hot = _store_of(
        _scored("pref", 0.9, type_="semantic"),
        _scored("kept", 0.6, 2),
        _scored("cut", 0.5),
    )
    manager = ContextIndexManager(hot, mock_embedder)

    await manager.recall("q", limit=1, types=["episodic"])

    updated = [c.args for c in hot.update_metadata.await_args_list]
    assert [(memory_id, fields["retrieval_count"]) for memory_id, fields in updated] == [
        ("kept", 3)
    ]


@pytest.mark.asyncio
async def test_recall_with_no_room_returns_nothing(mock_embedder: AsyncMock) -> None:
    hot = _store_of(_scored("hot", 0.9))
    manager = ContextIndexManager(hot, mock_embedder)

    assert await manager.recall("q", limit=0) == []
    hot.search.assert_not_awaited()
