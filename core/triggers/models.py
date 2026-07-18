"""Base trigger models: BaseTrigger ABC, ActionPayload, TriggerContext."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime  # noqa: TC003
from typing import Any, ClassVar

from pydantic import BaseModel

from bus.schemas.events import StateChangedEvent  # noqa: TC001
from core.notifications.schema import Urgency


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
    tz: str = "UTC"  # IANA timezone name for wall-clock semantics (cron, patterns)
    event: StateChangedEvent | None = None


class BaseTrigger(ABC, BaseModel):
    """Abstract trigger. Subclasses define evaluation logic and conditions schema.

    Every concrete subclass MUST define a `conditions` field typed to its own
    nested `Conditions` Pydantic model.
    """

    responds_to_tick: ClassVar[bool] = True

    trigger_id: str
    trigger_type: str
    name: str
    enabled: bool = True
    one_shot: bool = False
    created_by: str
    created_at: datetime
    last_fired: datetime | None = None
    action: ActionPayload | None = None
    urgency: Urgency = Urgency.INFORMATIONAL

    @abstractmethod
    def evaluate(self, context: TriggerContext) -> bool:
        """Return True if this trigger should fire now."""

    def next_fire_time(self, context: TriggerContext) -> datetime | None:
        """Next moment this trigger could fire based on the clock alone.

        None means "not clock-driven" — the trigger only responds to events.
        May return a past datetime; the scheduler evaluates immediately then
        excludes non-firing past candidates from the next alarm.

        Clock-driven invariant: a clock-driven type MUST both keep
        ``responds_to_tick = True`` AND return a non-None datetime here while it
        is armed (so the scheduler can compute a wakeup for it). A purely
        event-driven type instead sets ``responds_to_tick = False`` and inherits
        the None default below.
        """
        return None

    @classmethod
    def normalize_conditions(cls, conditions: dict[str, Any], tz_name: str) -> dict[str, Any]:
        """Normalize raw tool-call conditions before validation.

        Default: unchanged. Types with timezone-sensitive fields override
        (e.g. TimeTrigger localizes naive run_at to the user's timezone).
        """
        return conditions
