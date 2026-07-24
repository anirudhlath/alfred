"""Entry point for the Reflex Runner service.

Usage: python -m core.reflex
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from typing import TYPE_CHECKING

from bus.schemas.events import TriggerFired
from core.memory.paths import preferences_dir, profile_dir
from core.memory.reader import MemoryReader
from core.notifications.dispatcher import NotificationDispatcher
from core.notifications.dnd import DNDChecker
from core.notifications.publisher import NotificationPublisher
from core.notifications.schema import Urgency
from core.reflex import ollama_client
from core.reflex.context_reader import ContextReader
from core.reflex.engine import ReflexEngine, build_notification_body
from core.reflex.runner import ensure_consumer_group, process_stream_entry, publish_observation
from core.reflex.tool_registry import ToolRegistry
from core.routing.domain_router import DomainRouter
from core.warmup import start_warmup
from domains.home.home_agent import HomeAgent
from sdk.alfred_sdk.telemetry import clear_telemetry_buffer, get_telemetry_buffer
from shared.config import AlfredConfig
from shared.logging import configure_logging
from shared.redis_streams import create_redis, read_group
from shared.streams import (
    EVENTS_STREAM,
    HOME_ACTION_RESULTS_STREAM,
    HOME_STATE_STREAM,
    REFLEX_OBSERVATIONS_STREAM,
    decode_stream_value,
)
from telemetry.collector import flush_to_csv

if TYPE_CHECKING:
    from collections.abc import Mapping

    from shared.types import AioRedis

logger = logging.getLogger(__name__)

STREAM = HOME_STATE_STREAM
GROUP = "reflex-engine"
CONSUMER = "worker-1"
RESULT_STREAM = HOME_ACTION_RESULTS_STREAM

_shutdown = asyncio.Event()


EVENTS_GROUP = "reflex-trigger-fired"
EVENTS_CONSUMER = "worker-1"


def _handle_signal() -> None:
    logger.info("Shutdown signal received")
    _shutdown.set()


async def _handle_trigger_fired(
    entry_data: Mapping[str | bytes, str | bytes],
    engine: ReflexEngine,
    agent: DomainRouter,
    redis: AioRedis,
    publisher: NotificationPublisher,
) -> None:
    """Handle a single TriggerFired event — notify + optional SLM reasoning."""
    raw_event = entry_data.get("event") or entry_data.get(b"event")
    if raw_event is None:
        return

    event_str = decode_stream_value(raw_event)
    parsed = json.loads(event_str)

    if parsed.get("event_type") != "trigger_fired":
        return

    trigger_event = TriggerFired.model_validate(parsed)

    # Path A: Immediate notification (DND-aware via dispatcher)
    urgency = Urgency(trigger_event.urgency)
    await publisher.publish(
        title=f"Trigger: {trigger_event.trigger_name}",
        body=build_notification_body(trigger_event),
        source="trigger-engine",
        urgency=urgency,
    )

    # Path B: Reflex SLM reasoning (isolated — failures don't block ACK)
    try:
        action = await engine.process_trigger_fired(trigger_event)
        if action is not None:
            result = await agent.execute_action(action)
            await redis.xadd(HOME_ACTION_RESULTS_STREAM, {"event": result.model_dump_json()})

            await publish_observation(
                redis,
                REFLEX_OBSERVATIONS_STREAM,
                "trigger_fired",
                trigger_event,
                action,
                result,
            )
    except Exception as e:
        logger.error("SLM reasoning failed for trigger '%s': %s", trigger_event.trigger_name, e)


async def _consume_trigger_fired(
    redis: AioRedis,
    engine: ReflexEngine,
    agent: DomainRouter,
    publisher: NotificationPublisher,
) -> None:
    """Second event loop — TriggerFired events from alfred:events."""
    await ensure_consumer_group(redis, EVENTS_STREAM, EVENTS_GROUP)

    while not _shutdown.is_set():
        entries = await read_group(
            redis,
            EVENTS_GROUP,
            EVENTS_CONSUMER,
            {EVENTS_STREAM: ">"},
            count=10,
            block=5000,
        )
        for _stream_key, stream_entries in entries:
            for entry_id, entry_data in stream_entries:
                try:
                    await _handle_trigger_fired(
                        entry_data,
                        engine,
                        agent,
                        redis,
                        publisher,
                    )
                    await redis.xack(EVENTS_STREAM, EVENTS_GROUP, entry_id)
                except Exception as e:
                    logger.error(
                        "Error processing trigger_fired %s: %s — will retry",
                        entry_id,
                        e,
                    )


async def flush_telemetry_periodically(config: AlfredConfig, interval: float = 30.0) -> None:
    """Periodically flush the telemetry buffer to CSV."""
    while True:
        await asyncio.sleep(interval)
        buf = get_telemetry_buffer()
        if buf:
            entries = list(buf)
            clear_telemetry_buffer()
            flush_to_csv(entries, config.research_vault_path)
            logger.info("Flushed %d telemetry entries", len(entries))


async def run(config: AlfredConfig) -> None:
    """Main Reflex Runner event loop."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    r: AioRedis = create_redis(config.redis_url)

    # Check tool registry — warn if empty but keep running. Tools are
    # discovered dynamically via the engine's TTL-based cache refresh.
    registry = ToolRegistry(r)
    tools = await registry.get_tools()
    if not tools:
        logger.warning(
            "No tools found in alfred:tool_registry. "
            "Reflex will start processing events once a microservice registers tools."
        )
    else:
        logger.info(
            "Loaded %d tools from %d services",
            len(tools),
            len(ToolRegistry.get_registered_services(tools)),
        )

    await ensure_consumer_group(r, STREAM, GROUP)

    context_reader = ContextReader(redis=r)
    memory_reader = MemoryReader(
        preferences_dir=preferences_dir(),
        profile_dir=profile_dir(),
        default_proactivity=config.proactivity_level,
    )
    engine = ReflexEngine(
        preferences_dir=str(preferences_dir()),
        tool_registry=registry,
        context_reader=context_reader,
        memory_reader=memory_reader,
    )
    router = DomainRouter()
    router.register("home-service", HomeAgent(redis=r))

    # Notification wiring for TriggerFired
    dnd_checker = DNDChecker(redis=r, calendar_adapter=None)
    dispatcher = NotificationDispatcher(redis=r, dnd_checker=dnd_checker)
    publisher = NotificationPublisher(dispatcher)

    # Background tasks
    telemetry_task = asyncio.create_task(flush_telemetry_periodically(config))
    trigger_fired_task = asyncio.create_task(_consume_trigger_fired(r, engine, router, publisher))
    warmup_task = start_warmup("reflex", {"ollama model": ollama_client.warmup})

    logger.info("Reflex Runner started. Listening on stream '%s'...", STREAM)

    try:
        while not _shutdown.is_set():
            entries = await read_group(r, GROUP, CONSUMER, {STREAM: ">"}, count=10, block=5000)

            for _stream_key, stream_entries in entries:
                for entry_id, entry_data in stream_entries:
                    try:
                        await process_stream_entry(
                            entry_data=entry_data,
                            engine=engine,
                            agent=router,
                            redis=r,
                            result_stream=RESULT_STREAM,
                            observation_stream=REFLEX_OBSERVATIONS_STREAM,
                        )
                        # ACK only on success — retriable errors (Ollama down)
                        # propagate as exceptions and the message stays pending
                        # for redelivery on next XREADGROUP cycle.
                        await r.xack(STREAM, GROUP, entry_id)
                    except Exception as e:
                        logger.error("Error processing entry %s: %s — will retry", entry_id, e)
    finally:
        logger.info("Shutting down Reflex Runner...")
        warmup_task.cancel()
        trigger_fired_task.cancel()
        telemetry_task.cancel()
        await r.aclose()


def main() -> None:
    configure_logging(service="reflex")
    config = AlfredConfig.from_env()
    from shared.otel import init_tracing

    init_tracing(
        service_name="reflex",
        endpoint=config.otel_endpoint if config.signoz_enabled else None,
    )
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
