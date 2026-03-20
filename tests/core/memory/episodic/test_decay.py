"""Tests for episodic decay scheduler."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.memory.episodic.decay import DecayScheduler


def test_classify_hot() -> None:
    scheduler = DecayScheduler(hot_days=7, compress_days=90)
    ts = datetime.now(UTC) - timedelta(days=3)
    assert scheduler.classify(ts) == "hot"


def test_classify_warm() -> None:
    scheduler = DecayScheduler(hot_days=7, compress_days=90)
    ts = datetime.now(UTC) - timedelta(days=30)
    assert scheduler.classify(ts) == "warm"


def test_classify_cold() -> None:
    scheduler = DecayScheduler(hot_days=7, compress_days=90)
    ts = datetime.now(UTC) - timedelta(days=200)
    assert scheduler.classify(ts) == "cold"


def test_classify_archive() -> None:
    scheduler = DecayScheduler(hot_days=7, compress_days=90)
    ts = datetime.now(UTC) - timedelta(days=400)
    assert scheduler.classify(ts) == "archive"
