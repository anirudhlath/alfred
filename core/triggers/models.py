"""Base trigger models: BaseTrigger ABC, ActionPayload, TriggerContext."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime  # noqa: TC003
from typing import Any

from pydantic import BaseModel

from bus.schemas.events import StateChangedEvent  # noqa: TC001


class ActionPayload(BaseModel):
    """Action to execute when a trigger fires.

    Contains the subset of ActionRequest fields needed to describe the action.
    The Trigger Engine converts this to a full ActionRequest on fire, setting
    source='trigger-engine' and generating event metadata.
    """

    tool_name: str
    target_service: str
    parameters: dict[str, Any] = {}


class TriggerContext(BaseModel):
    """Read-only context passed to evaluate()."""

    now: datetime
    event: StateChangedEvent | None = None


class BaseTrigger(ABC, BaseModel):
    """Abstract trigger. Subclasses define evaluation logic and conditions schema.

    Every concrete subclass MUST define a `conditions` field typed to its own
    nested `Conditions` Pydantic model.
    """

    trigger_id: str
    trigger_type: str
    name: str
    enabled: bool = True
    one_shot: bool = False
    created_by: str
    created_at: datetime
    last_fired: datetime | None = None
    action: ActionPayload | None = None

    @abstractmethod
    def evaluate(self, context: TriggerContext) -> bool:
        """Return True if this trigger should fire now."""
