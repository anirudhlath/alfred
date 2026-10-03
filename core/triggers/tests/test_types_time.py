"""Tests for TimeTrigger."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    """Under computed semantics, a boundary is a match once ctx.now reaches it.

    created_at must precede the boundary — the default `datetime.now(UTC)`
    anchor would postdate this fixed historical date and never match.
    """
    trigger = _make_time_trigger(
        conditions={"cron": "0 7 * * *"},
        created_at=datetime(2026, 3, 10, 6, 0, 0, tzinfo=UTC),
    )
    ctx = TriggerContext(now=datetime(2026, 3, 10, 7, 0, 0, tzinfo=UTC))
    assert trigger.evaluate(ctx) is True


def test_cron_no_match() -> None:
    """After firing at a boundary, the trigger stays quiet until the next one."""
    trigger = _make_time_trigger(
        conditions={"cron": "0 7 * * *"},
        created_at=datetime(2026, 3, 9, 0, 0, 0, tzinfo=UTC),
        last_fired=datetime(2026, 3, 9, 7, 0, 0, tzinfo=UTC),
    )
    ctx = TriggerContext(now=datetime(2026, 3, 9, 8, 0, 0, tzinfo=UTC))
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


def _ctx(now: datetime, tz: str = "UTC") -> TriggerContext:
    return TriggerContext(now=now, tz=tz)


# --- next_fire_time: run_at ---


def test_next_fire_time_run_at_pending() -> None:
    due = datetime(2026, 7, 16, 15, 0, tzinfo=UTC)
    t = _make_time_trigger(conditions={"run_at": due.isoformat()})
    assert t.next_fire_time(_ctx(due - timedelta(seconds=5))) == due


def test_next_fire_time_run_at_already_fired_returns_none() -> None:
    due = datetime(2026, 7, 16, 15, 0, tzinfo=UTC)
    t = _make_time_trigger(conditions={"run_at": due.isoformat()}, last_fired=due)
    assert t.next_fire_time(_ctx(due + timedelta(seconds=1))) is None


def test_legacy_naive_run_at_interpreted_as_utc() -> None:
    t = _make_time_trigger(conditions={"run_at": "2026-07-16T15:00:00"})
    assert t.evaluate(_ctx(datetime(2026, 7, 16, 15, 0, 1, tzinfo=UTC))) is True
    assert t.evaluate(_ctx(datetime(2026, 7, 16, 14, 59, 59, tzinfo=UTC))) is False


# --- next_fire_time + evaluate: cron (computed, not window-matched) ---


def test_cron_next_fire_time_in_user_timezone() -> None:
    t = _make_time_trigger(
        conditions={"cron": "0 7 * * *"},
        created_at=datetime(2026, 7, 15, 20, 0, tzinfo=UTC),  # 14:00 Denver
    )
    nft = t.next_fire_time(_ctx(datetime(2026, 7, 15, 20, 0, tzinfo=UTC), tz="America/Denver"))
    assert nft is not None
    assert nft.hour == 7 and str(nft.tzinfo) == "America/Denver"
    assert nft.astimezone(UTC) == datetime(2026, 7, 16, 13, 0, tzinfo=UTC)  # 7am MDT


def test_cron_dst_transition() -> None:
    # US DST starts 2026-03-08: 7am Denver goes from UTC-7 to UTC-6.
    t = _make_time_trigger(
        conditions={"cron": "0 7 * * *"},
        last_fired=datetime(2026, 3, 7, 14, 0, tzinfo=UTC),  # fired 7am MST Mar 7
        created_at=datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
    )
    nft = t.next_fire_time(_ctx(datetime(2026, 3, 7, 15, 0, tzinfo=UTC), tz="America/Denver"))
    assert nft is not None
    assert nft.hour == 7
    assert nft.utcoffset() == timedelta(hours=-6)  # MDT after the spring-forward


def test_cron_late_wakeup_fires_exactly_once() -> None:
    t = _make_time_trigger(
        conditions={"cron": "0 7 * * *"},
        last_fired=datetime(2026, 7, 13, 7, 0, tzinfo=UTC),
        created_at=datetime(2026, 7, 1, 0, 0, tzinfo=UTC),
    )
    late_now = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)  # slept through 3 boundaries
    assert t.evaluate(_ctx(late_now)) is True  # catch-up fire
    fired = t.model_copy(update={"last_fired": late_now})
    assert fired.evaluate(_ctx(late_now)) is False  # re-anchored, no repeat


def test_cron_does_not_fire_before_first_boundary() -> None:
    t = _make_time_trigger(
        conditions={"cron": "0 7 * * *"},
        created_at=datetime(2026, 7, 16, 8, 0, tzinfo=UTC),  # created after today's 7am
    )
    assert t.evaluate(_ctx(datetime(2026, 7, 16, 9, 0, tzinfo=UTC))) is False


# --- normalize_conditions ---


def test_normalize_naive_run_at_uses_user_timezone() -> None:
    cls = TriggerRegistry.get("time")
    out = cls.normalize_conditions({"run_at": "2026-07-16T15:00:00"}, "America/Denver")
    parsed = datetime.fromisoformat(out["run_at"])
    assert parsed.utcoffset() == timedelta(hours=-6)


def test_normalize_preserves_explicit_offset() -> None:
    cls = TriggerRegistry.get("time")
    out = cls.normalize_conditions({"run_at": "2026-07-16T15:00:00+02:00"}, "America/Denver")
    assert datetime.fromisoformat(out["run_at"]).utcoffset() == timedelta(hours=2)


def test_normalize_without_run_at_is_noop() -> None:
    cls = TriggerRegistry.get("time")
    conditions = {"cron": "0 7 * * *"}
    assert cls.normalize_conditions(conditions, "America/Denver") == conditions


# --- run_in_seconds: relative delays resolved server-side ---

# A zone without DST, so the expected offset does not depend on the date the suite runs.
_IST = timedelta(hours=5, minutes=30)


@pytest.mark.parametrize("delay", [5, 0.5, 1200])
def test_normalize_run_in_seconds_resolves_to_aware_run_at(delay: float) -> None:
    cls = TriggerRegistry.get("time")
    before = datetime.now(UTC)
    out = cls.normalize_conditions({"run_in_seconds": delay}, "Asia/Kolkata")
    after = datetime.now(UTC)

    assert "run_in_seconds" not in out
    run_at = datetime.fromisoformat(out["run_at"])
    assert run_at.utcoffset() == _IST  # the user's zone, so it reads back as local time
    assert before + timedelta(seconds=delay) <= run_at <= after + timedelta(seconds=delay)


def test_resolved_run_in_seconds_builds_a_trigger_that_fires_on_time() -> None:
    cls = TriggerRegistry.get("time")
    conditions = cls.normalize_conditions({"run_in_seconds": 5}, "Asia/Kolkata")
    trigger = _make_time_trigger(conditions=conditions)
    due = trigger.conditions.run_at

    assert trigger.next_fire_time(_ctx(due)) == due
    assert trigger.evaluate(_ctx(due - timedelta(milliseconds=1))) is False
    assert trigger.evaluate(_ctx(due)) is True
    assert "run_in_seconds" not in trigger.model_dump(mode="json")["conditions"]


@pytest.mark.parametrize(
    "conditions",
    [
        {"cron": "0 7 * * *", "run_at": "2026-07-16T15:00:00+00:00"},
        {"cron": "0 7 * * *", "run_in_seconds": 5},
        {"run_at": "2026-07-16T15:00:00+00:00", "run_in_seconds": 5},
        {"cron": "0 7 * * *", "run_at": "2026-07-16T15:00:00+00:00", "run_in_seconds": 5},
    ],
)
def test_normalize_rejects_more_than_one_schedule_field(conditions: dict[str, Any]) -> None:
    cls = TriggerRegistry.get("time")
    with pytest.raises(ValueError, match="only one of cron, run_at, run_in_seconds"):
        cls.normalize_conditions(conditions, "UTC")


def test_normalize_ignores_explicit_nulls_when_counting_fields() -> None:
    cls = TriggerRegistry.get("time")
    out = cls.normalize_conditions({"cron": None, "run_at": None, "run_in_seconds": 5}, "UTC")
    assert out["cron"] is None
    assert datetime.fromisoformat(out["run_at"]) > datetime.now(UTC)


@pytest.mark.parametrize("delay", [0, -5, float("inf"), "soon"])
def test_normalize_rejects_invalid_run_in_seconds(delay: object) -> None:
    cls = TriggerRegistry.get("time")
    with pytest.raises(ValueError, match="run_in_seconds"):
        cls.normalize_conditions({"run_in_seconds": delay}, "UTC")


def test_unresolved_run_in_seconds_refused_at_construction() -> None:
    """A delay that skipped normalize_conditions has no due time and would never fire."""
    with pytest.raises(ValueError, match="must be resolved to run_at"):
        _make_time_trigger(conditions={"run_in_seconds": 5})


def test_stored_cron_and_run_at_together_still_load() -> None:
    """The one-field rule is a tool-boundary check: rows stored before it keep loading."""
    target = datetime(2026, 3, 10, 15, 0, 0, tzinfo=UTC)
    trigger = _make_time_trigger(conditions={"cron": "0 7 * * *", "run_at": target.isoformat()})
    assert trigger.next_fire_time(_ctx(target)) == target
