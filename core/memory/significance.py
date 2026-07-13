"""Significance scoring model — the Amygdala.

Heuristic multi-dimensional scoring of episodic entries across four dimensions:
safety, novelty, personal, and emotional. Used by EpisodicMemory and the Librarian.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shared.config import AlfredConfig
    from shared.types import AioRedis

from core.memory.schemas import EpisodicEntry, SignificanceScore
from shared.streams import ENTITY_FREQUENCY_KEY


class SignificanceScorer:
    """Heuristic significance scoring (the Amygdala)."""

    def __init__(self, redis: AioRedis, config: AlfredConfig) -> None:
        self._redis = redis
        self._config = config

    async def score(self, entry: EpisodicEntry) -> SignificanceScore:
        """Compute heuristic significance from structured fields."""
        safety = await self._score_safety(entry)
        novelty = await self._score_novelty(entry)
        personal = self._score_personal(entry)
        emotional = self._score_emotional(entry)

        overall = (
            self._config.significance_weight_safety * safety
            + self._config.significance_weight_novelty * novelty
            + self._config.significance_weight_personal * personal
            + self._config.significance_weight_emotional * emotional
        )

        return SignificanceScore(
            overall=round(overall, 3),
            safety=safety,
            novelty=novelty,
            personal=personal,
            emotional=emotional,
            source="heuristic",
        )

    async def _score_safety(self, entry: EpisodicEntry) -> float:
        """Safety dimension — urgent triggers get high scores."""
        if entry.source == "trigger":
            summary_lower = entry.summary.lower()
            if any(
                kw in summary_lower
                for kw in ("urgent", "critical", "emergency", "alarm", "smoke", "flood", "leak")
            ):
                return 1.0
            return 0.3
        return 0.0

    async def _score_novelty(self, entry: EpisodicEntry) -> float:
        """Novelty dimension — first-time entities get high scores."""
        if not entry.entities:
            return 0.5  # No entities = moderate novelty

        novelty_scores: list[float] = []
        for entity in entry.entities:
            # Increment frequency and get current count
            count = await self._redis.zincrby(ENTITY_FREQUENCY_KEY, 1, entity)
            if count <= 1:
                novelty_scores.append(1.0)  # First time seeing this entity
            else:
                # Decay novelty with frequency: 1/count
                novelty_scores.append(round(1.0 / float(count), 3))
        return round(sum(novelty_scores) / len(novelty_scores), 3)

    def _score_personal(self, entry: EpisodicEntry) -> float:
        """Personal dimension — conversations are highly personal."""
        match entry.source:
            case "conversation":
                return 0.8
            case "integration":
                return 0.5
            case "trigger":
                return 0.3
            case "system1_action":
                return 0.2
            case _:
                return 0.3

    def _score_emotional(self, entry: EpisodicEntry) -> float:
        """Emotional dimension — based on urgency/valence signals in summary."""
        summary_lower = entry.summary.lower()
        if any(kw in summary_lower for kw in ("urgent", "critical", "emergency")):
            return 0.9
        if any(kw in summary_lower for kw in ("important", "warning", "alert")):
            return 0.6
        return 0.2
