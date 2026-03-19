"""Memory schemas — Pydantic models for episodic and procedural memory."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Literal

from pydantic import BaseModel

from core.triggers.models import ActionPayload  # noqa: TC001


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
    valence: Literal["positive", "negative", "neutral"]


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
