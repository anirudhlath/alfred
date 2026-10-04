"""Availability bridging — `unavailable` and `unknown` carry no information.

Most state changes HA reports for watched entities are devices dropping off the
network and coming back. None of them is something that happened at home, but an
entity *can* change while unreachable (a TV turned on at the remote while its
integration was reconnecting), so they cannot simply be dropped.

The bridge keeps each entity's last real state and compares the state it comes back
in against it: the same state is a blip and is dropped, a different one becomes a
single ``last → new`` change. It runs ahead of the attention gate, so a blip costs no
inference and does not start the gate's cooldown.

Each hash field holds ``{"state", "event_id", "before"}``: the entity's state, the event
that set it, and the state before that event. The runner ACKs only on success, so an
event can come back after the bridge has already recorded it. A replay of the event
that wrote the record is compared against ``before``, or every return that failed
downstream would be dropped as a blip on its second delivery.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from loguru import logger

from shared.streams import LAST_KNOWN_STATE_KEY, decode_stream_value

if TYPE_CHECKING:
    from bus.schemas.events import StateChangedEvent
    from shared.types import AioRedis

UNAVAILABLE_STATES = frozenset({"unavailable", "unknown"})


def _last_state(raw: bytes | str | None, event: StateChangedEvent) -> str | None:
    """The state to compare ``event`` against; None when nothing usable is stored."""
    if raw is None:
        return None
    try:
        record = json.loads(decode_stream_value(raw))
    except ValueError:
        return None
    if not isinstance(record, dict):
        return None
    replay = record.get("event_id") == event.event_id
    state = record.get("before" if replay else "state")
    return state if isinstance(state, str) else None


async def bridge_availability(
    redis: AioRedis, event: StateChangedEvent
) -> StateChangedEvent | None:
    """The event as a real change, or None when it carries no information.

    A failed Redis call never blocks a real change. Remembering is bookkeeping,
    and under maxmemory HSET is refused while XACK still works, so a failed write
    is logged and the event goes on. A failed lookup drops a return from
    unavailability: without the last known state there is no change to claim.
    """
    if event.new_state in UNAVAILABLE_STATES:
        return None

    returning = event.old_state in UNAVAILABLE_STATES
    last: str | None = None
    if returning:
        try:
            raw = await redis.hget(LAST_KNOWN_STATE_KEY, event.entity_id)
        except Exception as exc:
            logger.warning("Last known state unreadable for {}: {}", event.entity_id, exc)
            return None
        last = _last_state(raw, event)

    record = {"state": event.new_state, "event_id": event.event_id, "before": last}
    try:
        await redis.hset(LAST_KNOWN_STATE_KEY, event.entity_id, json.dumps(record))
    except Exception as exc:
        logger.warning("Last known state not saved for {}: {}", event.entity_id, exc)

    if not returning:
        return event
    if last is None or last == event.new_state:
        logger.debug("Availability blip: {} back as {}", event.entity_id, event.new_state)
        return None
    return event.model_copy(update={"old_state": last})
