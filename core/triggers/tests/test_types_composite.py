"""Tests for CompositeTrigger."""

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
    import core.triggers.types  # noqa: F401


def _make_composite(**kwargs: Any) -> Any:
    cls = TriggerRegistry.get("composite")
    defaults: dict[str, Any] = {
        "trigger_id": "t-1",
        "trigger_type": "composite",
        "name": "test composite",
        "created_by": "test",
        "created_at": datetime.now(UTC),
        "conditions": {"children": [], "require": 1},
    }
    defaults.update(kwargs)
    return cls(**defaults)


def test_composite_registered() -> None:
    assert "composite" in TriggerRegistry.available_types()


def test_all_children_match() -> None:
    children = [
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.a", "state_match": "on"}},
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.a", "state_match": "on"}},
    ]
    trigger = _make_composite(conditions={"children": children, "require": 2})
    event = StateChangedEvent(
        source="test",
        domain="home",
        entity_id="light.a",
        new_state="on",
    )
    ctx = TriggerContext(now=datetime.now(UTC), event=event)
    assert trigger.evaluate(ctx) is True


def test_not_enough_children_match() -> None:
    children = [
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.a", "state_match": "on"}},
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.b", "state_match": "on"}},
    ]
    trigger = _make_composite(conditions={"children": children, "require": 2})
    event = StateChangedEvent(
        source="test",
        domain="home",
        entity_id="light.a",
        new_state="on",
    )
    ctx = TriggerContext(now=datetime.now(UTC), event=event)
    assert trigger.evaluate(ctx) is False


def test_mixed_time_and_sensor() -> None:
    children = [
        {"trigger_type": "time", "conditions": {"cron": "0 7 * * *"}},
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.a", "state_match": "on"}},
    ]
    trigger = _make_composite(conditions={"children": children, "require": 2})
    event = StateChangedEvent(
        source="test",
        domain="home",
        entity_id="light.a",
        new_state="on",
    )
    ctx = TriggerContext(now=datetime(2026, 3, 10, 7, 0, 0, tzinfo=UTC), event=event)
    assert trigger.evaluate(ctx) is True


def test_partial_require() -> None:
    children = [
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.a", "state_match": "on"}},
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.b", "state_match": "on"}},
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.a", "state_match": "on"}},
    ]
    trigger = _make_composite(conditions={"children": children, "require": 2})
    event = StateChangedEvent(
        source="test",
        domain="home",
        entity_id="light.a",
        new_state="on",
    )
    ctx = TriggerContext(now=datetime.now(UTC), event=event)
    assert trigger.evaluate(ctx) is True


def test_cached_children_populated() -> None:
    """model_post_init should pre-build child instances."""
    children = [
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.a", "state_match": "on"}},
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.b"}},
    ]
    trigger = _make_composite(conditions={"children": children, "require": 1})
    cached = trigger._cached_children  # type: ignore[attr-defined]
    assert len(cached) == 2
    assert cached[0].trigger_id == "t-1:child:0"
    assert cached[1].trigger_id == "t-1:child:1"


def test_cached_children_indexed_ids() -> None:
    """Each child should have a unique indexed trigger_id."""
    children = [
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.a"}},
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.b"}},
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.c"}},
    ]
    trigger = _make_composite(conditions={"children": children, "require": 1})
    cached = trigger._cached_children  # type: ignore[attr-defined]
    ids = [c.trigger_id for c in cached]
    assert ids == ["t-1:child:0", "t-1:child:1", "t-1:child:2"]


def test_model_copy_rebuilds_cached_children() -> None:
    """model_copy() should re-run model_post_init and rebuild children."""
    children = [
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.a"}},
    ]
    trigger = _make_composite(conditions={"children": children, "require": 1})
    copied = trigger.model_copy(update={"name": "renamed"})
    cached = copied._cached_children  # type: ignore[attr-defined]
    assert len(cached) == 1
