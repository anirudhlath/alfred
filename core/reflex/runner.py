"""Reflex Runner — orchestration loop for the System 1 pipeline.

Reads events from Redis Streams (consumer group), runs the Reflex Engine, and records its
decisions. Shadow mode (#285): nothing executes — act, ask and invalid proposals are
published as observations, and every decision is counted.
"""

from __future__ import annotations

import contextlib
import logging
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import redis.asyncio as aioredis

from bus.schemas.events import ReflexObservation, StateChangedEvent
from core.attribution.log import record_change
from core.reflex.availability import bridge_availability
from shared.redis_streams import REPLAY_MARKER_TTL_SECONDS
from shared.streams import (
    OBSERVED_ENTITY_PREFIX,
    REFLEX_DECIDED_PREFIX,
    REFLEX_DECISIONS_PREFIX,
    decode_stream_value,
)
from shared.types import AioRedis as AioRedis  # noqa: TC001  # re-export for backward compat

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date
    from typing import Literal

    from pydantic import BaseModel

    from bus.schemas.events import ActionRequest, ReflexProposal
    from core.reflex.attention import AttentionSet
    from core.reflex.engine import ReflexEngine
    from core.routing.domain_router import DomainAgent

logger = logging.getLogger(__name__)


def _debounce_default() -> int:
    """Read the debounce window from the env, tolerating garbage.

    This module is imported at module scope by several unrelated services
    (for ``ensure_consumer_group``), so a malformed value must never raise
    at import time and take them down with it.
    """
    raw = os.getenv("OBSERVATION_DEBOUNCE_SECONDS", "").strip()
    if not raw:
        return 300
    try:
        seconds = int(raw)
    except ValueError:
        logger.warning("Invalid OBSERVATION_DEBOUNCE_SECONDS %r — using 300", raw)
        return 300
    if seconds < 1:
        # Silently clamping made "0 to disable" look like it worked while
        # actually recording on a 1-second window.
        logger.warning(
            "OBSERVATION_DEBOUNCE_SECONDS %r is below the 1s minimum — clamping to 1. "
            "Passive observation cannot be disabled this way.",
            raw,
        )
        return 1
    return seconds


# Per-entity window for passive observation. Deliberately separate from the
# attention gate's 5-second cooldown: that one asks "is this SLM call worth
# making", this one asks "is this worth remembering". Values differ by two
# orders of magnitude. Tunable without a rebuild — see the review point in
# docs/superpowers/specs/2026-09-03-passive-observation-design.md.
OBSERVATION_DEBOUNCE_SECONDS = _debounce_default()


async def publish_observation(
    redis: AioRedis,
    stream: str,
    origin: Literal["state_change", "trigger_fired"],
    trigger_event: BaseModel,
    action: ActionRequest,
    result: BaseModel,
) -> None:
    """Publish a ReflexObservation to the observation stream."""
    observation = ReflexObservation(
        source="reflex-engine",
        origin=origin,
        trigger_event=trigger_event.model_dump(),
        action=action,
        result=result,
    )
    await redis.xadd(stream, {"event": observation.model_dump_json()})


DECISION_COUNT_TTL_SECONDS = 30 * 24 * 3600


def decisions_key(day: date) -> str:
    """The hash that counts one UTC day's Reflex decisions."""
    return f"{REFLEX_DECISIONS_PREFIX}{day.isoformat()}"


async def count_decision(redis: AioRedis, decision: str, *, now: datetime | None = None) -> None:
    """Count one decision for its UTC day. Best-effort: failures are logged, never raised.

    "none" observations are debounced per entity, so the stream undercounts them; these
    counters are the shadow report's true totals.
    """
    key = decisions_key((now or datetime.now(UTC)).astimezone(UTC).date())
    try:
        await redis.hincrby(key, decision, 1)
        await redis.expire(key, DECISION_COUNT_TTL_SECONDS)
    except Exception as e:
        logger.warning("Decision count failed (%s): %s", decision, e)


def decided_key(event_id: str) -> str:
    """The marker that Reflex has decided one event."""
    return f"{REFLEX_DECIDED_PREFIX}{event_id}"


