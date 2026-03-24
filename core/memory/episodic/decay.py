"""Episodic memory decay scheduler.

DEPRECATED: DecayScheduler is superseded by Librarian._apply_decay() which
implements significance-based, pressure-driven hot→cold migration.
This module is kept for backward compatibility until Task 17 removes it.

Decay schedule (historical reference):
  0-7 days:    hot    (full entries in Redis)
  7-90 days:   warm   (individual entries in SQLite)
  90-365 days: cold   (compressed summaries in SQLite)
  365+ days:   archive (only Librarian-flagged entries survive)
"""

from __future__ import annotations

import warnings
from datetime import UTC, datetime, timedelta
from typing import Literal


class DecayScheduler:
    """Classifies episodic entries by age for decay processing.

    DEPRECATED: Use Librarian._apply_decay() for significance-based decay.
    Will be removed in Task 17.
    """

    def __init__(self, hot_days: int = 7, compress_days: int = 90) -> None:
        warnings.warn(
            "DecayScheduler is deprecated and will be removed in Task 17. "
            "Use Librarian._apply_decay() for significance-based hot→cold migration.",
            DeprecationWarning,
            stacklevel=2,
        )
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
