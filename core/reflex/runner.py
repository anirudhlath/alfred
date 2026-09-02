"""Reflex Runner — orchestration loop for the System 1 pipeline.

Reads events from Redis Streams (consumer group), runs the Reflex Engine,
dispatches actions via a DomainAgent, and publishes structured observations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import redis.asyncio as aioredis

from bus.schemas.events import ReflexObservation, StateChangedEvent
from shared.redis_streams import reclaim_stale
from shared.streams import decode_stream_value
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
        logger.debug("No action for event %s", event.entity_id)
        return False

    result = await agent.execute_action(action)

    await redis.xadd(result_stream, {"event": result.model_dump_json()})

    await publish_observation(redis, observation_stream, "state_change", event, action, result)

    logger.info("Action: %s → %s (status=%s)", event.entity_id, action.tool_name, result.status)
    return True


# A reclaimed home event is only worth acting on while it still describes the house.
MAX_REPLAY_AGE_MS = 300_000  # 5 minutes


def is_replayable(entry_id: bytes | str, *, now_ms: int) -> bool:
    """Is this reclaimed stream entry recent enough to still act on?

    Redis stream ids are ``<unix-ms>-<seq>``. Entries older than
    ``MAX_REPLAY_AGE_MS`` are ACKed and dropped rather than replayed — reacting to a
    stale state change is worse than missing it. An id we cannot parse is treated as
    too old (fail closed).
    """
    raw = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
    try:
        created_ms = int(raw.split("-", 1)[0])
    except ValueError:
        return False
    return now_ms - created_ms <= MAX_REPLAY_AGE_MS


async def reclaim_replayable(
    redis: AioRedis,
    stream: str,
    group: str,
    consumer: str,
    *,
    now_ms: int,
) -> list[tuple[bytes | str, dict[bytes | str, bytes | str]]]:
    """Reclaim un-ACKed entries, returning only those still worth acting on.

    Entries past ``MAX_REPLAY_AGE_MS`` are ACKed and discarded: they still have to
    leave the pending-entries list (that is the leak being fixed), but replaying an
    hours-old state change would drive the house from history rather than from now.
    """
    claimed = await reclaim_stale(redis, stream, group, consumer)
    replayable: list[tuple[bytes | str, dict[bytes | str, bytes | str]]] = []
    expired: list[bytes | str] = []
    for entry_id, entry_data in claimed:
        if is_replayable(entry_id, now_ms=now_ms):
            replayable.append((entry_id, entry_data))
        else:
            expired.append(entry_id)
    for entry_id in expired:
        await redis.xack(stream, group, entry_id)
    if expired:
        logger.warning(
            "Dropped %d stale pending entries on '%s' (older than %dms)",
            len(expired),
            stream,
            MAX_REPLAY_AGE_MS,
        )
    if replayable:
        logger.info("Reclaimed %d pending entries on '%s'", len(replayable), stream)
    return replayable
