"""Tests for trigger base models."""

from __future__ import annotations

from datetime import UTC, datetime

from core.triggers.models import ActionPayload, TriggerContext


def test_action_payload_valid() -> None:
    ap = ActionPayload(
        tool_name="smart_home.dim_lights",
        target_service="home-service",
        parameters={"room": "living_room", "level": 30},
    )
    assert ap.tool_name == "smart_home.dim_lights"
    assert ap.parameters["level"] == 30


def test_action_payload_defaults() -> None:
    ap = ActionPayload(tool_name="x", target_service="y")
    assert ap.parameters == {}


def test_trigger_context_no_event() -> None:
    ctx = TriggerContext(now=datetime.now(UTC))
    assert ctx.event is None


def test_trigger_context_with_event() -> None:
    from bus.schemas.events import StateChangedEvent

    evt = StateChangedEvent(source="test", domain="home", entity_id="light.x", new_state="on")
    ctx = TriggerContext(now=datetime.now(UTC), event=evt)
    assert ctx.event is not None
    assert ctx.event.entity_id == "light.x"
