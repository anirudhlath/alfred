"""Tests for upgraded Librarian v2 — significance scoring and semantic keys."""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.librarian.consolidator import Librarian
from core.memory.schemas import SignificanceScore

_UTC = datetime.UTC
_TS = datetime.datetime(2026, 3, 19, tzinfo=_UTC)


def _make_librarian(
    *,
    api_key: str = "test-key",
    scorer: Any = None,
    context_index: Any = None,
    episodic_memory: Any = None,
) -> Librarian:
    redis = AsyncMock()
    if episodic_memory is None:
        episodic_memory = AsyncMock()
    if scorer is None:
        scorer = AsyncMock()
        scorer.score.return_value = SignificanceScore(
            overall=0.4,
            safety=0.0,
            novelty=0.5,
            personal=0.3,
            emotional=0.2,
        )
    if context_index is None:
        context_index = AsyncMock()
        context_index.reindex_semantic_files = AsyncMock()
    return Librarian(
        redis=redis,
        episodic_memory=episodic_memory,
        routine_store=MagicMock(),
        significance_scorer=scorer,
        context_index=context_index,
        claude_api_key=api_key,
    )


# ---------------------------------------------------------------------------
# _analyse_batch tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyse_batch_returns_entities_significance_semantic_key() -> None:
    """LLM call should return entities, significance dims, and semantic key."""
    librarian = _make_librarian()
    summaries = ["User turned on light.living_room at 10pm"]
    llm_payload = [
        {
            "entities": ["light.living_room"],
            "significance": {"safety": 0.0, "novelty": 0.3, "personal": 0.5, "emotional": 0.2},
            "semantic_key": "Evening lighting preference in living room",
        }
    ]
    mock_response = AsyncMock()
    mock_response.choices = [
        AsyncMock(message=AsyncMock(content=str(llm_payload).replace("'", '"')))
    ]
    import json

    mock_response.choices[0].message.content = json.dumps(llm_payload)

    with patch("litellm.acompletion", return_value=mock_response):
        results = await librarian._analyse_batch(summaries)

    assert len(results) == 1
    result = results[0]
    assert result["entities"] == ["light.living_room"]
    assert result["significance"]["safety"] == 0.0
    assert result["significance"]["novelty"] == 0.3
    assert result["significance"]["personal"] == 0.5
    assert result["significance"]["emotional"] == 0.2
    assert result["semantic_key"] == "Evening lighting preference in living room"


@pytest.mark.asyncio
async def test_analyse_batch_fallback_without_api_key() -> None:
    """Without API key, _analyse_batch returns list of empty dicts."""
    librarian = _make_librarian(api_key="")
    results = await librarian._analyse_batch(["some event"])
    assert results == [{}]


@pytest.mark.asyncio
async def test_analyse_batch_fallback_on_llm_error() -> None:
    """On LLM failure, _analyse_batch returns empty dicts (graceful degradation)."""
    librarian = _make_librarian()
    with patch("litellm.acompletion", side_effect=Exception("network error")):
        results = await librarian._analyse_batch(["event 1", "event 2"])
    assert results == [{}, {}]


@pytest.mark.asyncio
async def test_analyse_batch_pads_short_response() -> None:
    """If LLM returns fewer items than summaries, missing slots are filled with {}."""
    librarian = _make_librarian()
    mock_response = AsyncMock()
    import json

    single_item = [{"entities": ["x"], "significance": {}, "semantic_key": "x"}]
    mock_response.choices = [AsyncMock(message=AsyncMock(content=json.dumps(single_item)))]
    with patch("litellm.acompletion", return_value=mock_response):
        results = await librarian._analyse_batch(["a", "b", "c"])
    assert len(results) == 3
    assert results[1] == {}
    assert results[2] == {}


# ---------------------------------------------------------------------------
# _extract_episodic_entries tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_episodic_entries_populates_semantic_key() -> None:
    """When LLM returns a semantic key, it should be set on the EpisodicEntry."""
    librarian = _make_librarian()
    lines = ["2026-03-19T10:00:00Z [reflex] home.turn_on_light({entity: light.living_room}) -> ok"]
    import json

    llm_payload = [
        {
            "entities": ["light.living_room"],
            "significance": {"safety": 0.0, "novelty": 0.3, "personal": 0.5, "emotional": 0.2},
            "semantic_key": "Evening lighting preference in living room",
        }
    ]
    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock(message=AsyncMock(content=json.dumps(llm_payload)))]

    with patch("litellm.acompletion", return_value=mock_response):
        pairs = await librarian._extract_episodic_entries(lines)

    assert len(pairs) == 1
    entry, llm_sig = pairs[0]
    assert entry.semantic_key == "Evening lighting preference in living room"
    assert "light.living_room" in entry.entities
    assert llm_sig["safety"] == 0.0
    assert llm_sig["personal"] == 0.5


@pytest.mark.asyncio
async def test_extract_episodic_entries_fallback_empty_analysis() -> None:
    """Without API key, entries still have empty semantic_key and empty llm_sig."""
    librarian = _make_librarian(api_key="")
    lines = ["2026-03-19T10:00:00Z [reflex] action -> result"]
    pairs = await librarian._extract_episodic_entries(lines)
    assert len(pairs) == 1
    entry, llm_sig = pairs[0]
    assert entry.entities == []
    assert entry.semantic_key == ""
    assert llm_sig == {}


