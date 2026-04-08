"""Tests for memory schemas."""

from __future__ import annotations

from datetime import UTC, datetime

from core.memory.schemas import (
    EpisodicEntry,
    EpisodicResult,
    RoutineSpec,
    RoutineStep,
    SignificanceScore,
)


def test_episodic_entry_creation() -> None:
    entry = EpisodicEntry(
        id="ep-1",
        timestamp=datetime.now(UTC),
        source="conversation",
        summary="Sir asked for a briefing",
        entities=["calendar", "weather"],
        significance=SignificanceScore(overall=0.5),
        valence="neutral",
    )
    assert entry.source == "conversation"


def test_significance_score_defaults() -> None:
    score = SignificanceScore(overall=0.5)
    assert score.safety == 0.0
    assert score.novelty == 0.0
    assert score.personal == 0.0
    assert score.emotional == 0.0
    assert score.source == "heuristic"


def test_significance_score_full() -> None:
    score = SignificanceScore(
        overall=0.8,
        safety=0.9,
        novelty=0.3,
        personal=0.7,
        emotional=0.6,
        source="librarian",
    )
    assert score.overall == 0.8


def test_episodic_entry_with_significance() -> None:
    entry = EpisodicEntry(
        id="ep-1",
        timestamp=datetime.now(UTC),
        source="conversation",
        summary="test",
        entities=["light.kitchen"],
        significance=SignificanceScore(overall=0.5),
    )
    assert entry.significance.overall == 0.5
    assert entry.retrieval_count == 0
    assert entry.compressed_into is None


def test_episodic_result() -> None:
    entry = EpisodicEntry(
        id="ep-1",
        timestamp=datetime.now(UTC),
        source="conversation",
        summary="test",
        entities=[],
        significance=SignificanceScore(overall=0.5),
    )
    result = EpisodicResult(entry=entry, score=0.85, source_store="hot")
    assert result.score == 0.85


def test_routine_spec_last_suggested() -> None:
    spec = RoutineSpec(
        name="test",
        trigger_pattern="8pm",
        steps=[],
        confidence=0.7,
        learned_from=[],
        state="candidate",
    )
    assert spec.last_suggested is None


def test_routine_spec_defaults() -> None:
    step = RoutineStep(description="Dim living room lights to 30%")
    routine = RoutineSpec(
        name="evening_movie",
        trigger_pattern="every evening around 8pm",
        steps=[step],
        confidence=0.7,
        learned_from=["ep-1", "ep-2"],
        state="candidate",
    )
    assert routine.consecutive_misses == 0
    assert routine.last_hit is None
    assert step.action is None


def test_cost_state_defaults() -> None:
    """CostState moved to core.conscious.cost — verify import still works."""
    from core.conscious.cost import CostState

    cost = CostState(date="2026-03-19", spend_usd=2.50, cap_usd=5.0)
    assert cost.alert_sent is False
