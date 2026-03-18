"""Tests for TimeTrigger."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from core.triggers.models import TriggerContext
from core.triggers.registry import TriggerRegistry


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    import core.triggers.types.time  # noqa: F401


def _make_time_trigger(**kwargs: object) -> Any:
    cls = TriggerRegistry.get("time")
    defaults: dict[str, object] = {
        "trigger_id": "t-1",
        "trigger_type": "time",
        "name": "test",
        "created_by": "test",
        "created_at": datetime.now(UTC),
        "conditions": {},
    }
    defaults.update(kwargs)
    return cls(**defaults)


def test_time_trigger_registered() -> None:
    assert "time" in TriggerRegistry.available_types()


def test_cron_match() -> None:
    trigger = _make_time_trigger(conditions={"cron": "0 7 * * *"})
    ctx = TriggerContext(now=datetime(2026, 3, 10, 7, 0, 0, tzinfo=UTC))
    assert trigger.evaluate(ctx) is True


def test_cron_no_match() -> None:
    trigger = _make_time_trigger(conditions={"cron": "0 7 * * *"})
    ctx = TriggerContext(now=datetime(2026, 3, 10, 8, 0, 0, tzinfo=UTC))
    assert trigger.evaluate(ctx) is False


def test_run_at_fires() -> None:
    target = datetime(2026, 3, 10, 15, 0, 0, tzinfo=UTC)
    trigger = _make_time_trigger(conditions={"run_at": target.isoformat()})
    ctx = TriggerContext(now=datetime(2026, 3, 10, 15, 0, 1, tzinfo=UTC))
    assert trigger.evaluate(ctx) is True


def test_validated_cron_populated() -> None:
    """model_post_init should validate cron and store sentinel croniter."""
    trigger = _make_time_trigger(conditions={"cron": "0 7 * * *"})
    cached = trigger._validated_cron
    assert cached is not None


def test_validated_cron_none_for_run_at() -> None:
    """run_at triggers should have _validated_cron = None."""
    target = datetime(2026, 3, 10, 15, 0, 0, tzinfo=UTC)
    trigger = _make_time_trigger(conditions={"run_at": target.isoformat()})
    cached = trigger._validated_cron
    assert cached is None


def test_invalid_cron_fails_at_construction() -> None:
    """Bad cron expression should raise at construction, not at evaluate()."""
    with pytest.raises(ValueError, match=r"[Ii]nvalid|[Bb]ad"):
        _make_time_trigger(conditions={"cron": "not a cron"})


def test_model_copy_rebuilds_validated_cron() -> None:
    """model_copy() should re-run model_post_init."""
    trigger = _make_time_trigger(conditions={"cron": "0 7 * * *"})
    copied = trigger.model_copy(update={"name": "renamed"})
    cached = copied._validated_cron
    assert cached is not None


def test_run_at_not_yet() -> None:
    target = datetime(2026, 3, 10, 15, 0, 0, tzinfo=UTC)
    trigger = _make_time_trigger(conditions={"run_at": target.isoformat()})
    ctx = TriggerContext(now=datetime(2026, 3, 10, 14, 59, 0, tzinfo=UTC))
    assert trigger.evaluate(ctx) is False
