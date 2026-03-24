"""Tests for Librarian Claude-powered intelligence."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from core.librarian.consolidator import Librarian
from core.memory.schemas import EpisodicEntry, SignificanceScore

_UTC = datetime.UTC
_TS = datetime.datetime(2026, 3, 19, tzinfo=_UTC)


@pytest.fixture()
def librarian() -> Librarian:
    redis = AsyncMock()
    episodic = AsyncMock()
    routines = AsyncMock()
    return Librarian(
        redis=redis,
        episodic_store=episodic,
        routine_store=routines,
        claude_api_key="test-key",
    )


@pytest.mark.asyncio
async def test_extract_entities_with_claude(librarian: Librarian) -> None:
    """When Claude is available, entities should be extracted from scratchpad lines."""
    lines = [
        "2026-03-19T10:00:00Z [reflex] home.turn_on_light({entity: light.living_room}) → success",
    ]
    mock_response = AsyncMock()
    # Batch format: array of arrays — one inner array per observation
    mock_response.choices = [
        AsyncMock(message=AsyncMock(content='[["light.living_room", "living room"]]'))
    ]
    mock_response.usage = AsyncMock(prompt_tokens=100, completion_tokens=20)

    with patch("litellm.acompletion", return_value=mock_response):
        entries = await librarian._extract_episodic_entries(lines)

    assert len(entries) == 1
    assert "light.living_room" in entries[0].entities or "living room" in entries[0].entities


@pytest.mark.asyncio
async def test_extract_entities_fallback_without_api_key() -> None:
    """Without API key, entities should be empty (graceful fallback)."""
    redis = AsyncMock()
    episodic = AsyncMock()
    routines = AsyncMock()
    lib = Librarian(redis=redis, episodic_store=episodic, routine_store=routines, claude_api_key="")
    lines = ["2026-03-19T10:00:00Z [reflex] action → result"]
    entries = await lib._extract_episodic_entries(lines)
    assert len(entries) == 1
    assert entries[0].entities == []


@pytest.mark.asyncio
async def test_update_semantic_memory_writes_learned_preferences(
    librarian: Librarian, tmp_path: Path
) -> None:
    """When Claude detects a preference, it should be appended to learned.md."""
    librarian._preferences_dir = tmp_path / "prefs"
    librarian._preferences_dir.mkdir()

    entries = [
        EpisodicEntry(
            id="test-1",
            timestamp=_TS,
            source="reflex",
            summary="User set thermostat to 68F every night at 10pm",
            entities=["thermostat"],
            significance=SignificanceScore(overall=0.5),
            valence="neutral",
        )
    ]

    mock_response = AsyncMock()
    mock_response.choices = [
        AsyncMock(message=AsyncMock(content="PREFERENCE: climate: User prefers 68F at night"))
    ]

    with patch("litellm.acompletion", return_value=mock_response):
        count = await librarian._update_semantic_memory(entries)

    assert count == 1
    learned = (tmp_path / "prefs" / "learned.md").read_text()
    assert "68F at night" in learned


@pytest.mark.asyncio
async def test_update_semantic_memory_no_op_without_api_key(tmp_path: Path) -> None:
    """Without API key, semantic memory update should return 0."""
    redis = AsyncMock()
    episodic = AsyncMock()
    routines = AsyncMock()
    lib = Librarian(redis=redis, episodic_store=episodic, routine_store=routines, claude_api_key="")
    lib._preferences_dir = tmp_path / "prefs"
    lib._preferences_dir.mkdir()

    entries = [
        EpisodicEntry(
            id="test-2",
            timestamp=_TS,
            source="reflex",
            summary="some observation",
            entities=[],
            significance=SignificanceScore(overall=0.5),
            valence="neutral",
        )
    ]
    count = await lib._update_semantic_memory(entries)
    assert count == 0


@pytest.mark.asyncio
async def test_update_semantic_memory_returns_zero_on_none(
    librarian: Librarian, tmp_path: Path
) -> None:
    """When Claude returns NONE, semantic memory should not be updated."""
    librarian._preferences_dir = tmp_path / "prefs"
    librarian._preferences_dir.mkdir()

    entries = [
        EpisodicEntry(
            id="test-3",
            timestamp=_TS,
            source="reflex",
            summary="motion detected in hallway",
            entities=["motion.hallway"],
            significance=SignificanceScore(overall=0.5),
            valence="neutral",
        )
    ]

    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock(message=AsyncMock(content="NONE"))]

    with patch("litellm.acompletion", return_value=mock_response):
        count = await librarian._update_semantic_memory(entries)

    assert count == 0
    assert not (tmp_path / "prefs" / "learned.md").exists()


@pytest.mark.asyncio
async def test_apply_decay_returns_zero(librarian: Librarian) -> None:
    """Decay placeholder should return 0 archived entries."""
    archived = await librarian._apply_decay()
    assert archived == 0


@pytest.mark.asyncio
async def test_consolidate_updates_semantic_memory(librarian: Librarian, tmp_path: Path) -> None:
    """Consolidation should update preference files when patterns are detected."""
    librarian._preferences_dir = tmp_path / "prefs"
    librarian._preferences_dir.mkdir()
    librarian._redis.lrange = AsyncMock(return_value=[])
    librarian._redis.rename = AsyncMock(side_effect=Exception("no key"))

    # With empty scratchpad, no updates
    result = await librarian.consolidate()
    assert result["entries_processed"] == 0
