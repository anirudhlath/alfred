"""Metric definitions: pressure as the Librarian sees it, stuck streaks, exact ranks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from core.librarian.consolidator import Librarian, migration_pressure
from core.memory.vector_store import ContextMetadata, SearchResult
from evals.memory.env import StoredVectors
from evals.memory.metrics import (
    COMPRESSED_SOURCE,
    DECAY_SEARCH_QUERY,
    EligibilityTracker,
    _exact_rank,
    decay_pressure,
    is_eligible,
    names_target,
)

NOW = datetime(2026, 3, 1, tzinfo=UTC)
DAY = 86400.0


def _meta(**overrides: object) -> ContextMetadata:
    fields: dict[str, object] = {
        "type": "episodic",
        "source": "observation",
        "entities": "",
        "timestamp": NOW.timestamp() - 30 * DAY,
        "significance": 0.1,
        "retrieval_count": 0,
        "last_retrieved": 0.0,
    }
    fields.update(overrides)
    return ContextMetadata.model_validate(fields)


def test_decay_search_query_is_read_from_the_librarian() -> None:
    assert DECAY_SEARCH_QUERY == "general context memory event"


def test_never_retrieved_entries_use_their_age_for_recency() -> None:
    assert decay_pressure(_meta(), NOW.timestamp()) == pytest.approx(
        migration_pressure(30.0, 0.1, 0, 30.0)
    )


def test_retrieved_entries_use_the_last_retrieval() -> None:
    meta = _meta(retrieval_count=3, last_retrieved=NOW.timestamp() - 2 * DAY)
    assert decay_pressure(meta, NOW.timestamp()) == pytest.approx(
        migration_pressure(30.0, 0.1, 3, 2.0)
    )


@pytest.mark.parametrize(
    "meta", [_meta(type="semantic"), _meta(type="routine"), _meta(timestamp=0.0)]
)
def test_entries_the_decay_pass_skips_have_no_pressure(meta: ContextMetadata) -> None:
    assert decay_pressure(meta, NOW.timestamp()) is None


async def test_decay_pressure_agrees_with_apply_decay() -> None:
    """The eval's eligibility must be exactly the set _apply_decay would migrate."""
    metas = [
        _meta(timestamp=NOW.timestamp() - age * DAY, significance=sig)
        for age in (5.0, 16.0, 17.0, 29.0, 45.0)
        for sig in (0.105, 0.23, 0.355, 0.45)
    ]
    results = [
        SearchResult(id=f"m{i}", score=0.5, content="c", semantic_key="k", metadata=m)
        for i, m in enumerate(metas)
    ]
    episodic_memory = AsyncMock()
    context_index = AsyncMock()
    context_index.search_text = AsyncMock(return_value=results)
    librarian = Librarian(
        redis=AsyncMock(),
        episodic_memory=episodic_memory,
        routine_store=MagicMock(),
        significance_scorer=AsyncMock(),
        context_index=context_index,
    )

    migrated = await librarian._apply_decay(decay_migration_threshold=0.2, now=NOW)

    eligible = [r for r in results if is_eligible(r, NOW.timestamp(), 0.2)]
    assert migrated == len(eligible) > 0
    moved = {call.args[0].id for call in episodic_memory.copy_to_cold_and_remove.await_args_list}
    assert moved == {r.id for r in eligible}


def test_tracker_counts_consecutive_checks_and_resets_on_a_gap() -> None:
    tracker = EligibilityTracker()
    tracker.update({"a", "b"})
    tracker.update({"a"})
    tracker.update({"a", "b"})
    assert tracker.stuck(3) == ["a"]
    assert tracker.stuck(1) == ["a", "b"]


def test_exact_rank_ignores_ties_below_the_target() -> None:
    content = np.asarray([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.6, 0.8]], dtype=np.float32)
    vectors = StoredVectors(
        ids=["dup1", "dup2", "target", "near"], content=content, semantic=content
    )

    assert _exact_rank(vectors, [0.0, 1.0], "target") == (1, pytest.approx(1.0))
    rank, score = _exact_rank(vectors, [1.0, 0.0], "near")
    assert rank == 3  # two identical duplicates score strictly higher
    assert score == pytest.approx(0.6)
    assert _exact_rank(vectors, [1.0, 0.0], "gone") == (None, None)


def test_eligibility_uses_the_injected_threshold() -> None:
    result = SearchResult(id="m", score=0.0, content="c", semantic_key="k", metadata=_meta())
    pressure = decay_pressure(result.metadata, NOW.timestamp())
    assert pressure is not None
    assert is_eligible(result, NOW.timestamp(), pressure - 0.01)
    assert not is_eligible(result, NOW.timestamp(), pressure)
    assert not is_eligible(result, (NOW - timedelta(days=29)).timestamp(), 0.2)


def test_a_memory_answers_a_probe_as_itself_or_folded_into_a_summary() -> None:
    content = "[observation] sensor.washer_status: idle → error E21"
    assert names_target("det-04", content, "det-04", "observation", content)
    summary = f"[observation] sensor.washer_status: running → idle | {content}"
    assert names_target("det-04", content, "f3a1", COMPRESSED_SOURCE, summary)
    # Same text in an ordinary memory is a different event, not the target.
    assert not names_target("det-04", content, "obs-12-0001", "observation", summary)
    assert not names_target("det-04", content, "f3a1", COMPRESSED_SOURCE, "unrelated")
