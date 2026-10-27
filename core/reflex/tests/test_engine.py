"""Tests for the Reflex Engine — System 1 SLM inference loop."""

from __future__ import annotations

from bus.schemas.events import TriggerFired

# --- build_notification_body tests ---


def testbuild_notification_body_sensor_with_state() -> None:
    from core.reflex.engine import build_notification_body

    evt = TriggerFired(
        trigger_id="t-1",
        trigger_name="laundry done",
        trigger_type="sensor",
        context={
            "event_entity": "sensor.washing_machine_power",
            "event_state": "idle",
            "evaluated_at": "2026-03-23T21:00:00Z",
        },
    )
    body = build_notification_body(evt)
    assert "sensor.washing_machine_power: idle" in body
    assert "Fired at 2026-03-23T21:00:00Z" in body


def testbuild_notification_body_sensor_without_state() -> None:
    from core.reflex.engine import build_notification_body

    evt = TriggerFired(
        trigger_id="t-1",
        trigger_name="test",
        trigger_type="sensor",
        context={"event_entity": "sensor.temp", "evaluated_at": "2026-03-23T21:00:00Z"},
    )
    body = build_notification_body(evt)
    assert "sensor.temp" in body
    assert "sensor.temp:" not in body  # no trailing colon when state is missing


def testbuild_notification_body_time_trigger() -> None:
    from core.reflex.engine import build_notification_body

    evt = TriggerFired(
        trigger_id="t-1",
        trigger_name="take medicine",
        trigger_type="time",
        context={"trigger_type": "time", "evaluated_at": "2026-03-23T21:00:00Z"},
    )
    body = build_notification_body(evt)
    assert "Fired at 2026-03-23T21:00:00Z" in body


def testbuild_notification_body_empty_context() -> None:
    from core.reflex.engine import build_notification_body

    evt = TriggerFired(
        trigger_id="t-1",
        trigger_name="my trigger",
        trigger_type="time",
        context={},
    )
    body = build_notification_body(evt)
    assert "my trigger" in body
