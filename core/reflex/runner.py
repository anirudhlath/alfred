"""Reflex Runner — orchestration loop for the System 1 pipeline.

Reads events from Redis Streams (consumer group), runs the Reflex Engine,
dispatches actions via a DomainAgent, and publishes structured observations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import redis.asyncio as aioredis

from bus.schemas.events import ReflexObservation, StateChangedEvent
from shared.streams import decode_stream_value
from shared.types import AioRedis as AioRedis  # noqa: TC001  # re-export for backward compat

if TYPE_CHECKING:
    from collections.abc import Mapping

    from core.reflex.engine import ReflexEngine
    from core.routing.domain_router import DomainAgent

logger = logging.getLogger(__name__)


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
) -> bool:
    """Process a single Redis Stream entry. Returns True if an action was taken.

    Raises on retriable errors (e.g., Ollama down) so the caller can
    choose not to ACK the message. Returns False for skip-worthy errors
    (malformed event, no action needed).
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

    # NOTE: engine.process_event() calls Ollama. If Ollama is down, this
    # raises (httpx.ConnectError, etc.). We intentionally let it propagate
    # so the caller does NOT ACK the message — Redis will redeliver it.
    action = await engine.process_event(event)
    if action is None:
        logger.debug("No action for event %s", event.entity_id)
        return False

    result = await agent.execute_action(action)

    await redis.xadd(result_stream, {"event": result.model_dump_json()})

    # Publish structured observation for Memory Ingestor (D8)
    observation = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event=event.model_dump(),
        action=action,
        result=result,
    )
    await redis.xadd(observation_stream, {"event": observation.model_dump_json()})

    logger.info("Action: %s → %s (status=%s)", event.entity_id, action.tool_name, result.status)
    return True
