"""Tests for the Librarian consolidation agent."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.librarian.consolidator import Librarian
from core.memory.schemas import SignificanceScore


def _make_scorer_mock() -> AsyncMock:
    scorer = AsyncMock()
    scorer.score.return_value = SignificanceScore(
        overall=0.4, safety=0.0, novelty=0.5, personal=0.3, emotional=0.2
    )
    return scorer


@pytest.fixture
def mock_deps() -> dict[str, AsyncMock | MagicMock | str]:
    context_index = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()
    return {
        "redis": AsyncMock(),
        "episodic_memory": AsyncMock(),
        "routine_store": MagicMock(),
        "significance_scorer": _make_scorer_mock(),
        "context_index": context_index,
        "preferences_dir": "/tmp/test_prefs",
        "profile_dir": "/tmp/test_profile",
    }


@pytest.mark.asyncio
async def test_drain_scratchpad_normal_path(
    mock_deps: dict[str, AsyncMock | MagicMock | str],
) -> None:
    """Normal path: no leftover processing key, rename succeeds, drain entries."""
    redis_mock: AsyncMock = mock_deps["redis"]  # type: ignore[assignment]
    entries = [
        b"2026-03-19T10:00:00Z [reflex] smart_home.dim_lights({room: living}) -> success",
        b"2026-03-19T10:05:00Z [conscious] Briefing delivered to sir",
    ]
    # First lrange (leftover check) returns empty, second lrange (drain) returns entries
    redis_mock.lrange.side_effect = [[], entries]
    redis_mock.rename.return_value = None
    redis_mock.delete.return_value = None

    librarian = Librarian(**mock_deps)  # type: ignore[arg-type]
    result = await librarian._drain_scratchpad()

    assert len(result) == 2
    redis_mock.rename.assert_called_once()
    # delete is NOT called in _drain_scratchpad — it happens in consolidate()
    # after episodic writes succeed (crash safety)
    redis_mock.delete.assert_not_called()


@pytest.mark.asyncio
async def test_drain_scratchpad_crash_recovery(
    mock_deps: dict[str, AsyncMock | MagicMock | str],
) -> None:
    """Crash recovery: leftover processing key exists from previous run."""
    redis_mock: AsyncMock = mock_deps["redis"]  # type: ignore[assignment]
    leftover = [b"2026-03-19T09:00:00Z [reflex] leftover entry from crash"]
    # First lrange (leftover check) returns data — skip rename
    redis_mock.lrange.side_effect = [leftover, leftover]
    redis_mock.delete.return_value = None

    librarian = Librarian(**mock_deps)  # type: ignore[arg-type]
    result = await librarian._drain_scratchpad()

    assert len(result) == 1
    assert "leftover" in result[0]
    redis_mock.rename.assert_not_called()


@pytest.mark.asyncio
async def test_drain_scratchpad_empty_queue(
    mock_deps: dict[str, AsyncMock | MagicMock | str],
) -> None:
    """Empty queue: rename fails (key doesn't exist), returns empty list."""
    redis_mock: AsyncMock = mock_deps["redis"]  # type: ignore[assignment]
    redis_mock.lrange.return_value = []
    redis_mock.rename.side_effect = Exception("no such key")

    librarian = Librarian(**mock_deps)  # type: ignore[arg-type]
    result = await librarian._drain_scratchpad()

    assert result == []


@pytest.mark.asyncio
async def test_consolidate_empty_scratchpad(
    mock_deps: dict[str, AsyncMock | MagicMock | str],
) -> None:
    redis_mock: AsyncMock = mock_deps["redis"]  # type: ignore[assignment]
    redis_mock.lrange.return_value = []
    redis_mock.rename.side_effect = Exception("no such key")

    librarian = Librarian(**mock_deps)  # type: ignore[arg-type]
    result = await librarian.consolidate()
    assert result["entries_processed"] == 0


@pytest.mark.asyncio
async def test_consolidate_writes_episodic_entries(
    mock_deps: dict[str, AsyncMock | MagicMock | str],
) -> None:
    """Non-empty consolidation: verifies episodic_memory.write is called per entry."""
    redis_mock: AsyncMock = mock_deps["redis"]  # type: ignore[assignment]
    episodic_mock: AsyncMock = mock_deps["episodic_memory"]  # type: ignore[assignment]

    lines = [
        b"2026-03-19T10:00:00Z [reflex] dim lights -> success",
        b"2026-03-19T10:05:00Z [conscious] briefing delivered",
        b"2026-03-19T10:10:00Z [trigger] morning routine fired",
    ]
    # Normal drain path: empty leftover, then entries
    redis_mock.lrange.side_effect = [[], lines]
    redis_mock.rename.return_value = None
    redis_mock.delete.return_value = None

    librarian = Librarian(**mock_deps)  # type: ignore[arg-type]
    result = await librarian.consolidate()

    assert result["entries_processed"] == 3
    assert result["episodic_created"] == 3
    assert episodic_mock.write.call_count == 3

    # Verify the entries have correct source extraction
    first_call_entry = episodic_mock.write.call_args_list[0][0][0]
    assert first_call_entry.source == "reflex"
    assert "dim lights" in first_call_entry.summary
