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
    # created_at (propagated to the time child) must precede the boundary
    # below — computed next_fire_time anchors on it, so the default
    # `datetime.now(UTC)` would postdate this fixed historical date.
    trigger = _make_composite(
        conditions={"children": children, "require": 2},
        created_at=datetime(2026, 3, 10, 6, 0, 0, tzinfo=UTC),
    )
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
    cached = trigger._cached_children
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
    cached = trigger._cached_children
    ids = [c.trigger_id for c in cached]
    assert ids == ["t-1:child:0", "t-1:child:1", "t-1:child:2"]


def test_model_copy_rebuilds_cached_children() -> None:
    """model_copy() should re-run model_post_init and rebuild children."""
    children = [
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.a"}},
    ]
    trigger = _make_composite(conditions={"children": children, "require": 1})
    copied = trigger.model_copy(update={"name": "renamed"})
    cached = copied._cached_children
    assert len(cached) == 1


def test_next_fire_time_is_min_over_time_children() -> None:
    created = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    children = [
        {"trigger_type": "time", "conditions": {"run_at": "2026-07-16T15:00:00+00:00"}},
        {"trigger_type": "time", "conditions": {"run_at": "2026-07-16T12:00:00+00:00"}},
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.x"}},
    ]
    trigger = _make_composite(
        conditions={"children": children, "require": 1},
        created_at=created,
    )
    ctx = TriggerContext(now=created)
    assert trigger.next_fire_time(ctx) == datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def test_next_fire_time_none_when_only_sensor_children() -> None:
    children = [
        {"trigger_type": "sensor", "conditions": {"entity_id": "light.x"}},
    ]
    trigger = _make_composite(conditions={"children": children, "require": 1})
    assert trigger.next_fire_time(TriggerContext(now=datetime.now(UTC))) is None


def test_children_inherit_parent_last_fired() -> None:
    fired = datetime(2026, 7, 16, 7, 0, tzinfo=UTC)
    children = [
        {"trigger_type": "time", "conditions": {"cron": "0 7 * * *"}},
    ]
    trigger = _make_composite(
        conditions={"children": children, "require": 1},
        created_at=datetime(2026, 7, 1, 0, 0, tzinfo=UTC),
    )
    fired_copy = trigger.model_copy(update={"last_fired": fired})
    # Child cron must anchor from the parent's last_fired, not created_at —
    # otherwise a composite cron child re-fires on every scheduler wake.
    child_nft = fired_copy._cached_children[0].next_fire_time(TriggerContext(now=fired))
    assert child_nft is not None and child_nft.astimezone(UTC) > fired
