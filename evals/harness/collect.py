"""Bus entries become evidence records, timed on the sample's monotonic clock.

A stream entry's id is its wall time in ms. ``started``/``started_wall`` are the same
instant on both clocks, so ``t = started + (wall - started_wall)``. The container shares
the host's kernel clock, so the two agree.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from bus.schemas.events import TriggerCreated, TriggerFired
from core.notifications.schema import Notification
from evals.harness.evidence import NotificationRecord, TriggerFire, TriggerRecord

if TYPE_CHECKING:
    from evals.harness.bus import Entry

logger = logging.getLogger(__name__)

TOOL_CALL = "tool-call"  # TriggerCreated.created_by when System 2 used its tool


def _on_sample_clock(wall: float, started: float, started_wall: float) -> float:
    return started + (wall - started_wall)


def _this_sample(entries: list[Entry], started_wall: float) -> list[Entry]:
    """The entries from the sample's start on, earliest first.

    Checks take a record list's ``[0]`` as the earliest, so the order is set here rather
    than trusted to the reader. An entry before the start is an earlier sample's: a trigger
    it created is not this sample's behaviour. Stream ids are whole milliseconds, so one in
    the start's own millisecond reads as before it; nothing a sample causes happens that soon.
    """
    return sorted((e for e in entries if e.wall >= started_wall), key=lambda e: e.wall)


def _event(entry: Entry) -> dict[str, Any] | None:
    raw = entry.data.get("event")
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Unreadable entry on alfred:events: %r", raw[:120])
        return None
    return parsed if isinstance(parsed, dict) else None


def trigger_records(
    entries: list[Entry], started: float, started_wall: float
) -> tuple[list[TriggerRecord], list[TriggerFire]]:
    """The triggers System 2 created (other creators, such as the notification
    dispatcher's drain trigger, are not its behaviour), and every fire, in this sample
    and earliest first."""
    created: list[TriggerRecord] = []
    fired: list[TriggerFire] = []
    for entry in _this_sample(entries, started_wall):
        event = _event(entry)
        if event is None:
            continue
        t = _on_sample_clock(entry.wall, started, started_wall)
        kind = event.get("event_type")
        try:
            if kind == "trigger_created":
                made = TriggerCreated.model_validate(event)
                if made.created_by == TOOL_CALL:
                    created.append(
                        TriggerRecord(
                            t=t,
                            trigger_id=made.trigger_id,
                            trigger_type=made.trigger_type,
                            name=made.name,
                            created_by=made.created_by,
                            conditions=made.conditions,
                            urgency=made.urgency,
                            one_shot=made.one_shot,
                            created_at=made.timestamp,
                        )
                    )
            elif kind == "trigger_fired":
                fire = TriggerFired.model_validate(event)
                fired.append(
                    TriggerFire(
                        t=t,
                        trigger_id=fire.trigger_id,
                        name=fire.trigger_name,
                        trigger_type=fire.trigger_type,
                        urgency=fire.urgency,
                        fired_by=fire.fired_by,
                    )
                )
        except ValidationError as exc:
            logger.warning("Unreadable %s on alfred:events: %s", kind, exc)
    return created, fired


def _notification(raw: str) -> Notification | None:
    try:
        return Notification.model_validate_json(raw)
    except ValidationError as exc:
        logger.warning("Unreadable notification: %s", exc)
        return None


def _record(n: Notification, t: float | None) -> NotificationRecord:
    return NotificationRecord(
        t=t, title=n.title, body=n.body, urgency=str(n.urgency), source=n.source
    )


def notification_records(
    entries: list[Entry], started: float, started_wall: float
) -> list[NotificationRecord]:
    """The notifications the dispatcher sent in this sample, earliest first."""
    records: list[NotificationRecord] = []
    for entry in _this_sample(entries, started_wall):
        raw = entry.data.get("notification")
        if raw is not None and (n := _notification(raw)) is not None:
            records.append(_record(n, _on_sample_clock(entry.wall, started, started_wall)))
    return records


def deferred_records(raws: list[str]) -> list[NotificationRecord]:
    """The notifications still held deferred, in the list's order (the order deferred)."""
    return [_record(n, None) for raw in raws if (n := _notification(raw)) is not None]
