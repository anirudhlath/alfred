"""Tests for batched entity extraction in the Librarian."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.librarian.consolidator import Librarian
from core.memory.schemas import SignificanceScore


def _make_scorer_mock() -> AsyncMock:
    scorer = AsyncMock()
    scorer.score.return_value = SignificanceScore(
        overall=0.4, safety=0.0, novelty=0.5, personal=0.3, emotional=0.2
    )
    return scorer


def _make_librarian(*, api_key: str = "test-key") -> Librarian:
    context_index = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()
    return Librarian(
        redis=AsyncMock(),
        episodic_memory=AsyncMock(),
        routine_store=MagicMock(),
        significance_scorer=_make_scorer_mock(),
        context_index=context_index,
        claude_api_key=api_key,
    )


@pytest.mark.asyncio
async def test_batch_extraction_single_llm_call() -> None:
    """Multiple scratchpad lines should produce exactly ONE LLM call."""
    librarian = _make_librarian()
    lines = [
        "2026-03-19T10:00:00Z [reflex] home.turn_on_light({entity: light.living_room}) -> ok",
        "2026-03-19T10:05:00Z [reflex] home.set_temperature({entity: climate.main}) -> ok",
        "2026-03-19T10:10:00Z [conscious] user='good morning' -> 42 chars",
    ]

    llm_payload = [
        {
            "entities": ["light.living_room", "living room"],
            "significance": {"safety": 0.0, "novelty": 0.3, "personal": 0.5, "emotional": 0.2},
            "semantic_key": "Living room light on",
        },
        {
            "entities": ["climate.main", "main thermostat"],
            "significance": {"safety": 0.0, "novelty": 0.1, "personal": 0.3, "emotional": 0.1},
            "semantic_key": "Thermostat set to 72",
        },
        {
            "entities": [],
            "significance": {"safety": 0.0, "novelty": 0.0, "personal": 0.8, "emotional": 0.3},
            "semantic_key": "Good morning greeting",
        },
    ]
    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock(message=AsyncMock(content=json.dumps(llm_payload)))]
    mock_response.usage = AsyncMock(prompt_tokens=200, completion_tokens=50)

    with patch("litellm.acompletion", return_value=mock_response) as mock_llm:
        pairs = await librarian._extract_episodic_entries(lines)

    # Exactly ONE LLM call for all 3 lines
    assert mock_llm.call_count == 1
    assert len(pairs) == 3
    entry, _ = pairs[0]
    assert "light.living_room" in entry.entities


@pytest.mark.asyncio
async def test_batch_extraction_no_api_key() -> None:
    """Without API key, entities should be empty (no LLM call)."""
    lib = _make_librarian(api_key="")
    lines = ["2026-03-19T10:00:00Z [reflex] action -> result"]
    pairs = await lib._extract_episodic_entries(lines)
    assert len(pairs) == 1
    entry, llm_sig = pairs[0]
    assert entry.entities == []
    assert llm_sig == {}
