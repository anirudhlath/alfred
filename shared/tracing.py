"""TraceRecord — structured inference traces for evals, debugging, and observability.

Split into ReflexTraceRecord (System 1) and ConsciousTraceRecord (System 2)
sharing a common TraceRecordBase.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any, Literal

from pydantic import BaseModel

from bus.schemas.events import ActionRequest, StateChangedEvent  # noqa: TC001


class TraceRecordBase(BaseModel):
    """Common fields shared by all inference trace records."""

    trace_id: str
    timestamp: datetime
    model: str
    system: str  # "reflex" or "conscious"

    # Prompt
    prompt: str

    # Output
    raw_response: str
    parsed_action: ActionRequest | None

    # Metrics
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int


class ReflexTraceRecord(TraceRecordBase):
    """Trace of a single System 1 SLM inference call."""

    system: Literal["reflex"] = "reflex"

    # Reflex-specific inputs
    event: StateChangedEvent
    preferences_text: str
    tools: list[dict[str, Any]]


class ConsciousTraceRecord(TraceRecordBase):
    """Trace of a single System 2 Claude inference call."""

    system: Literal["conscious"] = "conscious"

    # Conscious-specific inputs
    request_id: str
    session_id: str
    channel: str

    # Agentic loop
    tool_calls: list[dict[str, Any]]


# Backward compat alias — existing code imports TraceRecord
TraceRecord = ReflexTraceRecord
