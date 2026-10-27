"""Tests for the Librarian's LLM-powered intelligence."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from core.librarian.consolidator import Librarian
from core.memory.schemas import EpisodicEntry, SignificanceScore
from shared.prompts.names import LIBRARIAN_EXTRACT
from shared.prompts.store import PromptStore
from tests.llm_fakes import Script
from tests.shared.prompts.fakes import FakeLangfuse

_UTC = datetime.UTC
_TS = datetime.datetime(2026, 3, 19, tzinfo=_UTC)


def _make_scorer_mock() -> AsyncMock:
    scorer = AsyncMock()
    scorer.score.return_value = SignificanceScore(
        overall=0.4, safety=0.0, novelty=0.5, personal=0.3, emotional=0.2
    )
    return scorer


def _librarian(script: Script | None = None) -> Librarian:
    context_index = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()
    return Librarian(
        redis=AsyncMock(),
        episodic_memory=AsyncMock(),
        routine_store=MagicMock(),
        significance_scorer=_make_scorer_mock(),
        context_index=context_index,
        model=script.model if script else None,
    )


@pytest.mark.asyncio
async def test_extract_entities_with_a_model() -> None:
    """With a model, entities should be extracted from scratchpad lines."""
    llm_payload = [
        {
            "entities": ["light.living_room", "living room"],
            "significance": {"safety": 0.0, "novelty": 0.3, "personal": 0.5, "emotional": 0.2},
            "semantic_key": "Living room light turned on",
        }
    ]
    librarian = _librarian(Script(llm_payload))
    lines = [
        "2026-03-19T10:00:00Z [reflex] home.turn_on_light({entity: light.living_room}) -> success",
    ]

    pairs = await librarian._extract_episodic_entries(lines)

    assert len(pairs) == 1
    entry, _ = pairs[0]
    assert "light.living_room" in entry.entities or "living room" in entry.entities


@pytest.mark.asyncio
async def test_extract_entities_fallback_without_a_model() -> None:
    """Without a model, entities should be empty (graceful fallback)."""
    lib = _librarian()
    lines = ["2026-03-19T10:00:00Z [reflex] action -> result"]
    pairs = await lib._extract_episodic_entries(lines)
    assert len(pairs) == 1
    entry, llm_sig = pairs[0]
    assert entry.entities == []
    assert llm_sig == {}


@pytest.mark.asyncio
async def test_update_semantic_memory_writes_learned_preferences(tmp_path: Path) -> None:
    """When the model detects a preference, it should be appended to learned.md."""
    librarian = _librarian(
        Script([{"domain": "climate", "observation": "User prefers 68F at night"}], [])
    )
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

    count = await librarian._update_semantic_memory(entries)

    assert count == 1
    learned = (tmp_path / "prefs" / "learned.md").read_text()
    assert "68F at night" in learned


@pytest.mark.asyncio
async def test_a_pass_uses_the_prompt_stores_text(tmp_path: Path) -> None:
    """The extract step sends what the prompt store serves, not a baked-in constant."""
    store = PromptStore(langfuse=FakeLangfuse({LIBRARIAN_EXTRACT: "Extract carefully."}))
    script = Script([{"domain": "climate", "observation": "User prefers 68F at night"}], [])
    context_index = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()
    librarian = Librarian(
        redis=AsyncMock(),
        episodic_memory=AsyncMock(),
        routine_store=MagicMock(),
        significance_scorer=_make_scorer_mock(),
        context_index=context_index,
        model=script.model,
        prompts=store,
    )
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

    count = await librarian._update_semantic_memory(entries)

    assert count == 1
    learned = (tmp_path / "prefs" / "learned.md").read_text()
    assert "68F at night" in learned
    assert "Extract carefully." in script.instructions()


@pytest.mark.asyncio
async def test_update_semantic_memory_no_op_without_a_model(tmp_path: Path) -> None:
    """Without a model, semantic memory update should return 0."""
    lib = _librarian()
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
async def test_update_semantic_memory_returns_zero_on_none(tmp_path: Path) -> None:
    """When the model finds no preference, semantic memory should not be updated."""
    librarian = _librarian(Script([]))
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

    count = await librarian._update_semantic_memory(entries)

    assert count == 0
    assert not (tmp_path / "prefs" / "learned.md").exists()


@pytest.mark.asyncio
async def test_apply_decay_returns_zero() -> None:
    """Decay placeholder should return 0 archived entries."""
    librarian = _librarian()
    archived = await librarian._apply_decay()
    assert archived == 0


@pytest.mark.asyncio
async def test_consolidate_empty_scratchpad_no_updates(tmp_path: Path) -> None:
    """Consolidation should short-circuit when scratchpad is empty."""
    librarian = _librarian()
    librarian._preferences_dir = tmp_path / "prefs"
    librarian._preferences_dir.mkdir()
    librarian._redis.lrange = AsyncMock(return_value=[])
    librarian._redis.rename = AsyncMock(side_effect=Exception("no key"))

    # With empty scratchpad, no updates
    result = await librarian.consolidate()
    assert result["entries_processed"] == 0


def test_the_librarian_passes_its_timeout_to_its_agents() -> None:
    context_index = AsyncMock()
    librarian = Librarian(
        redis=AsyncMock(),
        episodic_memory=AsyncMock(),
        routine_store=MagicMock(),
        significance_scorer=_make_scorer_mock(),
        context_index=context_index,
        model=Script().model,
        timeout_s=7.0,
    )
    assert librarian._agents is not None
    assert librarian._agents.timeout_s == 7.0
