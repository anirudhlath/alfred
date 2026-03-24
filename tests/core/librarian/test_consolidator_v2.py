"""Tests for Librarian v2: significance scoring, semantic keys, conflict resolution, decay."""

from __future__ import annotations

import datetime
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from core.librarian.consolidator import ConflictItem, Librarian
from core.memory.schemas import EpisodicEntry, SignificanceScore
from core.memory.vector_store import ContextMetadata, SearchResult

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


# ---------------------------------------------------------------------------
# Part A: Semantic conflict resolution tests
# ---------------------------------------------------------------------------


def _make_entry(summary: str = "User prefers 72°F") -> EpisodicEntry:
    return EpisodicEntry(
        id="test-id",
        timestamp=datetime.datetime(2026, 3, 19, tzinfo=datetime.UTC),
        source="reflex",
        summary=summary,
        entities=[],
        significance=SignificanceScore(overall=0.5),
    )


@pytest.mark.asyncio
async def test_apply_conflict_resolutions_confirm_skips(tmp_path: Path) -> None:
    """Confirm items produce no changes."""
    librarian = _make_librarian()
    librarian._preferences_dir = tmp_path

    existing = "---\ndomain: general\n---\n\n# Learned Preferences\n\n- Prefers 72°F\n"
    resolutions = [
        ConflictItem(type="confirm", line="Prefers 72°F", explanation="Consistent"),
    ]
    updated, changes = librarian._apply_conflict_resolutions(existing, resolutions, "2026-03-24")
    assert changes == 0
    assert updated == existing


@pytest.mark.asyncio
async def test_apply_conflict_resolutions_contradict_updates_line(tmp_path: Path) -> None:
    """Contradict items replace the old line and add provenance."""
    librarian = _make_librarian()
    librarian._preferences_dir = tmp_path

    existing = "---\ndomain: general\n---\n\n# Learned Preferences\n\n- Prefers 72°F\n"
    resolutions = [
        ConflictItem(
            type="contradict",
            old="Prefers 72°F",
            new="Prefers 68°F",
            explanation="Last 5 observations show 68°F",
        ),
    ]
    updated, changes = librarian._apply_conflict_resolutions(existing, resolutions, "2026-03-24")
    assert changes == 1
    assert "Prefers 68°F" in updated
    assert "Revised on 2026-03-24" in updated
    assert "Prefers 72°F" not in updated or "was 'Prefers 72°F'" in updated


@pytest.mark.asyncio
async def test_apply_conflict_resolutions_new_appends(tmp_path: Path) -> None:
    """New items are appended to the content."""
    librarian = _make_librarian()
    librarian._preferences_dir = tmp_path

    existing = "---\ndomain: general\n---\n\n# Learned Preferences\n\n- Prefers 72°F\n"
    resolutions = [
        ConflictItem(
            type="new",
            content="Prefers warm lighting in evening",
            explanation="Observed 3 times this week",
        ),
    ]
    updated, changes = librarian._apply_conflict_resolutions(existing, resolutions, "2026-03-24")
    assert changes == 1
    assert "Prefers warm lighting in evening" in updated
    assert "Prefers 72°F" in updated  # original preserved


@pytest.mark.asyncio
async def test_update_semantic_memory_confirm_makes_no_change(tmp_path: Path) -> None:
    """When all resolutions are 'confirm', learned.md should remain unchanged."""
    librarian = _make_librarian()
    librarian._preferences_dir = tmp_path

    learned = tmp_path / "learned.md"
    original = "---\ndomain: general\n---\n\n# Learned Preferences\n\n- Prefers 72°F\n"
    learned.write_text(original)

    # Pass 1: extract returns a preference
    extract_response = AsyncMock()
    extract_response.choices = [
        AsyncMock(message=AsyncMock(content="PREFERENCE: temperature: Prefers 72°F"))
    ]

    # Pass 2: conflict resolution returns confirm
    conflict_response = AsyncMock()
    conflict_payload = [{"type": "confirm", "line": "Prefers 72°F", "explanation": "Consistent"}]
    conflict_response.choices = [AsyncMock(message=AsyncMock(content=json.dumps(conflict_payload)))]

    with patch("litellm.acompletion", side_effect=[extract_response, conflict_response]):
        result = await librarian._update_semantic_memory([_make_entry()])

    # confirm → no changes → returns 0
    assert result == 0
    assert learned.read_text() == original


