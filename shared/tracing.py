"""TraceRecord — structured inference trace for evals, debugging, and observability.

Reusable across the evals runner, Reflex Engine debug mode, and future
SigNoz/OpenTelemetry export.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any

from pydantic import BaseModel

from bus.schemas.events import ActionRequest, StateChangedEvent  # noqa: TC001


class TraceRecord(BaseModel):
    """Complete trace of a single SLM inference call."""

    trace_id: str
    timestamp: datetime
    model: str

    # Inputs
    event: StateChangedEvent
    preferences_text: str
    tools: list[dict[str, Any]]

    # Prompt
    prompt: str

    # Output
    raw_response: str
    parsed_action: ActionRequest | None

    # Metrics
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
