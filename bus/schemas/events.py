"""Canonical event schemas for the Alfred Event Bus.

This is the SINGLE SOURCE OF TRUTH for all event types flowing through
MQTT and Redis Streams. All inter-agent communication uses these schemas.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, TypeAdapter, field_validator

UrgencyLevel = Literal["informational", "important", "urgent"]
ChannelName = Literal["web_pwa", "signal", "voice", "ios", "satellite"]
ContentType = Literal["text", "audio"]

# An ActionRequest's reason rides into APNs notification metadata, whose payload
# ceiling is 4 KB. A model-generated reason has no natural bound, so cap it here —
# at the schema, which is the one place every producer passes through.
REASON_MAX_LEN = 500


class BaseEvent(BaseModel):
    """Base for all events on the Alfred Event Bus."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str = Field(description="Service or component that produced this event")


class ChangeContext(BaseModel):
    """Who caused a state change, as the publishing service resolved it."""

    id: str | None = None  # the platform's context id (HA: context.id)
    user_id: str | None = None  # the platform user who caused it
    parent_id: str | None = None  # set when an automation or script caused it
    person: str | None = None  # person entity of that user, when the service could map it
    by_service: bool = False  # the publishing service's own call caused it (on Alfred's request)


class StateChangedEvent(BaseEvent):
    """A device or entity changed state. Published by microservices."""

    event_type: str = "state_changed"
    domain: str = Field(description="Domain: home, media, finance, etc.")
    entity_id: str = Field(description="Unique entity identifier, e.g. light.living_room")
    old_state: str | None = None
    new_state: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    context: ChangeContext | None = None  # who caused it; absent from older publishers


class ActionRequest(BaseEvent):
    """A request to execute an MCP tool on a microservice."""

    event_type: str = "action_request"
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    target_service: str = Field(description="Which microservice should handle this")
    tool_name: str = Field(description="MCP tool name, e.g. smart_home.dim_lights")
    parameters: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None  # why the actor wants this — shown on the confirmation prompt
    confirmed: bool = False  # set True only by the confirmation flow (contract C3)

    @field_validator("reason")
    @classmethod
    def _cap_reason(cls, value: str | None) -> str | None:
        """Truncate an over-long reason to ``REASON_MAX_LEN``.

        Truncate, never reject: the reason is explanatory text riding alongside the
        action, and dropping a door-unlock request because the model was wordy about
        it would be the worse failure. 500 characters is well inside the 4 KB APNs
        payload it ends up in, with room for the rest of the notification.
        """
        return value if value is None else value[:REASON_MAX_LEN]


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
    channel: ChannelName
    session_id: str
    identity_claim: str
    authenticated: bool = False
    content_type: ContentType
    content: str
    audio_ref: str | None = None
    # Satellite metadata (None for non-satellite channels)
    device_id: str | None = None
    area: str | None = None
    identity_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    timezone: str | None = None  # IANA name from the client (e.g. America/Denver)


class AlfredResponse(BaseEvent):
    """Outbound response to a user channel."""

    event_type: str = "alfred_response"
    channel: ChannelName
    session_id: str
    text: str
    voice_audio_ref: str | None = None
    actions_taken: list[str] = Field(default_factory=list)
    mood: Literal["neutral", "pleased", "concerned", "amused", "serious"] = "neutral"
    # The answered UserRequest's event_id. A channel matches on it when it is set, so a
    # late answer to an earlier request is never taken for the next one's.
    in_reply_to: str | None = None
    # True for an answer that was not waited for: the resumed run of an approved action, or
    # the late answer to a request whose caller already got a fallback.
    followup: bool = False


class ParkedCall(BaseModel):
    """One tool call a run deferred for approval."""

    tool_call_id: str
    tool_name: str = Field(description="Registry name of the tool")
    target_service: str
    parameters: dict[str, Any]
    reason: str | None = Field(default=None, max_length=REASON_MAX_LEN)


