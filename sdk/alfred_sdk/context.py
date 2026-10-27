"""Context data models — the shape live state is read in (see live_state.py)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ContextEntry(BaseModel):
    """A single entity's state snapshot."""

    entity_id: str
    state: str
    attributes: dict[str, Any] = {}


class ContextSnapshot(BaseModel):
    """Structured context from a service, grouped by domain."""

    controllable: dict[str, list[ContextEntry]] = {}
    sensors: dict[str, list[ContextEntry]] = {}
