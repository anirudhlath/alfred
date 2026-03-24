"""Tests for SignificanceScorer — heuristic significance scoring (the Amygdala)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from core.memory.schemas import EpisodicEntry, SignificanceScore
from core.memory.significance import SignificanceScorer
from shared.config import AlfredConfig
from shared.streams import ENTITY_FREQUENCY_KEY


def _make_entry(
    source: str = "conversation",
    summary: str = "User asked about the weather.",
    entities: list[str] | None = None,
) -> EpisodicEntry:
    """Helper to create a minimal EpisodicEntry for testing."""
    return EpisodicEntry(
        id="test-id",
        timestamp=datetime(2026, 3, 24, 12, 0, 0),
        source=source,
        summary=summary,
        entities=entities if entities is not None else [],
        significance=SignificanceScore(overall=0.0),
    )


@pytest.fixture
def config() -> AlfredConfig:
    return AlfredConfig()


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    # Default: zincrby returns 1.0 (first time seen)
    redis.zincrby.return_value = 1.0
    return redis


@pytest.fixture
def scorer(mock_redis: AsyncMock, config: AlfredConfig) -> SignificanceScorer:
    return SignificanceScorer(redis=mock_redis, config=config)


# ---------------------------------------------------------------------------
# Safety dimension
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safety_urgent_trigger_returns_1(scorer: SignificanceScorer) -> None:
    entry = _make_entry(source="trigger", summary="Urgent: smoke detected in kitchen.")
    score = await scorer._score_safety(entry)
    assert score == 1.0


@pytest.mark.asyncio
async def test_safety_critical_trigger_returns_1(scorer: SignificanceScorer) -> None:
    entry = _make_entry(source="trigger", summary="Critical water leak under sink.")
    score = await scorer._score_safety(entry)
    assert score == 1.0


@pytest.mark.asyncio
async def test_safety_normal_trigger_returns_0_3(scorer: SignificanceScorer) -> None:
    entry = _make_entry(source="trigger", summary="Motion detected in hallway.")
    score = await scorer._score_safety(entry)
    assert score == 0.3


@pytest.mark.asyncio
async def test_safety_conversation_returns_0(scorer: SignificanceScorer) -> None:
    entry = _make_entry(source="conversation", summary="User said hello.")
    score = await scorer._score_safety(entry)
    assert score == 0.0


@pytest.mark.asyncio
async def test_safety_integration_returns_0(scorer: SignificanceScorer) -> None:
    entry = _make_entry(source="integration", summary="Calendar event retrieved.")
    score = await scorer._score_safety(entry)
    assert score == 0.0


# ---------------------------------------------------------------------------
# Novelty dimension
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_novelty_first_time_entity_returns_1(
    scorer: SignificanceScorer, mock_redis: AsyncMock
) -> None:
    mock_redis.zincrby.return_value = 1.0  # first occurrence
    entry = _make_entry(entities=["kitchen_light"])
    score = await scorer._score_novelty(entry)
    assert score == 1.0
    mock_redis.zincrby.assert_awaited_once_with(ENTITY_FREQUENCY_KEY, 1, "kitchen_light")


@pytest.mark.asyncio
async def test_novelty_frequent_entity_returns_low_score(
    scorer: SignificanceScorer, mock_redis: AsyncMock
) -> None:
    mock_redis.zincrby.return_value = 10.0  # seen 10 times
    entry = _make_entry(entities=["kitchen_light"])
    score = await scorer._score_novelty(entry)
    assert score == pytest.approx(0.1, abs=1e-3)


@pytest.mark.asyncio
async def test_novelty_second_occurrence_returns_0_5(
    scorer: SignificanceScorer, mock_redis: AsyncMock
) -> None:
    mock_redis.zincrby.return_value = 2.0
    entry = _make_entry(entities=["bedroom_sensor"])
    score = await scorer._score_novelty(entry)
    assert score == pytest.approx(0.5, abs=1e-3)


@pytest.mark.asyncio
async def test_novelty_no_entities_returns_moderate(scorer: SignificanceScorer) -> None:
    entry = _make_entry(entities=[])
    score = await scorer._score_novelty(entry)
    assert score == 0.5


@pytest.mark.asyncio
async def test_novelty_multiple_entities_averaged(
    scorer: SignificanceScorer, mock_redis: AsyncMock
) -> None:
    # First entity seen once (1.0), second seen 4 times (0.25)
    mock_redis.zincrby.side_effect = [1.0, 4.0]
    entry = _make_entry(entities=["entity_a", "entity_b"])
    score = await scorer._score_novelty(entry)
    # Average: (1.0 + 0.25) / 2 = 0.625
    assert score == pytest.approx(0.625, abs=1e-3)
    assert mock_redis.zincrby.await_count == 2


@pytest.mark.asyncio
async def test_novelty_calls_zincrby_for_each_entity(
    scorer: SignificanceScorer, mock_redis: AsyncMock
) -> None:
    mock_redis.zincrby.return_value = 1.0
    entry = _make_entry(entities=["a", "b", "c"])
    await scorer._score_novelty(entry)
    assert mock_redis.zincrby.await_count == 3


# ---------------------------------------------------------------------------
# Personal dimension
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_personal_conversation_returns_0_8(scorer: SignificanceScorer) -> None:
    entry = _make_entry(source="conversation")
    score = scorer._score_personal(entry)
    assert score == 0.8


@pytest.mark.asyncio
async def test_personal_integration_returns_0_5(scorer: SignificanceScorer) -> None:
    entry = _make_entry(source="integration")
    score = scorer._score_personal(entry)
    assert score == 0.5


@pytest.mark.asyncio
async def test_personal_trigger_returns_0_3(scorer: SignificanceScorer) -> None:
    entry = _make_entry(source="trigger")
    score = scorer._score_personal(entry)
    assert score == 0.3


@pytest.mark.asyncio
async def test_personal_system1_action_returns_0_2(scorer: SignificanceScorer) -> None:
    entry = _make_entry(source="system1_action")
    score = scorer._score_personal(entry)
    assert score == 0.2


@pytest.mark.asyncio
async def test_personal_unknown_source_returns_0_3(scorer: SignificanceScorer) -> None:
    entry = _make_entry(source="unknown_source")
    score = scorer._score_personal(entry)
    assert score == 0.3


# ---------------------------------------------------------------------------
# Emotional dimension
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emotional_urgent_returns_0_9(scorer: SignificanceScorer) -> None:
    entry = _make_entry(summary="Urgent: check the door lock.")
    score = scorer._score_emotional(entry)
    assert score == 0.9


@pytest.mark.asyncio
async def test_emotional_critical_returns_0_9(scorer: SignificanceScorer) -> None:
    entry = _make_entry(summary="Critical system failure detected.")
    score = scorer._score_emotional(entry)
    assert score == 0.9


@pytest.mark.asyncio
async def test_emotional_emergency_returns_0_9(scorer: SignificanceScorer) -> None:
    entry = _make_entry(summary="Emergency: gas leak suspected.")
    score = scorer._score_emotional(entry)
    assert score == 0.9


@pytest.mark.asyncio
async def test_emotional_warning_returns_0_6(scorer: SignificanceScorer) -> None:
    entry = _make_entry(summary="Warning: battery low on sensor.")
    score = scorer._score_emotional(entry)
    assert score == 0.6


@pytest.mark.asyncio
async def test_emotional_important_returns_0_6(scorer: SignificanceScorer) -> None:
    entry = _make_entry(summary="Important meeting at 3pm tomorrow.")
    score = scorer._score_emotional(entry)
    assert score == 0.6


@pytest.mark.asyncio
async def test_emotional_alert_returns_0_6(scorer: SignificanceScorer) -> None:
    entry = _make_entry(summary="Alert: package delivered.")
    score = scorer._score_emotional(entry)
    assert score == 0.6


@pytest.mark.asyncio
async def test_emotional_normal_returns_0_2(scorer: SignificanceScorer) -> None:
    entry = _make_entry(summary="User asked what the weather is like today.")
    score = scorer._score_emotional(entry)
    assert score == 0.2


# ---------------------------------------------------------------------------
# Overall score — weighted calculation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overall_score_uses_config_weights(
    scorer: SignificanceScorer, mock_redis: AsyncMock
) -> None:
    """Verify overall = 0.35*safety + 0.25*novelty + 0.25*personal + 0.15*emotional."""
    mock_redis.zincrby.return_value = 1.0  # novelty = 1.0 (first time)
    # conversation, no safety keywords → safety=0.0, novelty=1.0, personal=0.8, emotional=0.2
    entry = _make_entry(
        source="conversation",
        summary="User asked about the weather.",
        entities=["weather"],
    )
    result = await scorer.score(entry)

    expected = round(0.35 * 0.0 + 0.25 * 1.0 + 0.25 * 0.8 + 0.15 * 0.2, 3)
    assert result.overall == pytest.approx(expected, abs=1e-3)
    assert result.source == "heuristic"
    assert result.safety == 0.0
    assert result.novelty == 1.0
    assert result.personal == 0.8
    assert result.emotional == 0.2


@pytest.mark.asyncio
async def test_overall_score_urgent_trigger(
    scorer: SignificanceScorer, mock_redis: AsyncMock
) -> None:
    """Urgent trigger should yield high overall score."""
    mock_redis.zincrby.return_value = 1.0
    entry = _make_entry(
        source="trigger",
        summary="Urgent: smoke alarm triggered in kitchen.",
        entities=["smoke_alarm"],
    )
    result = await scorer.score(entry)

    # safety=1.0, novelty=1.0, personal=0.3, emotional=0.9
    expected = round(0.35 * 1.0 + 0.25 * 1.0 + 0.25 * 0.3 + 0.15 * 0.9, 3)
    assert result.overall == pytest.approx(expected, abs=1e-3)


@pytest.mark.asyncio
async def test_score_returns_significance_score_model(
    scorer: SignificanceScorer, mock_redis: AsyncMock
) -> None:
    entry = _make_entry()
    result = await scorer.score(entry)
    assert isinstance(result, SignificanceScore)


@pytest.mark.asyncio
async def test_overall_rounded_to_3_decimal_places(
    scorer: SignificanceScorer, mock_redis: AsyncMock
) -> None:
    mock_redis.zincrby.return_value = 3.0  # novelty = 1/3
    entry = _make_entry(source="conversation", entities=["x"])
    result = await scorer.score(entry)
    # Ensure rounding applied — value should have at most 3 decimal places
    assert result.overall == round(result.overall, 3)


# ---------------------------------------------------------------------------
# Entity frequency tracking via ZINCRBY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entity_frequency_tracked_on_zincrby(
    scorer: SignificanceScorer, mock_redis: AsyncMock
) -> None:
    """ZINCRBY must be called with ENTITY_FREQUENCY_KEY for each entity."""
    mock_redis.zincrby.return_value = 1.0
    entry = _make_entry(entities=["light_sensor", "motion_sensor"])
    await scorer._score_novelty(entry)
    calls = mock_redis.zincrby.await_args_list
    keys_used = {c.args[0] for c in calls}
    assert keys_used == {ENTITY_FREQUENCY_KEY}
    members_tracked = {c.args[2] for c in calls}
    assert members_tracked == {"light_sensor", "motion_sensor"}