@pytest.mark.asyncio
async def test_update_semantic_memory_new_preference_appended(tmp_path: Path) -> None:
    """New preference is written to learned.md."""
    librarian = _make_librarian()
    librarian._preferences_dir = tmp_path

    learned = tmp_path / "learned.md"
    original = "---\ndomain: general\n---\n\n# Learned Preferences\n\n- Prefers 72°F\n"
    learned.write_text(original)

    extract_response = AsyncMock()
    extract_response.choices = [
        AsyncMock(
            message=AsyncMock(content="PREFERENCE: lighting: Prefers warm lighting in evening")
        )
    ]

    conflict_response = AsyncMock()
    conflict_payload = [
        {
            "type": "new",
            "content": "Prefers warm lighting in evening",
            "explanation": "Observed 3x",
        }
    ]
    conflict_response.choices = [AsyncMock(message=AsyncMock(content=json.dumps(conflict_payload)))]

    with patch("litellm.acompletion", side_effect=[extract_response, conflict_response]):
        result = await librarian._update_semantic_memory([_make_entry()])

    assert result == 1
    content = learned.read_text()
    assert "warm lighting in evening" in content


@pytest.mark.asyncio
async def test_update_semantic_memory_contradict_updates_preference(tmp_path: Path) -> None:
    """Contradicted preference is replaced with provenance note."""
    librarian = _make_librarian()
    librarian._preferences_dir = tmp_path

    learned = tmp_path / "learned.md"
    original = "---\ndomain: general\n---\n\n# Learned Preferences\n\n- Prefers 72°F\n"
    learned.write_text(original)

    extract_response = AsyncMock()
    extract_response.choices = [
        AsyncMock(message=AsyncMock(content="PREFERENCE: temperature: Prefers 68°F"))
    ]

    conflict_response = AsyncMock()
    conflict_payload = [
        {
            "type": "contradict",
            "old": "Prefers 72°F",
            "new": "Prefers 68°F",
            "explanation": "Last 5 observations show 68°F",
        }
    ]
    conflict_response.choices = [AsyncMock(message=AsyncMock(content=json.dumps(conflict_payload)))]

    with patch("litellm.acompletion", side_effect=[extract_response, conflict_response]):
        result = await librarian._update_semantic_memory([_make_entry()])

    assert result == 1
    content = learned.read_text()
    assert "Prefers 68°F" in content
    assert "Revised on" in content


@pytest.mark.asyncio
async def test_update_semantic_memory_fallback_on_conflict_llm_failure(tmp_path: Path) -> None:
    """When conflict resolution LLM fails, falls back to append-only behavior."""
    librarian = _make_librarian()
    librarian._preferences_dir = tmp_path

    extract_response = AsyncMock()
    extract_response.choices = [
        AsyncMock(message=AsyncMock(content="PREFERENCE: temp: Prefers 70°F"))
    ]

    # Conflict LLM raises an error
    with patch(
        "litellm.acompletion",
        side_effect=[extract_response, Exception("LLM error")],
    ):
        result = await librarian._update_semantic_memory([_make_entry()])

    # Fallback: should still write (append-only)
    assert result == 1
    learned = tmp_path / "learned.md"
    assert "Prefers 70°F" in learned.read_text()


@pytest.mark.asyncio
async def test_update_semantic_memory_no_api_key_returns_zero(tmp_path: Path) -> None:
    """Without API key, _update_semantic_memory returns 0."""
    librarian = _make_librarian(api_key="")
    librarian._preferences_dir = tmp_path
    result = await librarian._update_semantic_memory([_make_entry()])
    assert result == 0


@pytest.mark.asyncio
async def test_update_semantic_memory_none_output_returns_zero(tmp_path: Path) -> None:
    """When LLM returns NONE, _update_semantic_memory returns 0."""
    librarian = _make_librarian()
    librarian._preferences_dir = tmp_path

    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock(message=AsyncMock(content="NONE"))]

    with patch("litellm.acompletion", return_value=mock_response):
        result = await librarian._update_semantic_memory([_make_entry()])

    assert result == 0