class ParkedRun(BaseModel):
    """A System 2 run parked until its deferred calls are approved or denied.

    One approval covers every call the run deferred. ``messages`` is the run's message
    history as the JSON Pydantic AI serialises it, kept opaque here so ``bus/`` needs
    nothing from the agent framework.
    """

    kind: Literal["parked_run"] = "parked_run"
    approval_id: str
    session_id: str
    channel: ChannelName
    identity: dict[str, Any]
    request_event_id: str
    created_at: datetime
    content_type: ContentType
    area: str | None = None
    tz_name: str
    summary: str
    calls: list[ParkedCall] = Field(min_length=1)
    messages: str

    @property
    def record_id(self) -> str:
        return self.approval_id

    def action_requests(self) -> list[ActionRequest]:
        """One unconfirmed ``ActionRequest`` per call, all under the approval id."""
        return [
            ActionRequest(
                source="conscious-engine",
                timestamp=self.created_at,
                request_id=self.approval_id,
                target_service=call.target_service,
                tool_name=call.tool_name,
                parameters=call.parameters,
                reason=call.reason,
                confirmed=False,
            )
            for call in self.calls
        ]


class ParkedAction(BaseModel):
    """A critical action the router intercepted and is holding for confirmation."""

    kind: Literal["action"] = "action"
    action: ActionRequest

    @property
    def record_id(self) -> str:
        return self.action.request_id

    def action_requests(self) -> list[ActionRequest]:
        return [self.action]


PendingRecord = Annotated[ParkedRun | ParkedAction, Field(discriminator="kind")]
PENDING_RECORD: TypeAdapter[PendingRecord] = TypeAdapter(PendingRecord)


class ApprovalDecision(BaseEvent):
    """The owner's decision on a parked run, published for the conscious process to resume."""

    event_type: str = "approval_decision"
    approval_id: str
    decision: Literal["approve", "deny"]
    run: ParkedRun


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


ReflexDecision = Literal["act", "ask", "none", "invalid"]


class ReflexProposal(BaseModel):
    """What the Reflex Engine decided about one event (#285).

    In shadow mode nothing executes: ``action`` is what Reflex *would* have run.
    ``raw`` keeps the model's text and ``problem`` says what was wrong with it when
    the decision is ``invalid``.
    """

    decision: ReflexDecision
    reason: str | None = None
    action: ActionRequest | None = None
    raw: str | None = None
    problem: str | None = None


class ReflexObservation(BaseEvent):
    """A structured observation of a Reflex Engine event for System 2 awareness.

    Published after a Reflex action executes, and also when the Reflex
    Engine considers an event and takes no action (``action is None``).
    The Memory Ingestor consumes these and writes them to episodic memory
    so that the Conscious Engine can recall them during context assembly.
    """

    event_type: str = "reflex_observation"
    observation_id: str = Field(default_factory=lambda: str(uuid4()))
    origin: Literal["state_change", "trigger_fired"]
    trigger_event: dict[str, Any] = Field(
        description="The originating event payload (StateChanged or TriggerFired)"
    )
    # None means: this event was seen, considered, and no action was taken.
    # Passive observations exist so pattern detection has something to read;
    # without them Alfred only remembers what it did, never what it saw.
    action: ActionRequest | None = None
    result: ActionResult | None = None
    decision_context: str | None = None
    # Reflex's decision when it was act, ask or invalid (#285). action/result above
    # mean "this happened"; a proposal only means "Reflex would have done this".
    proposal: ReflexProposal | None = None


class ServiceRegistered(BaseEvent):
    """A sovereign service (re)registered its manifest in the tool registry.

    Published by the SDK's AlfredClient.register() AFTER the registry hset,
    so consumers can immediately read the manifest. The channels process
    consumes this to (re)push stored credentials to the service's
    credentials_endpoint (self-healing, event-driven — no polling).
    """

    event_type: str = "service_registered"
    service_name: str = Field(description="Name of the service that registered")
    credentials_endpoint: str | None = Field(
        default=None,
        description="Absolute URL core POSTs credentials to, if the service accepts pushes",
    )
    has_credentials_schema: bool = Field(
        default=False, description="Whether the manifest declares a credentials schema"
    )
