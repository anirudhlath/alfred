"""Canonical event schemas for the Alfred Event Bus.

This is the SINGLE SOURCE OF TRUTH for all event types flowing through
MQTT and Redis Streams. All inter-agent communication uses these schemas.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class BaseEvent(BaseModel):
    """Base for all events on the Alfred Event Bus."""

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


class TelemetryEvent(BaseEvent):
    """Telemetry metric for observability and research."""

    event_type: str = "telemetry"
    metric_type: str = Field(description="latency | tokens | event_throughput")
    category: str = Field(description="reflex | bus | inference | etc.")
    value: float
    unit: str = Field(description="ms | tokens | bytes | count")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolRegistration(BaseEvent):
    """A microservice registering its MCP tool capabilities."""

    event_type: str = "tool_registration"
    service_name: str
    service_endpoint: str = Field(description="HTTP endpoint for MCP calls")
    tools: list[dict[str, Any]] = Field(description="List of tool manifests")


class TriggerCreated(BaseEvent):
    """The LLM dynamically created a trigger (Phase 2)."""

    event_type: str = "trigger_created"
    trigger_id: str = Field(default_factory=lambda: str(uuid4()))
    trigger_type: str = Field(description="scheduled | event_conditional | composite")
    conditions: dict[str, Any] = Field(description="Conditions that must be met to fire")
    action: ActionRequest = Field(description="Action to execute when conditions are met")
