"""What one scenario run produced — the only input the checks and the judge see.

Times are ``time.monotonic()`` seconds. The proxy, the fake HA and the driver share
one process, so their clocks agree.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system1", "system2", "librarian", "unknown"]


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

    def calls_after_step(self, step: int | None) -> list[HaCall]:
        if step is None:
            return list(self.ha_calls)
        start = self.step_started[step]
        return [c for c in self.ha_calls if c.t >= start]