# ---------------------------------------------------------------------------
# consolidate() integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidate_writes_to_episodic_memory_not_store() -> None:
    """consolidate() must call episodic_memory.write(), never episodic_store.write()."""
    episodic_memory = AsyncMock()
    scorer = AsyncMock()
    scorer.score.return_value = SignificanceScore(
        overall=0.4, safety=0.0, novelty=0.5, personal=0.3, emotional=0.2
    )
    context_index = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()

    librarian = _make_librarian(
        api_key="",
        episodic_memory=episodic_memory,
        scorer=scorer,
        context_index=context_index,
    )

    lines = [b"2026-03-19T10:00:00Z [reflex] dim lights -> success"]
    librarian._redis.lrange.side_effect = [[], lines]
    librarian._redis.rename.return_value = None
    librarian._redis.delete.return_value = None

    result = await librarian.consolidate()

    assert result["episodic_created"] == 1
    episodic_memory.write.assert_awaited_once()
    # Verify a SignificanceScore was passed
    call_args = episodic_memory.write.call_args
    assert isinstance(call_args[0][1], SignificanceScore)


@pytest.mark.asyncio
async def test_consolidate_merges_heuristic_and_llm_significance() -> None:
    """LLM significance dims should be max-merged with heuristic scores."""
    episodic_memory = AsyncMock()
    scorer = AsyncMock()
    scorer.score.return_value = SignificanceScore(
        overall=0.35,
        safety=0.0,
        novelty=0.1,
        personal=0.2,
        emotional=0.1,
        source="heuristic",
    )
    context_index = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()
    librarian = _make_librarian(
        episodic_memory=episodic_memory,
        scorer=scorer,
        context_index=context_index,
    )

    import json

    llm_payload = [
        {
            "entities": ["smoke.detector"],
            "significance": {"safety": 0.9, "novelty": 0.8, "personal": 0.3, "emotional": 0.7},
            "semantic_key": "Smoke detector alert",
        }
    ]
    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock(message=AsyncMock(content=json.dumps(llm_payload)))]

    lines = [b"2026-03-19T10:00:00Z [trigger] smoke detector fired -> alert sent"]
    librarian._redis.lrange.side_effect = [[], lines]
    librarian._redis.rename.return_value = None
    librarian._redis.delete.return_value = None

    with patch("litellm.acompletion", return_value=mock_response):
        await librarian.consolidate()

    call_args = episodic_memory.write.call_args
    sig: SignificanceScore = call_args[0][1]
    # LLM values (0.9, 0.8, 0.7) are higher than heuristic (0.0, 0.1, 0.1)
    assert sig.safety == 0.9
    assert sig.novelty == 0.8
    assert sig.emotional == 0.7
    # personal: max(0.2, 0.3) = 0.3
    assert sig.personal == 0.3
    assert sig.source == "librarian"


@pytest.mark.asyncio
async def test_consolidate_reindexes_semantic_files_at_end() -> None:
    """consolidate() must call context_index.reindex_semantic_files() at end of cycle."""
    context_index = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()
    librarian = _make_librarian(api_key="", context_index=context_index)

    lines = [b"2026-03-19T10:00:00Z [reflex] action -> ok"]
    librarian._redis.lrange.side_effect = [[], lines]
    librarian._redis.rename.return_value = None
    librarian._redis.delete.return_value = None

    await librarian.consolidate()

    context_index.reindex_semantic_files.assert_awaited_once()


@pytest.mark.asyncio
async def test_consolidate_reindex_not_called_on_empty_scratchpad() -> None:
    """reindex_semantic_files should NOT be called when scratchpad is empty."""
    context_index = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()
    librarian = _make_librarian(api_key="", context_index=context_index)

    librarian._redis.lrange.return_value = []
    librarian._redis.rename.side_effect = Exception("no such key")

    result = await librarian.consolidate()

    assert result["entries_processed"] == 0
    context_index.reindex_semantic_files.assert_not_awaited()


@pytest.mark.asyncio
async def test_consolidate_heuristic_only_when_no_api_key() -> None:
    """When no API key, significance comes from heuristic scorer alone (source=heuristic)."""
    episodic_memory = AsyncMock()
    scorer = AsyncMock()
    scorer.score.return_value = SignificanceScore(
        overall=0.4, safety=0.0, novelty=0.5, personal=0.3, emotional=0.2, source="heuristic"
    )
    context_index = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()
    librarian = _make_librarian(
        api_key="",
        episodic_memory=episodic_memory,
        scorer=scorer,
        context_index=context_index,
    )

    lines = [b"2026-03-19T10:00:00Z [reflex] dim lights -> success"]
    librarian._redis.lrange.side_effect = [[], lines]
    librarian._redis.rename.return_value = None
    librarian._redis.delete.return_value = None

    await librarian.consolidate()

    call_args = episodic_memory.write.call_args
    sig: SignificanceScore = call_args[0][1]
    assert sig.source == "heuristic"