async def already_decided(redis: AioRedis, event_id: str) -> bool:
    """Whether Reflex has already decided *event_id* and counted and published the decision.

    A redelivery of an entry (the reclaim pass replays one left un-ACKed) must not ask the
    model about the same event again. Raises when Redis cannot answer: the entry then stays
    pending and is replayed, where guessing "not decided" could decide it twice.
    """
    return await redis.exists(decided_key(event_id)) == 1


async def mark_decided(redis: AioRedis, event_id: str) -> None:
    """Mark *event_id* decided for twice the replay window (``SET NX EX``).

    Call it once the decision is counted and published, so a crash before then decides the
    event again. Best-effort: under Redis's maxmemory ``SET`` is refused while ``XACK`` still
    works, and failing the entry would only decide it a second time. A refusal is logged.
    """
    try:
        await redis.set(decided_key(event_id), 1, nx=True, ex=REPLAY_MARKER_TTL_SECONDS)
    except Exception as e:
        logger.warning("Decided marker not saved for %s: %s", event_id, e)


async def publish_proposal(
    redis: AioRedis,
    stream: str,
    origin: Literal["state_change", "trigger_fired"],
    trigger_event: BaseModel,
    proposal: ReflexProposal,
) -> None:
    """Publish an observation carrying a proposal Reflex did not execute."""
    observation = ReflexObservation(
        source="reflex-engine",
        origin=origin,
        trigger_event=trigger_event.model_dump(),
        proposal=proposal,
    )
    await redis.xadd(stream, {"event": observation.model_dump_json()})


async def observe_passively(
    redis: AioRedis,
    stream: str,
    event: StateChangedEvent,
    debounce_seconds: int = OBSERVATION_DEBOUNCE_SECONDS,
) -> bool:
    """Record an event the Reflex Engine saw but took no action on.

    Debounced per entity so one flapping device cannot flood episodic
    memory. Returns True if an observation was published.
    """
    seen_key = f"{OBSERVED_ENTITY_PREFIX}{event.entity_id}"
    # Redis rejects a zero TTL outright ("invalid expire time"), which under
    # the no-action path would raise on every event it is meant to record.
    if not await redis.set(seen_key, "1", nx=True, ex=max(1, debounce_seconds)):
        logger.debug("Observation debounced: %s", event.entity_id)
        return False

    observation = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event=event.model_dump(),
    )
    try:
        await redis.xadd(stream, {"event": observation.model_dump_json()})
    except Exception:
        # Release the window, or the redelivered event finds the key already
        # set and the retry silently records nothing. Suppressed: a failing DEL
        # would replace the original exception and hide why the publish failed.
        with contextlib.suppress(Exception):
            await redis.delete(seen_key)
        raise
    logger.debug("Observed: %s (%s → %s)", event.entity_id, event.old_state, event.new_state)
    return True


async def ensure_consumer_group(
    redis: AioRedis,
    stream: str,
    group: str,
    *,
    start_id: str = "0",
) -> None:
    """Create a consumer group if it doesn't already exist.

    A new group reads from *start_id*: ``"0"``, the default, delivers the stream's whole
    history; ``"$"`` only what is added after the group is created.
    """
    try:
        await redis.xgroup_create(stream, group, id=start_id, mkstream=True)
        logger.info("Created consumer group '%s' on stream '%s'", group, stream)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" in str(e):
            logger.debug("Consumer group '%s' already exists", group)
        else:
            raise


