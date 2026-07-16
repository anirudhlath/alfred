"""Canonical event schemas for the Alfred Event Bus.

This is the SINGLE SOURCE OF TRUTH for all event types flowing through
MQTT and Redis Streams. All inter-agent communication uses these schemas.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

UrgencyLevel = Literal["informational", "important", "urgent"]


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


class TriggerFired(BaseEvent):
    """A trigger's conditions were met. Emitted when trigger has no direct action."""

    event_type: str = "trigger_fired"
    source: str = "trigger-engine"
    trigger_id: str
    trigger_name: str
    trigger_type: str
    context: dict[str, Any] = Field(default_factory=dict)
    urgency: UrgencyLevel = "informational"
    fired_by: Literal["engine", "admin"] = "engine"


class UserRequest(BaseEvent):
    """Inbound user interaction from any channel."""

    event_type: str = "user_request"
    channel: Literal["web_pwa", "signal", "voice", "ios"]
    session_id: str
    identity_claim: str
    authenticated: bool = False
    content_type: Literal["text", "audio"]
    content: str
    audio_ref: str | None = None


class AlfredResponse(BaseEvent):
    """Outbound response to a user channel."""

    event_type: str = "alfred_response"
    channel: Literal["web_pwa", "signal", "voice", "ios"]
    session_id: str
    text: str
    voice_audio_ref: str | None = None
    actions_taken: list[str] = Field(default_factory=list)
    mood: Literal["neutral", "pleased", "concerned", "amused", "serious"] = "neutral"


class TriggerCreated(BaseEvent):
    """A trigger was dynamically created."""

    event_type: str = "trigger_created"
    source: str = "trigger-engine"
    trigger_id: str = Field(default_factory=lambda: str(uuid4()))
    trigger_type: str = Field(description="Registered trigger type (e.g. time, sensor, composite)")
    name: str
    created_by: str
    conditions: dict[str, Any] = Field(description="Trigger-type-specific conditions")
    action: dict[str, Any] | None = None
    one_shot: bool = False
    urgency: UrgencyLevel = "informational"


class ReflexObservation(BaseEvent):
    """A structured observation of a Reflex Engine action for System 2 awareness.

    Published after every Reflex action execution. The Memory Ingestor
    consumes these and writes them to episodic memory so that the
    Conscious Engine can recall Reflex actions during context assembly.
    """

    event_type: str = "reflex_observation"
    observation_id: str = Field(default_factory=lambda: str(uuid4()))
    origin: Literal["state_change", "trigger_fired"]
    trigger_event: dict[str, Any] = Field(
        description="The originating event payload (StateChanged or TriggerFired)"
    )
    action: ActionRequest
    result: ActionResult
    decision_context: str | None = None
