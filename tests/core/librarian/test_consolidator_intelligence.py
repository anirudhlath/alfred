"""Tests for Librarian Claude-powered intelligence."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from core.librarian.consolidator import Librarian
from core.memory.schemas import EpisodicEntry, SignificanceScore

_UTC = datetime.UTC
_TS = datetime.datetime(2026, 3, 19, tzinfo=_UTC)


def _make_scorer_mock() -> AsyncMock:
    scorer = AsyncMock()
    scorer.score.return_value = SignificanceScore(
        overall=0.4, safety=0.0, novelty=0.5, personal=0.3, emotional=0.2
    )
    return scorer


@pytest.fixture()
def librarian() -> Librarian:
    redis = AsyncMock()
    episodic_memory = AsyncMock()
    routines = MagicMock()
    context_index = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()
    return Librarian(
        redis=redis,
        episodic_memory=episodic_memory,
        routine_store=routines,
        significance_scorer=_make_scorer_mock(),
        context_index=context_index,
        claude_api_key="test-key",
    )


@pytest.mark.asyncio
async def test_extract_entities_with_claude(librarian: Librarian) -> None:
    """When Claude is available, entities should be extracted from scratchpad lines."""
    lines = [
        "2026-03-19T10:00:00Z [reflex] home.turn_on_light({entity: light.living_room}) -> success",
    ]
    mock_response = AsyncMock()
    import json

    llm_payload = [
        {
            "entities": ["light.living_room", "living room"],
            "significance": {"safety": 0.0, "novelty": 0.3, "personal": 0.5, "emotional": 0.2},
            "semantic_key": "Living room light turned on",
        }
    ]
    mock_response.choices = [AsyncMock(message=AsyncMock(content=json.dumps(llm_payload)))]
    mock_response.usage = AsyncMock(prompt_tokens=100, completion_tokens=20)

    with patch("litellm.acompletion", return_value=mock_response):
        pairs = await librarian._extract_episodic_entries(lines)

    assert len(pairs) == 1
    entry, _ = pairs[0]
    assert "light.living_room" in entry.entities or "living room" in entry.entities


@pytest.mark.asyncio
async def test_extract_entities_fallback_without_api_key() -> None:
    """Without API key, entities should be empty (graceful fallback)."""
    redis = AsyncMock()
    episodic_memory = AsyncMock()
    context_index = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()
    lib = Librarian(
        redis=redis,
        episodic_memory=episodic_memory,
        routine_store=MagicMock(),
        significance_scorer=_make_scorer_mock(),
        context_index=context_index,
        claude_api_key="",
    )
    lines = ["2026-03-19T10:00:00Z [reflex] action -> result"]
    pairs = await lib._extract_episodic_entries(lines)
    assert len(pairs) == 1
    entry, llm_sig = pairs[0]
    assert entry.entities == []
    assert llm_sig == {}


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
    context_index = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()
    lib = Librarian(
        redis=redis,
        episodic_memory=AsyncMock(),
        routine_store=MagicMock(),
        significance_scorer=_make_scorer_mock(),
        context_index=context_index,
        claude_api_key="",
    )
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
async def test_consolidate_empty_scratchpad_no_updates(
    librarian: Librarian, tmp_path: Path
) -> None:
    """Consolidation should short-circuit when scratchpad is empty."""
    librarian._preferences_dir = tmp_path / "prefs"
    librarian._preferences_dir.mkdir()
    librarian._redis.lrange = AsyncMock(return_value=[])
    librarian._redis.rename = AsyncMock(side_effect=Exception("no key"))

    # With empty scratchpad, no updates
    result = await librarian.consolidate()
    assert result["entries_processed"] == 0