@pytest.mark.asyncio
async def test_update_semantic_memory_creates_learned_md_if_missing(tmp_path: Path) -> None:
    """learned.md is created with a header skeleton when it doesn't exist."""
    librarian = _make_librarian()
    librarian._preferences_dir = tmp_path

    extract_response = AsyncMock()
    extract_response.choices = [
        AsyncMock(message=AsyncMock(content="PREFERENCE: food: Likes pasta"))
    ]

    conflict_response = AsyncMock()
    conflict_payload = [{"type": "new", "content": "Likes pasta", "explanation": "Observed"}]
    conflict_response.choices = [AsyncMock(message=AsyncMock(content=json.dumps(conflict_payload)))]

    with patch("litellm.acompletion", side_effect=[extract_response, conflict_response]):
        result = await librarian._update_semantic_memory([_make_entry()])

    assert result == 1
    learned = tmp_path / "learned.md"
    content = learned.read_text()
    assert "Likes pasta" in content
    assert "Learned Preferences" in content


# ---------------------------------------------------------------------------
# Part B: Contextual decay tests
# ---------------------------------------------------------------------------


def _make_search_result(
    entry_id: str,
    timestamp: float,
    significance: float,
    retrieval_count: int,
    entry_type: str = "episodic",
) -> SearchResult:
    return SearchResult(
        id=entry_id,
        score=0.9,
        content="test content",
        semantic_key="test key",
        metadata=ContextMetadata(
            type=entry_type,
            source="reflex",
            entities="",
            timestamp=timestamp,
            significance=significance,
            retrieval_count=retrieval_count,
        ),
    )


@pytest.mark.asyncio
async def test_apply_decay_migrates_old_low_significance_entries() -> None:
    """Old, low-significance, unread entries should be migrated to cold."""
    episodic_memory = AsyncMock()
    context_index = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()

    librarian = _make_librarian(episodic_memory=episodic_memory, context_index=context_index)

    # Entry: 30 days old, low significance=0.1, retrieval_count=0
    # pressure = 30 * (1-0.1) * (1/(0+1)) = 30 * 0.9 * 1.0 = 27.0 > threshold=1.0
    old_ts = datetime.datetime.now(datetime.UTC).timestamp() - (30 * 86400)
    old_entry = _make_search_result("old-entry-1", old_ts, significance=0.1, retrieval_count=0)

    context_index._embedder = AsyncMock()
    context_index._embedder.embed = AsyncMock(return_value=[0.1] * 10)
    context_index.search = AsyncMock(return_value=[old_entry])

    migrated = await librarian._apply_decay(decay_migration_threshold=1.0)

    assert migrated == 1
    episodic_memory.migrate_to_cold.assert_awaited_once_with("old-entry-1")


