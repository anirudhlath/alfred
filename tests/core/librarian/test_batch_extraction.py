"""Tests for batched entity extraction in the Librarian."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.librarian.consolidator import Librarian


@pytest.fixture()
def librarian() -> Librarian:
    return Librarian(
        redis=AsyncMock(),
        episodic_store=AsyncMock(),
        routine_store=AsyncMock(),
        claude_api_key="test-key",
    )


@pytest.mark.asyncio
async def test_batch_extraction_single_llm_call(librarian: Librarian) -> None:
    """Multiple scratchpad lines should produce exactly ONE LLM call for entities."""
    lines = [
        "2026-03-19T10:00:00Z [reflex] home.turn_on_light({entity: light.living_room}) → success",
        "2026-03-19T10:05:00Z [reflex] home.set_temperature"
        "({entity: climate.main, temp: 72}) → success",
        "2026-03-19T10:10:00Z [conscious] user='good morning'"
        " → 42 chars (actions=none, tokens=100+20)",
    ]

    mock_response = AsyncMock()
    mock_response.choices = [
        AsyncMock(
            message=AsyncMock(
                content=(
                    '[["light.living_room", "living room"], '
                    '["climate.main", "main thermostat"], '
                    '["none"]]'
                )
            )
        )
    ]
    mock_response.usage = AsyncMock(prompt_tokens=200, completion_tokens=50)

    with patch("litellm.acompletion", return_value=mock_response) as mock_llm:
        entries = await librarian._extract_episodic_entries(lines)

    # Exactly ONE LLM call for all 3 lines
    assert mock_llm.call_count == 1
    assert len(entries) == 3
    assert "light.living_room" in entries[0].entities


@pytest.mark.asyncio
async def test_batch_extraction_no_api_key() -> None:
    """Without API key, entities should be empty (no LLM call)."""
    lib = Librarian(
        redis=AsyncMock(),
        episodic_store=AsyncMock(),
        routine_store=AsyncMock(),
        claude_api_key="",
    )
    lines = ["2026-03-19T10:00:00Z [reflex] action → result"]
    entries = await lib._extract_episodic_entries(lines)
    assert len(entries) == 1
    assert entries[0].entities == []
