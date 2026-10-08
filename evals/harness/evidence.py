"""What one scenario run produced — the only input the checks and the judge see.

Times are ``time.monotonic()`` seconds. The proxy, the fake HA and the driver share
one process, so their clocks agree.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, Self, get_args

from pydantic import BaseModel, Field, field_validator, model_validator

from bus.schemas.events import ReflexDecision, UrgencyLevel

Role = Literal["system1", "system2", "librarian", "unknown"]
Decision = ReflexDecision
Urgency = UrgencyLevel
StepKind = Literal["user", "ha_event", "wait", "clock", "advance_trigger", "dnd"]
STEP_KINDS: tuple[StepKind, ...] = get_args(StepKind)


class TranscriptTurn(BaseModel):
    role: Literal["user", "alfred", "event"]
    text: str


class Reply(BaseModel):
    step: int  # index into the variant's steps
    text: str
    source: str
    actions_taken: list[str] = Field(default_factory=list)
    latency_ms: float


class HaCall(BaseModel):
    t: float
    domain: str
    service: str
    service_data: dict[str, Any] = Field(default_factory=dict)
    # The targets the call asked for (areas expanded within the domain), not the
    # entities it affected: an entity of another domain, or one without a state, is
    # listed here but was left unchanged. Compare ``ha_states`` for what changed.
    entity_ids: list[str] = Field(default_factory=list)


class HaState(BaseModel):
    state: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class LlmCall(BaseModel):
    t: float
    role: Role
    latency_ms: float
    status: int
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tools_offered: list[str] = Field(default_factory=list)
    response_text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ReflexCall(BaseModel):
    """One System 1 call, parsed the way Reflex parses it (``core.reflex.decision``)."""

    t: float  # when the request reached the proxy
    latency_ms: float
    decision: Decision
    reason: str = ""
    tool: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    # Entity and area ids the proposal names, plus each named entity's area and each named
    # area's entities in the tool's domain, so a check can name either.
    targets: list[str] = Field(default_factory=list)
    problem: str | None = None
    local_hour: int | None = None  # the hour the prompt's clock line showed

    @property
    def done(self) -> float:
        """When System 1's reply came back: Reflex has decided."""
        return self.t + self.latency_ms / 1000


class TriggerRecord(BaseModel):
    """A trigger System 2 created through its tool (``TriggerCreated`` on alfred:events)."""

    t: float
    trigger_id: str
    trigger_type: str
    name: str
    created_by: str
    conditions: dict[str, Any] = Field(default_factory=dict)  # normalised: run_at, never a delay
    urgency: str = "informational"
    one_shot: bool = False
    created_at: datetime  # the event's own timestamp, the base a relative delay ran from
    # What it runs when it fires, as the engine runs it (an ``ActionPayload``). None: it
    # fires a TriggerFired instead.
    action: dict[str, Any] | None = None

    @field_validator("created_at")
    @classmethod
    def _naive_is_utc(cls, value: datetime) -> datetime:
        """A naive timestamp reads as UTC, so it compares with an aware ``run_at``."""
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class TriggerFire(BaseModel):
    """A trigger of this sample fired: a ``TriggerFired`` on alfred:events for one with no
    action, or the ``ActionRequest`` the engine sends to alfred:actions in its place for
    one with an action (``collect.trigger_records``)."""

    t: float
    trigger_id: str
    name: str
    trigger_type: str
    urgency: str
    fired_by: str


class NotificationRecord(BaseModel):
    """A notification the dispatcher sent, or one it still held deferred at the end."""

    t: float | None = None  # None for one read off the deferred list
    title: str
    body: str = ""
    urgency: str
    source: str
    # The trigger a trigger engine's notification is for, by name (its title says it).
    # None for any other notification.
    trigger: str | None = None


class Advance(BaseModel):
    """An ``advance_trigger`` step pulled this trigger's run_at to now at ``t``."""

    step: int
    trigger_id: str
    name: str
    t: float


class ClockSet(BaseModel):
    """A ``clock`` step set the user's zone to ``tz`` so the local hour was ``hour``."""

    step: int
    hour: int
    tz: str


class Evidence(BaseModel):
    scenario_id: str
    variant: int
    epoch: int
    session_id: str
    started_at: float
    ended_at: float
    step_started: list[float] = Field(default_factory=list)
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    replies: list[Reply] = Field(default_factory=list)
    ha_calls: list[HaCall] = Field(default_factory=list)
    ha_states: dict[str, HaState] = Field(default_factory=dict)
    llm_calls: list[LlmCall] = Field(default_factory=list)
    step_kinds: list[StepKind] = Field(default_factory=list)
    reflex: list[ReflexCall] = Field(default_factory=list)
    triggers_created: list[TriggerRecord] = Field(default_factory=list)
    triggers_fired: list[TriggerFire] = Field(default_factory=list)
    notifications: list[NotificationRecord] = Field(default_factory=list)  # dispatched
    deferred: list[NotificationRecord] = Field(default_factory=list)  # still held at the end
    advances: list[Advance] = Field(default_factory=list)
    clocks: list[ClockSet] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_kind_per_step(self) -> Self:
        if self.step_kinds and len(self.step_kinds) != len(self.step_started):
            raise ValueError(
                f"step_kinds has {len(self.step_kinds)} entries for "
                f"{len(self.step_started)} steps; record one per step or none"
            )
        return self

    def calls_after_step(self, step: int | None) -> list[HaCall]:
        if step is None:
            return list(self.ha_calls)
        start = self.step_start(step)
        return [c for c in self.ha_calls if c.t >= start]

    def step_index(self, step: int) -> int:
        """*step* (which counts every step, -1 the last) as a non-negative index."""
        n = len(self.step_started)
        if not -n <= step < n:
            raise IndexError(f"step {step} is outside the sample's {n} steps")
        return step % n

    def step_window(self, step: int) -> tuple[float, float]:
        """From the step's start to the next step's, or to the sample's end."""
        i = self.step_index(step)
        end = self.step_started[i + 1] if i + 1 < len(self.step_started) else self.ended_at
        return self.step_started[i], end

    def step_start(self, step: int) -> float:
        """When the step started (*step* counts every step, -1 the last)."""
        return self.step_started[self.step_index(step)]

    def reflex_during(self, step: int) -> list[ReflexCall]:
        """The System 1 calls that reached the proxy during the step's window."""
        start, end = self.step_window(step)
        return [c for c in self.reflex if start <= c.t < end]

    def last_step(self, kind: StepKind) -> int | None:
        """The index of the last step of *kind*, or None when the sample has none."""
        return next(
            (i for i in range(len(self.step_kinds) - 1, -1, -1) if self.step_kinds[i] == kind),
            None,
        )

    def step_at(self, kind: StepKind, at_step: int | None) -> int | None:
        """The step a check's ``at_step`` names, as an index; by default the last *kind* step.

        None only when *at_step* is None and the sample has no step of *kind*. An explicit
        *at_step* is taken as given, whatever its kind.
        """
        return self.last_step(kind) if at_step is None else self.step_index(at_step)

    def clock_at(self, step: int) -> ClockSet | None:
        """The clock a step ran under: the last clock step at or before it."""
        i = self.step_index(step)
        return next((c for c in reversed(self.clocks) if c.step <= i), None)
