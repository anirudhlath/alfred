"""Episodic memory decay scheduler.

Decay schedule:
  0-7 days:    hot    (full entries in Redis)
  7-90 days:   warm   (individual entries in SQLite)
  90-365 days: cold   (compressed summaries in SQLite)
  365+ days:   archive (only Librarian-flagged entries survive)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal


class DecayScheduler:
    """Classifies episodic entries by age for decay processing."""

    def __init__(self, hot_days: int = 7, compress_days: int = 90) -> None:
        self._hot_days = hot_days
        self._compress_days = compress_days

    def classify(self, timestamp: datetime) -> Literal["hot", "warm", "cold", "archive"]:
        """Classify an entry by its age."""
        age = datetime.now(UTC) - timestamp
        if age <= timedelta(days=self._hot_days):
            return "hot"
        if age <= timedelta(days=self._compress_days):
            return "warm"
        if age <= timedelta(days=365):
            return "cold"
        return "archive"
