"""Bus entries become evidence records, timed on the sample's monotonic clock.

A stream entry's id is its wall time in ms. ``started``/``started_wall`` are the same
instant on both clocks, so ``t = started + (wall - started_wall)``. The container shares
the host's kernel clock, so the two agree.

An entry the harness recognises (a TriggerCreated, a TriggerFired, an ActionRequest, a
notification) but cannot read raises ``HarnessError``: dropped, it would turn a check's
"none created" into a fail, when the harness, not Alfred, failed. Anything else is not
evidence and is ignored, except the triggers process registering (``trigger_records``).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from bus.schemas.events import ActionRequest, TriggerCreated, TriggerFired
from core.notifications.schema import Notification
from core.triggers.models import TRIGGER_ENGINE_SOURCE, TRIGGER_TITLE_PREFIX, ActionPayload
from evals.harness.bus import window_start_ms
from evals.harness.errors import HarnessError
from evals.harness.evidence import NotificationRecord, TriggerFire, TriggerRecord
from shared.streams import (
    ACTIONS_STREAM,
    DEFERRED_NOTIFICATIONS_KEY,
    EVENTS_STREAM,
    NOTIFICATION_DISPATCH_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from evals.harness.bus import Entry

logger = logging.getLogger(__name__)

TOOL_CALL = "tool-call"  # TriggerCreated.created_by when System 2 used its tool


def _on_sample_clock(wall: float, started: float, started_wall: float) -> float:
    return started + (wall - started_wall)


def _this_sample(entries: Iterable[Entry], started_wall: float) -> list[Entry]:
    """The entries in the sample's window, earliest first.

    Checks take a record list's ``[0]`` as the earliest, so the order is set here rather
    than trusted to the reader. An entry before the window is an earlier sample's: a
    trigger it created is not this sample's behaviour. The window starts where the bus
    reads from (``window_start_ms``).
    """
    start = window_start_ms(started_wall) / 1000  # an entry's wall is its id's ms / 1000
    return sorted((e for e in entries if e.wall >= start), key=lambda e: e.wall)


def _event(entry: Entry, stream: str) -> dict[str, Any] | None:
    """The entry's event as a JSON object, or None when it has none the harness can type."""
    raw = entry.data.get("event")
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Unreadable entry on %s: %r", stream, raw[:120])
        return None
    return parsed if isinstance(parsed, dict) else None


