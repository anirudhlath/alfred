"""Reflex Runner — orchestration loop for the System 1 pipeline.

Reads events from Redis Streams (consumer group), runs the Reflex Engine,
dispatches actions via Home Agent, and logs observations to the scratchpad.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import redis.asyncio as aioredis

from bus.schemas.events import StateChangedEvent

if TYPE_CHECKING:
    from collections.abc import Mapping

    from core.reflex.engine import ReflexEngine
    from domains.home.home_agent import HomeAgent

logger = logging.getLogger(__name__)

# Type alias — redis-py generics are not typed in this version
AioRedis = aioredis.Redis


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
    agent: HomeAgent,
    redis: AioRedis,
    result_stream: str,
    scratchpad_queue: str,
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

    event_str = raw_event.decode() if isinstance(raw_event, bytes) else raw_event

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

    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    observation = f"{timestamp} [reflex] {action.tool_name}({action.parameters}) → {result.status}"
    await redis.lpush(scratchpad_queue, observation)  # type: ignore[misc]

    logger.info("Action: %s → %s (status=%s)", event.entity_id, action.tool_name, result.status)
    return True
