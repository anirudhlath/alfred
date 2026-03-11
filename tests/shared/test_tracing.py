"""Tests for shared.tracing.TraceRecord."""

from __future__ import annotations

from datetime import UTC, datetime

from bus.schemas.events import ActionRequest, StateChangedEvent
from shared.tracing import TraceRecord


def test_trace_record_construction() -> None:
    """TraceRecord can be built from all required fields."""
    event = StateChangedEvent(
        source="eval",
        domain="home",
        entity_id="light.living_room",
        new_state="on",
    )
    trace = TraceRecord(
        trace_id="test-001",
        timestamp=datetime.now(UTC),
        model="llama3:8b",
        event=event,
        preferences_text="- dim lights when TV on",
        tools=[{"name": "dim_lights", "target_service": "home-service"}],
        prompt="You are Alfred...",
        raw_response='{"action": "none"}',
        parsed_action=None,
        latency_ms=123.4,
        prompt_tokens=100,
        completion_tokens=10,
    )
    assert trace.trace_id == "test-001"
    assert trace.parsed_action is None
    assert trace.latency_ms == 123.4


def test_trace_record_with_action() -> None:
    """TraceRecord stores parsed ActionRequest."""
    event = StateChangedEvent(
        source="eval",
        domain="home",
        entity_id="media_player.tv",
        new_state="on",
    )
    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="lighting.dim_lights",
        parameters={"room": "living_room"},
    )
    trace = TraceRecord(
        trace_id="test-002",
        timestamp=datetime.now(UTC),
        model="gpt-oss:20b",
        event=event,
        preferences_text="- dim lights when TV on",
        tools=[],
        prompt="prompt text",
        raw_response='{"tool_name": "lighting.dim_lights"}',
        parsed_action=action,
        latency_ms=456.7,
        prompt_tokens=200,
        completion_tokens=30,
    )
    assert trace.parsed_action is not None
    assert trace.parsed_action.tool_name == "lighting.dim_lights"


def test_trace_record_json_round_trip() -> None:
    """TraceRecord serializes to JSON and back."""
    event = StateChangedEvent(
        source="eval",
        domain="home",
        entity_id="light.living_room",
        new_state="on",
    )
    trace = TraceRecord(
        trace_id="test-003",
        timestamp=datetime.now(UTC),
        model="llama3:8b",
        event=event,
        preferences_text="prefs",
        tools=[{"name": "t1"}],
        prompt="prompt",
        raw_response="{}",
        parsed_action=None,
        latency_ms=100.0,
        prompt_tokens=50,
        completion_tokens=5,
    )
    json_str = trace.model_dump_json()
    restored = TraceRecord.model_validate_json(json_str)
    assert restored.trace_id == trace.trace_id
    assert restored.model == trace.model
