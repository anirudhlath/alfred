"""Availability bridging — `unavailable`, `unknown` and no state carry no information.

Most state changes HA reports for watched entities are devices dropping off the
network and coming back. None of them is something that happened at home, but an
entity *can* change while unreachable (a TV turned on at the remote while its
integration was reconnecting), so they cannot simply be dropped. HA also re-adds
every entity after a restart as ``None → state``, which is the same kind of return.

The bridge keeps each entity's last real state and compares the state it comes back
in against it: the same state is a blip and is dropped, a different one becomes a
single ``last → new`` change. It runs ahead of the attention gate, so a blip costs no
inference and does not start the gate's cooldown.

The runner ACKs only on success and its reclaim pass replays what failed, by which
time newer events for the same entity have usually moved the known state on. So a
return's decision is kept under its event id and reused by any replay, and the known
state only moves forward in event time: a late replay never rolls it back.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from loguru import logger

from shared.redis_streams import MAX_REPLAY_AGE_MS
from shared.streams import (
    AVAILABILITY_DECISION_PREFIX,
    LAST_KNOWN_STATE_KEY,
    decode_stream_value,
)

if TYPE_CHECKING:
    from bus.schemas.events import StateChangedEvent
    from shared.types import AioRedis

UNAVAILABLE_STATES = frozenset({"unavailable", "unknown"})

# Twice the oldest entry the reclaim pass will replay, so a replay finds its decision.
DECISION_TTL_SECONDS = 2 * MAX_REPLAY_AGE_MS // 1000


def _no_information(state: str | None) -> bool:
    return state is None or state in UNAVAILABLE_STATES


def _known(raw: bytes | str | None) -> tuple[str, float] | None:
    """The stored ``(state, event time)``; None when nothing usable is stored."""
    if raw is None:
        return None
    try:
        record = json.loads(decode_stream_value(raw))
        state, at = record["state"], float(record["at"])
    except (ValueError, TypeError, KeyError):
        return None
    return (state, at) if isinstance(state, str) else None


async def _remember(redis: AioRedis, event: StateChangedEvent) -> None:
    """Record ``event.new_state`` unless a newer event already set the state.

    Bookkeeping: under maxmemory HSET is refused while XACK still works, so a
    failure is logged and the event goes on.
    """
    at = event.timestamp.timestamp()
    try:
        known = _known(await redis.hget(LAST_KNOWN_STATE_KEY, event.entity_id))
        if known is not None and known[1] > at:
            return
        record = json.dumps({"state": event.new_state, "at": at})
        await redis.hset(LAST_KNOWN_STATE_KEY, event.entity_id, record)
    except Exception as exc:
        logger.warning("Last known state not saved for {}: {}", event.entity_id, exc)


async def _came_back_from(redis: AioRedis, event: StateChangedEvent) -> str | None:
    """The state a returning entity left, or None when its return is a blip.

    The same answer on every delivery of the event. Without the last known state
    there is no change to claim, so a failed lookup is a blip too.
    """
    decision_key = f"{AVAILABILITY_DECISION_PREFIX}{event.event_id}"
    try:
        decided = await redis.get(decision_key)
        if decided is not None:
            return decode_stream_value(decided) or None
        known = _known(await redis.hget(LAST_KNOWN_STATE_KEY, event.entity_id))
    except Exception as exc:
        logger.warning("Last known state unreadable for {}: {}", event.entity_id, exc)
        return None

    left = known[0] if known is not None and known[0] != event.new_state else None
    try:
        await redis.set(decision_key, left or "", ex=DECISION_TTL_SECONDS)
    except Exception as exc:
        logger.warning("Return of {} not kept for replay: {}", event.entity_id, exc)
    await _remember(redis, event)
    if left is None:
        logger.debug("Availability blip: {} back as {}", event.entity_id, event.new_state)
    return left


async def bridge_availability(
    redis: AioRedis, event: StateChangedEvent
) -> StateChangedEvent | None:
    """The event as a real change, or None when it carries no information."""
    if event.new_state in UNAVAILABLE_STATES:
        return None
    if not _no_information(event.old_state):
        await _remember(redis, event)
        return event
    left = await _came_back_from(redis, event)
    return None if left is None else event.model_copy(update={"old_state": left})
