"""Memory schemas — Pydantic models for episodic and procedural memory."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from core.triggers.models import ActionPayload  # noqa: TC001

# How many lifecycle-cycle confidence samples a routine keeps (the sparkline on the
# Triggers bench). Owned here, next to the field, and imported by the Librarian's
# `_append_confidence` so the write cap and the load cap cannot drift apart.
CONFIDENCE_HISTORY_LEN = 8


class SignificanceScore(BaseModel):
    """Multi-dimensional significance score inspired by amygdala function."""

    overall: float
    safety: float = 0.0
    novelty: float = 0.0
    personal: float = 0.0
    emotional: float = 0.0
    source: Literal["heuristic", "librarian"] = "heuristic"


class EpisodicEntry(BaseModel):
    """Episodic memory entry.

    Embedding stored separately (keyed by id) to avoid base64 bloat
    in JSON serialization. See core/memory/embeddings.py (Plan 3).
    """

    id: str
    timestamp: datetime
    source: str  # "conversation", "system1_action", "trigger", "integration"
    summary: str
    entities: list[str]
    significance: SignificanceScore
    semantic_key: str = ""
    retrieval_count: int = 0
    last_retrieved: datetime | None = None
    compressed_into: str | None = None
    valence: Literal["positive", "negative", "neutral"] = "neutral"


class EpisodicResult(BaseModel):
    """Result from episodic memory recall."""

    entry: EpisodicEntry
    score: float
    source_store: Literal["hot", "cold"]


class RoutineStep(BaseModel):
    """A single step in a learned routine."""

    description: str
    action: ActionPayload | None = None


class RoutineSpec(BaseModel):
    """Procedural memory — a learned routine/pattern."""

    name: str
    trigger_pattern: str
    steps: list[RoutineStep]
    confidence: float
    learned_from: list[str]  # Episodic entry IDs
    state: Literal["candidate", "active", "dormant", "archived"]
    last_hit: datetime | None = None
    consecutive_misses: int = 0
    last_suggested: datetime | None = None
    confidence_history: list[float] = Field(default_factory=list)  # newest last (Librarian)

    @field_validator("confidence_history")
    @classmethod
    def _trim_confidence_history(cls, value: list[float]) -> list[float]:
        """Keep the newest ``CONFIDENCE_HISTORY_LEN`` samples.

        Trim rather than reject: routines are YAML on disk, so a hand-edited or
        pre-cap file must still load. Refusing it would take the whole routine out
        of the Librarian's lifecycle over its own history.
        """
        return value[-CONFIDENCE_HISTORY_LEN:]
