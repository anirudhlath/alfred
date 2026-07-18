"""Event schema helpers for the SDK.

Standalone copies of the canonical event types from bus/schemas/events.py.
SDK consumers can use these without importing from the bus package directly.

NOTE: These MUST stay wire-compatible with bus/schemas/events.py.
Run test_schema_compatibility to verify after any changes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class BaseEvent(BaseModel):
    """Base event — mirrors bus/schemas/events.py for SDK standalone use."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str = Field(description="Service or component that produced this event")


class StateChangedEvent(BaseEvent):
    """A device or entity changed state. Published by microservices."""

    event_type: str = "state_changed"
    domain: str = Field(description="Domain: home, media, finance, etc.")
    entity_id: str = Field(description="Unique entity identifier, e.g. light.living_room")
    old_state: str | None = None
    new_state: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class ActionRequest(BaseEvent):
    """A request to execute an MCP tool on a microservice."""

    event_type: str = "action_request"
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    target_service: str = Field(description="Which microservice should handle this")
    tool_name: str = Field(description="MCP tool name, e.g. smart_home.dim_lights")
    parameters: dict[str, Any] = Field(default_factory=dict)


class ActionResult(BaseEvent):
    """Result of an MCP tool execution."""

    event_type: str = "action_result"
    request_id: str
    tool_name: str
    status: Literal["success", "error"]
    result: dict[str, Any] | None = None
    error: str | None = None


class ServiceRegistered(BaseEvent):
    """A sovereign service (re)registered its manifest. Mirrors bus/schemas/events.py."""

    event_type: str = "service_registered"
    service_name: str = Field(description="Name of the service that registered")
    credentials_endpoint: str | None = Field(
        default=None,
        description="Absolute URL core POSTs credentials to, if the service accepts pushes",
    )
    has_credentials_schema: bool = Field(
        default=False, description="Whether the manifest declares a credentials schema"
    )
