"""Tests for memory schemas."""

from __future__ import annotations

from datetime import UTC, datetime

from core.memory.schemas import EpisodicEntry, RoutineSpec, RoutineStep


def test_episodic_entry_creation() -> None:
    entry = EpisodicEntry(
        id="ep-1",
        timestamp=datetime.now(UTC),
        source="conversation",
        summary="Sir asked for a briefing",
        entities=["calendar", "weather"],
        valence="neutral",
    )
    assert entry.source == "conversation"


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