async def process_stream_entry(
    entry_id: bytes | str,
    entry_data: Mapping[str | bytes, str | bytes],
    engine: ReflexEngine,
    agent: DomainAgent,
    redis: AioRedis,
    result_stream: str,
    observation_stream: str,
    attention: AttentionSet | None = None,
) -> bool:
    """Process a single Redis Stream entry. Returns True if an action was taken — never,
    in shadow mode (#285); ``agent`` and ``result_stream`` wait for #286.

    Raises on retriable errors (e.g., the model server down) so the caller can choose not
    to ACK the message. Returns False — and is ACKed by the caller — for
    malformed events, availability blips (``core/reflex/availability.py``),
    attention-gated events, and events the engine chose not to act on. That
    last branch is not a no-op: it records a debounced passive observation,
    best-effort, so a failed write never blocks the ACK.
    Act, ask and invalid proposals are recorded too, undebounced and best-effort.

    An event is decided once: after the gate, an event already marked decided
    (``already_decided``) is skipped, and the event is marked once its decision is counted and
    published. A delivery that raised before then leaves it unmarked, so the replay decides.
    """
    raw_event = entry_data.get("event") or entry_data.get(b"event")
    if raw_event is None:
        logger.warning("Stream entry missing 'event' field: %s", entry_data)
        return False

    event_str = decode_stream_value(raw_event)

    try:
        event = StateChangedEvent.model_validate_json(event_str)
    except Exception as e:
        logger.error("Failed to parse event: %s — %s", e, event_str[:200])
        return False

    # Availability bridge — a device dropping off the network and coming back is
    # not a change. Ahead of the gate, so a blip costs no inference and does not
    # start the cooldown that would swallow a real change right behind it.
    bridged = await bridge_availability(redis, event, entry_id)
    if bridged is None:
        return False
    event = bridged

    # Count the change by its cause. Ahead of the gate, so the changes it drops are counted
    # too; once per entry, so a redelivery after a failed inference is not counted again.
    # Best-effort (record_change never raises): it never changes a decision or the ACK.
    await record_change(redis, event, entry_id=entry_id)

    # Attention gate — only attention-set members on real transitions reach
    # the SLM. Gated events stay fully visible to triggers and context
    # (they consume the stream independently / via home-service snapshots).
    if attention is not None and not await attention.should_fire(event):
        logger.debug(
            "Attention-gated: %s (%s → %s)", event.entity_id, event.old_state, event.new_state
        )
        return False

    # A redelivered entry (an un-ACKed one the reclaim pass replays, or a crash between the
    # decision and the ACK) is not decided again: the model is asked once per event.
    if await already_decided(redis, event.event_id):
        logger.info("Event %s already decided — not asking the model again", event.event_id)
        return False

    # engine.process_event() calls the model. If it is down this raises
    # (httpx.ConnectError, etc.) and the caller does NOT ACK — Redis redelivers.
    proposal = await engine.process_event(event)
    await count_decision(redis, proposal.decision)
    await _record_outcome(redis, observation_stream, event, proposal)
    # Counted and published, so a redelivery stops here; a crash before this line decides again.
    await mark_decided(redis, event.event_id)
    return False


async def _record_outcome(
    redis: AioRedis, observation_stream: str, event: StateChangedEvent, proposal: ReflexProposal
) -> None:
    """Record what Reflex decided about a state change. Best-effort: nothing here raises."""
    if proposal.decision == "none":
        # Record it rather than dropping it. Without this Alfred remembers
        # only what it did, never what it saw, and pattern detection has
        # nothing to run over.
        #
        # Isolated — failures don't block ACK. Recording is bookkeeping for an
        # event the engine has already finished handling, and under Redis
        # maxmemory the deny-oom commands it needs (SET, XADD) are rejected
        # while XREADGROUP/XACK still succeed. Propagating would leave every
        # no-action event un-ACKed and feed each one back into a fresh SLM
        # inference on the next reclaim pass.
        try:
            await observe_passively(redis, observation_stream, event)
        except Exception as e:
            logger.warning("Passive observation failed for %s: %s", event.entity_id, e)
        return

    # Shadow mode (#285): record what Reflex would do and execute nothing. Not
    # debounced — act/ask/invalid are rare and are the evidence this slice collects.
    # Isolated like the passive path, for the same maxmemory reason.
    if proposal.decision == "invalid":
        logger.warning("Invalid Reflex output for %s: %s", event.entity_id, proposal.problem)
    try:
        await publish_proposal(redis, observation_stream, "state_change", event, proposal)
    except Exception as e:
        logger.warning("Proposal observation failed for %s: %s", event.entity_id, e)
        return
    tool = proposal.action.tool_name if proposal.action is not None else "-"
    logger.info("Shadow %s for %s: %s", proposal.decision, event.entity_id, tool)
