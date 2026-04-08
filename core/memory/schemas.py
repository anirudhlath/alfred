"""Memory schemas — Pydantic models for episodic and procedural memory."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Literal

from pydantic import BaseModel

from core.triggers.models import ActionPayload  # noqa: TC001


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
