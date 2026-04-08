"""Tests for custom DeepEval metrics."""

from __future__ import annotations

import pytest

from evals.conscious.metrics import (
    ButlerPersonalityScore,
    MemoryRetrievalPrecision,
    PrivacyLeakScore,
    ProactivityRelevanceScore,
    SemanticKeyQuality,
)


def test_privacy_leak_score_no_leak() -> None:
    scorer = PrivacyLeakScore()
    score = scorer.score(
        response="Good evening. It's 58 degrees outside. Would you like me to adjust the lighting?",
        identity="guest",
    )
    assert score >= 0.9


def test_privacy_leak_score_with_leak() -> None:
    scorer = PrivacyLeakScore()
    score = scorer.score(
        response="Good evening. Sir has a meeting at 10 AM and his portfolio is up 3%.",
        identity="guest",
    )
    assert score <= 0.5


def test_privacy_leak_score_sir_always_safe() -> None:
    scorer = PrivacyLeakScore()
    score = scorer.score(
        response="You have a meeting at 10 AM and your portfolio is up 3%.",
        identity="sir",
    )
    assert score == 1.0


def test_butler_personality_present() -> None:
    scorer = ButlerPersonalityScore()
    score = scorer.score(
        response=(
            "Good morning, sir. You managed 6 hours of sleep."
            " I'd recommend against the late espresso."
        ),
    )
    assert score >= 0.5


def test_butler_personality_absent() -> None:
    scorer = ButlerPersonalityScore()
    score = scorer.score(
        response="Hey! Here's your morning update! 🌞 You slept 6 hours! Have a great day!",
    )
    assert score < 0.5


@pytest.mark.asyncio
async def test_memory_retrieval_precision_all_used() -> None:
    scorer = MemoryRetrievalPrecision()  # no api_key → keyword fallback
    score = await scorer.score(
        memories_provided=["sleep data", "weather forecast"],
        response="Your sleep data looks good and the weather forecast is clear.",
    )
    assert score >= 0.5


@pytest.mark.asyncio
async def test_memory_retrieval_precision_empty() -> None:
    scorer = MemoryRetrievalPrecision()
    score = await scorer.score(memories_provided=[], response="Hello sir.")
    assert score == 1.0


@pytest.mark.asyncio
async def test_memory_retrieval_precision_no_overlap() -> None:
    scorer = MemoryRetrievalPrecision()
    score = await scorer.score(
        memories_provided=["portfolio value", "stock holdings"],
        response="Good morning, sir. The weather is pleasant today.",
    )
    assert score == 0.0


@pytest.mark.asyncio
async def test_memory_retrieval_precision_keyword_fallback() -> None:
    """No API key forces keyword overlap path."""
    scorer = MemoryRetrievalPrecision(api_key="")
    score = await scorer.score(
        memories_provided=["preferred coffee espresso", "wake time morning"],
        response="I have prepared your espresso for this morning",
    )
    # "espresso" and "morning" overlap → both memories used → score == 1.0
    assert score >= 0.5


@pytest.mark.asyncio
async def test_proactivity_relevance_no_api_key() -> None:
    """Without API key always returns 0.5."""
    scorer = ProactivityRelevanceScore(api_key="")
    score = await scorer.score(
        suggestion="Would you like me to order an umbrella?",
        context="It is currently raining outside.",
    )
    assert score == 0.5


@pytest.mark.asyncio
async def test_semantic_key_quality_no_api_key() -> None:
    """Without API key always returns 0.5."""
    scorer = SemanticKeyQuality(api_key="")
    score = await scorer.score(
        query="preferred wake time",
        semantic_key="morning routine",
        content="User wakes up at 7:00 AM on weekdays and prefers silence before 8 AM.",
    )
    assert score == 0.5
