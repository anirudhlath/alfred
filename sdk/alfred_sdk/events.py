"""Event schema helpers for the SDK.

Re-exports the canonical event types from bus/schemas for convenience.
SDK consumers can use these without importing from the bus package directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class BaseEvent(BaseModel):
    """Base event — mirrors bus/schemas/events.py for SDK standalone use."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str


class StateChangedEvent(BaseEvent):
    event_type: str = "state_changed"
    domain: str
    entity_id: str
    old_state: str | None = None
    new_state: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class ActionRequest(BaseEvent):
    event_type: str = "action_request"
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    target_service: str
    tool_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ActionResult(BaseEvent):
    event_type: str = "action_result"
    request_id: str
    tool_name: str
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None