def _read[M: BaseModel](model: type[M], data: dict[str, Any], what: str) -> M:
    """*data* as *model*. Data that does not fit is a harness failure naming *what*."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise HarnessError(f"unreadable {what}") from exc


def _engine_ran(event: dict[str, Any]) -> ActionPayload | None:
    """The action the engine ran, when *event* is a trigger with an action firing: an
    ActionRequest from the trigger engine (``core/triggers/engine.py``). A confirmed one is
    a critical action republished once the user confirmed it (``core/routing/pending.py``),
    not a second fire. None for anything else."""
    if event.get("event_type") != "action_request":
        return None
    request = _read(ActionRequest, event, f"action_request on {ACTIONS_STREAM}")
    if request.source != TRIGGER_ENGINE_SOURCE or request.confirmed:
        return None
    return ActionPayload(
        tool_name=request.tool_name,
        target_service=request.target_service,
        parameters=request.parameters,
    )


def _runs(trigger: TriggerRecord, action: ActionPayload) -> bool:
    return trigger.action == action.model_dump()


def trigger_records(
    events: list[Entry], started: float, started_wall: float, *, actions: Sequence[Entry] = ()
) -> tuple[list[TriggerRecord], list[TriggerFire]]:
    """The triggers System 2 created (other creators, such as the notification
    dispatcher's drain trigger, are not its behaviour), and the fires, in this sample and
    earliest first.

    *events* are alfred:events entries, and every TriggerFired among them is a fire. A
    trigger with an action fires instead as the engine's ActionRequest on alfred:actions,
    read from *actions*. The request names no trigger, so it is a fire of the newest
    trigger this sample created before it with that action, and of none when there is
    none. It does not say who fired it either: an admin fire reads as the engine's.
    """
    created: list[TriggerRecord] = []
    fired: list[TriggerFire] = []
    for entry in _this_sample(events, started_wall):
        event = _event(entry, EVENTS_STREAM)
        if event is None:
            continue
        t = _on_sample_clock(entry.wall, started, started_wall)
        kind = event.get("event_type")
        what = f"{kind} on {EVENTS_STREAM}"
        if kind == "trigger_created":
            made = _read(TriggerCreated, event, what)
            if made.created_by == TOOL_CALL:
                # create_trigger takes a falsy action as none (core/triggers/feature.py).
                action = _read(ActionPayload, made.action, what) if made.action else None
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
                        action=None if action is None else action.model_dump(),
                    )
                )
        elif kind == "service_registered":
            # The triggers process registers once, at start, after rehydrating every
            # snapshot: the triggers earlier samples deleted are back (bus.delete_triggers).
            # Only its name is read: another service's registration is not evidence.
            if event.get("service_name") == TRIGGER_ENGINE_SOURCE:
                raise HarnessError(
                    "the triggers process restarted mid-sample: it brought back the "
                    "triggers earlier samples deleted"
                )
        elif kind == "trigger_fired":
            fire = _read(TriggerFired, event, what)
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
    for entry in _this_sample(actions, started_wall):
        event = _event(entry, ACTIONS_STREAM)
        ran = None if event is None else _engine_ran(event)
        if ran is None:
            continue
        t = _on_sample_clock(entry.wall, started, started_wall)
        trigger = next((r for r in reversed(created) if r.t <= t and _runs(r, ran)), None)
        if trigger is not None:
            fired.append(
                TriggerFire(
                    t=t,
                    trigger_id=trigger.trigger_id,
                    name=trigger.name,
                    trigger_type=trigger.trigger_type,
                    urgency=trigger.urgency,
                    fired_by="engine",
                )
            )
    return created, sorted(fired, key=lambda f: f.t)


def is_fire_of(entry: Entry, trigger: TriggerRecord) -> bool:
    """Whether *entry*, off alfred:events or alfred:actions, is *trigger* firing: its
    TriggerFired, or the engine's ActionRequest with its action. Unreadable, it raises as
    in ``trigger_records``."""
    event = _event(entry, f"{EVENTS_STREAM} or {ACTIONS_STREAM}")
    if event is None:
        return False
    if event.get("event_type") == "trigger_fired":
        fire = _read(TriggerFired, event, f"trigger_fired on {EVENTS_STREAM}")
        return fire.trigger_id == trigger.trigger_id
    ran = _engine_ran(event)
    return ran is not None and _runs(trigger, ran)


def _notification(raw: str, where: str) -> Notification:
    try:
        return Notification.model_validate_json(raw)
    except ValidationError as exc:
        raise HarnessError(f"unreadable notification on {where}") from exc


def _record(n: Notification, t: float | None) -> NotificationRecord:
    by_trigger = n.source == TRIGGER_ENGINE_SOURCE and n.title.startswith(TRIGGER_TITLE_PREFIX)
    return NotificationRecord(
        t=t,
        title=n.title,
        body=n.body,
        urgency=str(n.urgency),
        source=n.source,
        trigger=n.title.removeprefix(TRIGGER_TITLE_PREFIX) if by_trigger else None,
    )


def notification_records(
    entries: list[Entry], started: float, started_wall: float
) -> list[NotificationRecord]:
    """The notifications the dispatcher sent in this sample, earliest first."""
    return [
        _record(
            _notification(raw, NOTIFICATION_DISPATCH_STREAM),
            _on_sample_clock(entry.wall, started, started_wall),
        )
        for entry in _this_sample(entries, started_wall)
        if (raw := entry.data.get("notification")) is not None
    ]


def deferred_records(raws: list[str]) -> list[NotificationRecord]:
    """The notifications still held deferred, in the list's order (the order deferred).
    Everything on the list is a notification, so an unreadable item is a harness failure."""
    return [_record(_notification(raw, DEFERRED_NOTIFICATIONS_KEY), None) for raw in raws]
