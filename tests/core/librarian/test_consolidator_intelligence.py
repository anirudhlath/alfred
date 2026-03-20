"""Tests for Librarian Claude-powered intelligence."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.librarian.consolidator import Librarian
from core.memory.schemas import EpisodicEntry


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
    mock_response.choices = [
        AsyncMock(message=AsyncMock(content='["light.living_room", "living room"]'))
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
    lib = Librarian(
        redis=redis, episodic_store=episodic, routine_store=routines, claude_api_key=""
    )
    lines = ["2026-03-19T10:00:00Z [reflex] action → result"]
    entries = await lib._extract_episodic_entries(lines)
    assert len(entries) == 1
    assert entries[0].entities == []
