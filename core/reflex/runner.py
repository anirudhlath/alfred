"""Reflex Runner — orchestration loop for the System 1 pipeline.

Reads events from Redis Streams (consumer group), runs the Reflex Engine,
dispatches actions via a DomainAgent, and publishes structured observations.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import redis.asyncio as aioredis

from bus.schemas.events import ReflexObservation, StateChangedEvent
from shared.streams import OBSERVED_ENTITY_PREFIX, decode_stream_value
from shared.types import AioRedis as AioRedis  # noqa: TC001  # re-export for backward compat

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Literal

    from pydantic import BaseModel

    from bus.schemas.events import ActionRequest
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
    try:
        return max(1, int(raw)) if raw else 300
    except ValueError:
        logger.warning("Invalid OBSERVATION_DEBOUNCE_SECONDS %r — using 300", raw)
        return 300


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
        # set and the retry silently records nothing.
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
    entry_data: Mapping[str | bytes, str | bytes],
    engine: ReflexEngine,
    agent: DomainAgent,
    redis: AioRedis,
    result_stream: str,
    observation_stream: str,
    attention: AttentionSet | None = None,
) -> bool:
    """Process a single Redis Stream entry. Returns True if an action was taken.

    Raises on retriable errors (e.g., Ollama down) so the caller can
    choose not to ACK the message. Returns False for skip-worthy errors
    (malformed event, no action needed) AND for attention-gated events —
    gated events are still ACKed by the caller.
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

    # Attention gate — only attention-set members on real transitions reach
    # the SLM. Gated events stay fully visible to triggers and context
    # (they consume the stream independently / via home-service snapshots).
    if attention is not None and not await attention.should_fire(event):
        logger.debug(
            "Attention-gated: %s (%s → %s)", event.entity_id, event.old_state, event.new_state
        )
        return False

    # NOTE: engine.process_event() calls Ollama. If Ollama is down, this
    # raises (httpx.ConnectError, etc.). We intentionally let it propagate
    # so the caller does NOT ACK the message — Redis will redeliver it.
    action = await engine.process_event(event)
    if action is None:
        # Record it rather than dropping it. Without this Alfred remembers
        # only what it did, never what it saw, and pattern detection has
        # nothing to run over.
        await observe_passively(redis, observation_stream, event)
        return False

    result = await agent.execute_action(action)

    await redis.xadd(result_stream, {"event": result.model_dump_json()})

    await publish_observation(redis, observation_stream, "state_change", event, action, result)

    logger.info("Action: %s → %s (status=%s)", event.entity_id, action.tool_name, result.status)
    return True
