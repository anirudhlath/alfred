"""Tests for ContextIndexManager."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.memory.context_index import ContextIndexManager
from core.memory.vector_store import ContextMetadata, SearchResult

if TYPE_CHECKING:
    from pathlib import Path
    from unittest.mock import AsyncMock

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
