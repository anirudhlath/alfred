"""Tests for the Librarian consolidation agent."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.librarian.consolidator import Librarian


@pytest.fixture
def mock_deps() -> dict[str, AsyncMock | MagicMock | str]:
    return {
        "redis": AsyncMock(),
        "episodic_store": AsyncMock(),
        "routine_store": MagicMock(),
        "preferences_dir": "/tmp/test_prefs",
        "profile_dir": "/tmp/test_profile",
    }


@pytest.mark.asyncio
async def test_drain_scratchpad(mock_deps: dict[str, AsyncMock | MagicMock | str]) -> None:
    redis_mock: AsyncMock = mock_deps["redis"]  # type: ignore[assignment]
    redis_mock.lrange.return_value = [
        b"2026-03-19T10:00:00Z [reflex] smart_home.dim_lights({room: living}) -> success",
        b"2026-03-19T10:05:00Z [conscious] Briefing delivered to sir",
    ]
    redis_mock.ltrim.return_value = None

    librarian = Librarian(**mock_deps)  # type: ignore[arg-type]
    entries = await librarian._drain_scratchpad()
    assert len(entries) == 2


@pytest.mark.asyncio
async def test_consolidate_empty_scratchpad(
    mock_deps: dict[str, AsyncMock | MagicMock | str],
) -> None:
    redis_mock: AsyncMock = mock_deps["redis"]  # type: ignore[assignment]
    redis_mock.lrange.return_value = []
    librarian = Librarian(**mock_deps)  # type: ignore[arg-type]
    result = await librarian.consolidate()
    assert result["entries_processed"] == 0
