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
time newer events for the same entity have usually moved the known state on. Both
problems are solved with the stream entry ID, which Redis assigns in arrival order
and keeps across redelivery: a return's decision is kept under it and reused by any
replay, and the known state only moves forward in entry order, so a late replay
never rolls it back.
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


def _position(entry_id: bytes | str) -> tuple[int, int]:
    """A stream entry ID (``ms-seq``) as something that sorts in arrival order."""
    ms, _, seq = decode_stream_value(entry_id).partition("-")
    return int(ms), int(seq or 0)


def _known(raw: bytes | str | None) -> tuple[str, tuple[int, int]] | None:
    """The stored ``(state, position)``; None when nothing usable is stored."""
    if raw is None:
        return None
    try:
        record = json.loads(decode_stream_value(raw))
        state, position = record["state"], _position(record["entry"])
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    return (state, position) if isinstance(state, str) else None


def _decided(raw: bytes | str) -> str | None:
    """A stored decision: the state left, or None for a blip (and for anything unreadable)."""
    try:
        left = json.loads(decode_stream_value(raw))["left"]
    except (ValueError, TypeError, KeyError):
        return None
    return left if isinstance(left, str) else None


async def _remember(redis: AioRedis, event: StateChangedEvent, entry_id: bytes | str) -> None:
    """Record ``event.new_state`` unless a later entry already set the state.

    Bookkeeping: under maxmemory HSET is refused while XACK still works, so a
    failure is logged and the event goes on. Safe to repeat, which is what lets a
    replay retry a write its first delivery lost.
    """
    try:
        position = _position(entry_id)
        known = _known(await redis.hget(LAST_KNOWN_STATE_KEY, event.entity_id))
        if known is not None and known[1] > position:
            return
        record = json.dumps({"state": event.new_state, "entry": decode_stream_value(entry_id)})
        await redis.hset(LAST_KNOWN_STATE_KEY, event.entity_id, record)
    except Exception as exc:
        logger.warning("Last known state not saved for {}: {}", event.entity_id, exc)


async def _came_back_from(
    redis: AioRedis, event: StateChangedEvent, entry_id: bytes | str
) -> str | None:
    """The state a returning entity left, or None when its return is a blip.

    The same answer on every delivery of the entry. Without the last known state
    there is no change to claim, so a failed lookup is a blip too.
    """
    decision_key = f"{AVAILABILITY_DECISION_PREFIX}{decode_stream_value(entry_id)}"
    try:
        decision = await redis.get(decision_key)
        known = None
        if decision is None:
            known = _known(await redis.hget(LAST_KNOWN_STATE_KEY, event.entity_id))
    except Exception as exc:
        logger.warning("Last known state unreadable for {}: {}", event.entity_id, exc)
        return None

    if decision is not None:
        left = _decided(decision)
    else:
        left = known[0] if known is not None and known[0] != event.new_state else None
        try:
            await redis.set(decision_key, json.dumps({"left": left}), ex=DECISION_TTL_SECONDS)
        except Exception as exc:
            logger.warning("Return of {} not kept for replay: {}", event.entity_id, exc)
    await _remember(redis, event, entry_id)
    if left is None:
        logger.debug("Availability blip: {} back as {}", event.entity_id, event.new_state)
    return left


async def bridge_availability(
    redis: AioRedis, event: StateChangedEvent, entry_id: bytes | str
) -> StateChangedEvent | None:
    """The event as a real change, or None when it carries no information.

    ``entry_id`` is the event's stream entry ID: its identity across redelivery
    and its place in arrival order.
    """
    if event.new_state in UNAVAILABLE_STATES:
        return None
    if not _no_information(event.old_state):
        await _remember(redis, event, entry_id)
        return event
    left = await _came_back_from(redis, event, entry_id)
    return None if left is None else event.model_copy(update={"old_state": left})
