"""Tests for SensorTrigger."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from bus.schemas.events import StateChangedEvent
from core.triggers.models import TriggerContext
from core.triggers.registry import TriggerRegistry


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    import core.triggers.types.sensor
    import core.triggers.types.time  # noqa: F401


def _make_sensor_trigger(**kwargs: Any) -> Any:
    cls = TriggerRegistry.get("sensor")
    defaults: dict[str, Any] = {
        "trigger_id": "t-1",
        "trigger_type": "sensor",
        "name": "test",
        "created_by": "test",
        "created_at": datetime.now(UTC),
        "conditions": {"entity_id": "light.living_room"},
    }
    defaults.update(kwargs)
    return cls(**defaults)


def _make_event(
    entity_id: str = "light.living_room",
    new_state: str = "on",
    attributes: dict[str, Any] | None = None,
) -> StateChangedEvent:
    return StateChangedEvent(
        source="test",
        domain="home",
        entity_id=entity_id,
        new_state=new_state,
        attributes=attributes or {},
    )


def test_sensor_trigger_registered() -> None:
    assert "sensor" in TriggerRegistry.available_types()


def test_entity_match() -> None:
    trigger = _make_sensor_trigger()
    ctx = TriggerContext(now=datetime.now(UTC), event=_make_event())
    assert trigger.evaluate(ctx) is True


def test_entity_no_match() -> None:
    trigger = _make_sensor_trigger()
    ctx = TriggerContext(now=datetime.now(UTC), event=_make_event(entity_id="light.bedroom"))
    assert trigger.evaluate(ctx) is False


def test_state_match() -> None:
    trigger = _make_sensor_trigger(
        conditions={"entity_id": "light.living_room", "state_match": "on"}
    )
    ctx = TriggerContext(now=datetime.now(UTC), event=_make_event(new_state="on"))
    assert trigger.evaluate(ctx) is True


def test_state_no_match() -> None:
    trigger = _make_sensor_trigger(
        conditions={"entity_id": "light.living_room", "state_match": "on"}
    )
    ctx = TriggerContext(now=datetime.now(UTC), event=_make_event(new_state="off"))
    assert trigger.evaluate(ctx) is False


def test_attribute_match() -> None:
    trigger = _make_sensor_trigger(
        conditions={
            "entity_id": "light.living_room",
            "attribute_match": {"brightness": 100},
        }
    )
    ctx = TriggerContext(now=datetime.now(UTC), event=_make_event(attributes={"brightness": 100}))
    assert trigger.evaluate(ctx) is True


def test_attribute_no_match() -> None:
    trigger = _make_sensor_trigger(
        conditions={
            "entity_id": "light.living_room",
            "attribute_match": {"brightness": 100},
        }
    )
    ctx = TriggerContext(now=datetime.now(UTC), event=_make_event(attributes={"brightness": 50}))
    assert trigger.evaluate(ctx) is False


def test_no_event_returns_false() -> None:
    trigger = _make_sensor_trigger()
    ctx = TriggerContext(now=datetime.now(UTC))
    assert trigger.evaluate(ctx) is False
