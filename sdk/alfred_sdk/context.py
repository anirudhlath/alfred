"""ContextProvider protocol and data models for service context publishing."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

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


@runtime_checkable
class ContextProvider(Protocol):
    """Protocol for services/features that provide context to Alfred."""

    async def get_context(self) -> ContextSnapshot: ...
