"""Tests for telemetry decorators."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest


@pytest.mark.asyncio
async def test_track_latency_records_duration() -> None:
    from sdk.alfred_sdk.telemetry import get_telemetry_buffer, track_latency

    @track_latency(category="test")
    async def slow_function() -> str:
        await asyncio.sleep(0.05)
        return "done"

    result = await slow_function()
    assert result == "done"

    buffer = get_telemetry_buffer()
    assert len(buffer) >= 1
    entry = buffer[-1]
    assert entry["category"] == "test"
    assert entry["metric_type"] == "latency"
    assert entry["value"] >= 50  # at least 50ms
    assert entry["unit"] == "ms"
    assert entry["function"] == "slow_function"


@pytest.mark.asyncio
async def test_track_tokens_records_usage() -> None:
    from sdk.alfred_sdk.telemetry import get_telemetry_buffer, track_tokens

    @track_tokens(model="llama3:8b")
    async def mock_inference(prompt: str) -> dict[str, Any]:
        return {
            "response": "dim the lights",
            "prompt_tokens": 150,
            "completion_tokens": 10,
            "total_tokens": 160,
        }

    result = await mock_inference("test prompt")
    assert result["response"] == "dim the lights"

    buffer = get_telemetry_buffer()
    token_entries = [e for e in buffer if e["metric_type"] == "tokens"]
    assert len(token_entries) >= 1
    entry = token_entries[-1]
    assert entry["model"] == "llama3:8b"
    assert entry["prompt_tokens"] == 150
    assert entry["completion_tokens"] == 10


@pytest.mark.asyncio
async def test_track_event_records_bus_metrics() -> None:
    from sdk.alfred_sdk.telemetry import get_telemetry_buffer, track_event

    @track_event(bus="redis")
    async def publish_something(topic: str, data: dict[str, Any]) -> dict[str, bool]:
        return {"published": True}

    await publish_something("home/state", {"entity": "light"})

    buffer = get_telemetry_buffer()
    event_entries = [e for e in buffer if e["metric_type"] == "event_throughput"]
    assert len(event_entries) >= 1
    entry = event_entries[-1]
    assert entry["bus"] == "redis"


def test_track_latency_works_on_sync() -> None:
    from sdk.alfred_sdk.telemetry import get_telemetry_buffer, track_latency

    @track_latency(category="sync-test")
    def sync_function() -> int:
        return 42

    result = sync_function()
    assert result == 42

    buffer = get_telemetry_buffer()
    sync_entries = [e for e in buffer if e["category"] == "sync-test"]
    assert len(sync_entries) >= 1
