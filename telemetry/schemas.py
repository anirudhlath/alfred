"""Pydantic models for telemetry data — typed versions of the dict entries
produced by the SDK decorators."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, Field


class LatencyMetric(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metric_type: str = "latency"
    category: str
    function: str
    value: Annotated[float, Field(description="Duration in milliseconds")]
    unit: str = "ms"


class TokenMetric(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metric_type: str = "tokens"
    model: str
    function: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    inference_ms: float
    unit: str = "tokens"


class EventMetric(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metric_type: str = "event_throughput"
    bus: str
    function: str
    value: Annotated[float, Field(description="Publish latency in milliseconds")]
    unit: str = "ms"
