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
from core.reflex.availability import bridge_availability
from shared.streams import OBSERVED_ENTITY_PREFIX, REFLEX_DECISIONS_PREFIX, decode_stream_value
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
) -> None:
    """Create a consumer group if it doesn't already exist."""
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
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

    Raises on retriable errors (e.g., Ollama down) so the caller can choose not
    to ACK the message. Returns False — and is ACKed by the caller — for
    malformed events, availability blips (``core/reflex/availability.py``),
    attention-gated events, and events the engine chose not to act on. That
    last branch is not a no-op: it records a debounced passive observation,
    best-effort, so a failed write never blocks the ACK.
    Act, ask and invalid proposals are recorded too, undebounced and best-effort.
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

    # Attention gate — only attention-set members on real transitions reach
    # the SLM. Gated events stay fully visible to triggers and context
    # (they consume the stream independently / via home-service snapshots).
    if attention is not None and not await attention.should_fire(event):
        logger.debug(
            "Attention-gated: %s (%s → %s)", event.entity_id, event.old_state, event.new_state
        )
        return False

    # engine.process_event() calls the model. If it is down this raises
    # (httpx.ConnectError, etc.) and the caller does NOT ACK — Redis redelivers.
    proposal = await engine.process_event(event)
    await count_decision(redis, proposal.decision)

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
        return False

    # Shadow mode (#285): record what Reflex would do and execute nothing. Not
    # debounced — act/ask/invalid are rare and are the evidence this slice collects.
    # Isolated like the passive path, for the same maxmemory reason.
    if proposal.decision == "invalid":
        logger.warning("Invalid Reflex output for %s: %s", event.entity_id, proposal.problem)
    try:
        await publish_proposal(redis, observation_stream, "state_change", event, proposal)
    except Exception as e:
        logger.warning("Proposal observation failed for %s: %s", event.entity_id, e)
        return False
    tool = proposal.action.tool_name if proposal.action is not None else "-"
    logger.info("Shadow %s for %s: %s", proposal.decision, event.entity_id, tool)
    return False
