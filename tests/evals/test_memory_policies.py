"""Decay policies differ only in candidate source and threshold; the decay code is real."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.memory.vector_store import ContextMetadata, SearchResult
from evals.memory.policies import (
    MASTER_THRESHOLD,
    POLICIES,
    BranchDecay,
    NoDecay,
    OracleDecay,
    RecordingIndex,
    ScanAllHotIndex,
    policy_factory,
)
from tests.evals.memory_fakes import FakeRedis, HashEmbedder

NOW = datetime(2026, 3, 1, tzinfo=UTC)


def _result(memory_id: str, age_days: float, memory_type: str = "episodic") -> SearchResult:
    return SearchResult(
        id=memory_id,
        score=0.3,
        content=memory_id,
        semantic_key=memory_id,
        metadata=ContextMetadata(
            type=memory_type,
            source="observation",
            entities="",
            timestamp=NOW.timestamp() - age_days * 86400 if memory_type == "episodic" else 0.0,
            significance=0.105,
            retrieval_count=0,
        ),
    )


def _env(librarian: Any) -> Any:
    return SimpleNamespace(
        hot=AsyncMock(),
        embedder=HashEmbedder(),
        redis=FakeRedis(),
        librarian=MagicMock(return_value=librarian),
    )


def test_registry_holds_the_three_baselines_in_report_order() -> None:
    assert list(POLICIES)[:3] == ["no_decay", "branch", "oracle"]
    with pytest.raises(ValueError, match="unknown policy"):
        policy_factory("nope")


def test_no_decay_runs_master_threshold_whatever_is_configured() -> None:
    policy = NoDecay(_env(AsyncMock()), threshold=0.2)
    assert policy.threshold == MASTER_THRESHOLD == 1.0


def test_oracle_selects_by_scan_and_branch_by_search() -> None:
    env = _env(AsyncMock())
    assert isinstance(OracleDecay(env, 0.2)._index, ScanAllHotIndex)
    branch_index = BranchDecay(env, 0.2)._index
    assert type(branch_index) is RecordingIndex


async def test_a_pass_reports_what_selection_handed_the_pressure_filter() -> None:
    librarian = AsyncMock()
    librarian._apply_decay = AsyncMock(return_value=1)
    policy = BranchDecay(_env(librarian), threshold=0.2)
    policy._index.last = [
        _result("old", 40.0),
        _result("young", 2.0),
        _result("section", 0.0, memory_type="semantic"),
    ]

    stats = await policy.run_pass(NOW)

    librarian._apply_decay.assert_awaited_once_with(decay_migration_threshold=0.2, now=NOW)
    assert (stats.migrated, stats.candidates, stats.candidates_episodic) == (1, 3, 2)
    assert stats.candidates_eligible == 1


async def test_recording_index_keeps_the_real_search_results() -> None:
    store = AsyncMock()
    store.search = AsyncMock(return_value=[_result("a", 30.0)])
    index = RecordingIndex(store, HashEmbedder())

    results = await index.search_text("general context memory event", limit=500)

    assert [r.id for r in results] == [r.id for r in index.last] == ["a"]
    assert store.search.await_args.kwargs["limit"] == 500


async def test_scan_index_ignores_query_limit_and_floor() -> None:
    redis = FakeRedis(
        {f"ctx:m{i}": {"type": "episodic", "timestamp": 1.0, "content": f"m{i}"} for i in range(25)}
    )
    index = ScanAllHotIndex(AsyncMock(), HashEmbedder(), redis)  # type: ignore[arg-type]

    results = await index.search_text("anything", limit=1, min_similarity=0.99)

    assert len(results) == 25