@pytest.mark.asyncio
async def test_apply_decay_spares_high_significance_entries() -> None:
    """High-significance entries should NOT be migrated regardless of age."""
    episodic_memory = AsyncMock()
    context_index = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()

    librarian = _make_librarian(episodic_memory=episodic_memory, context_index=context_index)

    # Entry: 30 days old, high significance=0.95, retrieval_count=0
    # pressure = 30 * (1-0.95) * 1.0 = 30 * 0.05 = 1.5 but threshold=5.0 → spared
    old_ts = datetime.datetime.now(datetime.UTC).timestamp() - (30 * 86400)
    important_entry = _make_search_result(
        "important-1", old_ts, significance=0.95, retrieval_count=0
    )

    context_index._embedder = AsyncMock()
    context_index._embedder.embed = AsyncMock(return_value=[0.1] * 10)
    context_index.search = AsyncMock(return_value=[important_entry])

    migrated = await librarian._apply_decay(decay_migration_threshold=5.0)

    assert migrated == 0
    episodic_memory.migrate_to_cold.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_decay_spares_frequently_retrieved_entries() -> None:
    """Frequently-retrieved entries resist migration due to low pressure."""
    episodic_memory = AsyncMock()
    context_index = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()

    librarian = _make_librarian(episodic_memory=episodic_memory, context_index=context_index)

    # Entry: 30 days old, low sig=0.1, but retrieval_count=100
    # pressure = 30 * 0.9 * (1/101) ≈ 0.267 < threshold=1.0 → spared
    old_ts = datetime.datetime.now(datetime.UTC).timestamp() - (30 * 86400)
    popular_entry = _make_search_result("popular-1", old_ts, significance=0.1, retrieval_count=100)

    context_index._embedder = AsyncMock()
    context_index._embedder.embed = AsyncMock(return_value=[0.1] * 10)
    context_index.search = AsyncMock(return_value=[popular_entry])

    migrated = await librarian._apply_decay(decay_migration_threshold=1.0)

    assert migrated == 0
    episodic_memory.migrate_to_cold.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_decay_skips_non_episodic_entries() -> None:
    """Semantic and routine entries should be skipped during decay."""
    episodic_memory = AsyncMock()
    context_index = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()

    librarian = _make_librarian(episodic_memory=episodic_memory, context_index=context_index)

    old_ts = datetime.datetime.now(datetime.UTC).timestamp() - (30 * 86400)
    semantic_entry = _make_search_result(
        "sem-1", old_ts, significance=0.1, retrieval_count=0, entry_type="semantic"
    )
    routine_entry = _make_search_result(
        "rout-1", old_ts, significance=0.1, retrieval_count=0, entry_type="routine"
    )

    context_index._embedder = AsyncMock()
    context_index._embedder.embed = AsyncMock(return_value=[0.1] * 10)
    context_index.search = AsyncMock(return_value=[semantic_entry, routine_entry])

    migrated = await librarian._apply_decay(decay_migration_threshold=1.0)

    assert migrated == 0
    episodic_memory.migrate_to_cold.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_decay_handles_search_failure_gracefully() -> None:
    """If context_index search fails, _apply_decay returns 0 without raising."""
    episodic_memory = AsyncMock()
    context_index = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()

    librarian = _make_librarian(episodic_memory=episodic_memory, context_index=context_index)

    context_index._embedder = AsyncMock()
    context_index._embedder.embed = AsyncMock(side_effect=Exception("embed failed"))
    context_index.search = AsyncMock(side_effect=Exception("search failed"))

    migrated = await librarian._apply_decay()

    assert migrated == 0
    episodic_memory.migrate_to_cold.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_decay_migration_error_continues_for_other_entries() -> None:
    """If migration fails for one entry, others are still processed."""
    episodic_memory = AsyncMock()
    context_index = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()

    librarian = _make_librarian(episodic_memory=episodic_memory, context_index=context_index)

    old_ts = datetime.datetime.now(datetime.UTC).timestamp() - (30 * 86400)
    entry1 = _make_search_result("entry-1", old_ts, significance=0.1, retrieval_count=0)
    entry2 = _make_search_result("entry-2", old_ts, significance=0.1, retrieval_count=0)

    context_index._embedder = AsyncMock()
    context_index._embedder.embed = AsyncMock(return_value=[0.1] * 10)
    context_index.search = AsyncMock(return_value=[entry1, entry2])

    # First migration fails, second succeeds
    episodic_memory.migrate_to_cold.side_effect = [Exception("migrate failed"), None]

    migrated = await librarian._apply_decay(decay_migration_threshold=1.0)

    # Only second entry migrated successfully
    assert migrated == 1


@pytest.mark.asyncio
async def test_apply_decay_skips_zero_timestamp_entries() -> None:
    """Entries with timestamp=0 should be skipped (semantic/metadata entries)."""
    episodic_memory = AsyncMock()
    context_index = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()

    librarian = _make_librarian(episodic_memory=episodic_memory, context_index=context_index)

    zero_ts_entry = _make_search_result("zero-ts", 0.0, significance=0.0, retrieval_count=0)

    context_index._embedder = AsyncMock()
    context_index._embedder.embed = AsyncMock(return_value=[0.1] * 10)
    context_index.search = AsyncMock(return_value=[zero_ts_entry])

    migrated = await librarian._apply_decay(decay_migration_threshold=1.0)

    assert migrated == 0
    episodic_memory.migrate_to_cold.assert_not_awaited()


# ---------------------------------------------------------------------------
# Part C: DecayScheduler deprecation test
# ---------------------------------------------------------------------------


def test_decay_scheduler_emits_deprecation_warning() -> None:
    """DecayScheduler should emit a DeprecationWarning on instantiation."""
    import warnings

    from core.memory.episodic.decay import DecayScheduler

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        DecayScheduler()

    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
    assert any("Task 17" in str(w.message) for w in caught)
