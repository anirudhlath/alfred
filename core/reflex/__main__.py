"""Entry point for the Reflex Runner service.

Usage: python -m core.reflex
"""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path
from typing import TYPE_CHECKING

import redis.asyncio as aioredis

from core.memory.reader import MemoryReader
from core.memory.scratchpad_writer import ScratchpadWriter
from core.reflex.context_reader import ContextReader
from core.reflex.engine import ReflexEngine
from core.reflex.runner import ensure_consumer_group, process_stream_entry
from core.reflex.tool_registry import ToolRegistry
from core.routing.domain_router import DomainRouter
from domains.home.home_agent import HomeAgent
from sdk.alfred_sdk.telemetry import clear_telemetry_buffer, get_telemetry_buffer
from shared.config import AlfredConfig
from shared.logging import configure_logging
from shared.streams import HOME_ACTION_RESULTS_STREAM, HOME_STATE_STREAM, SCRATCHPAD_QUEUE
from telemetry.collector import flush_to_csv

if TYPE_CHECKING:
    from shared.types import AioRedis

logger = logging.getLogger(__name__)

STREAM = HOME_STATE_STREAM
GROUP = "reflex-engine"
CONSUMER = "worker-1"
RESULT_STREAM = HOME_ACTION_RESULTS_STREAM

_shutdown = asyncio.Event()


def _handle_signal() -> None:
    logger.info("Shutdown signal received")
    _shutdown.set()


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

    r: AioRedis = aioredis.from_url(config.redis_url)

    # Fail-fast: verify tools are registered before entering the event loop
    registry = ToolRegistry(r)
    tools = await registry.get_tools()
    if not tools:
        await r.aclose()
        raise RuntimeError(
            "No tools found in alfred:tool_registry. "
            "Start at least one microservice (e.g., home-service) before the Reflex Runner."
        )
    logger.info(
        "Loaded %d tools from %d services",
        len(tools),
        len(ToolRegistry.get_registered_services(tools)),
    )

    await ensure_consumer_group(r, STREAM, GROUP)

    context_reader = ContextReader(redis=r)
    memory_dir = Path(__file__).resolve().parent.parent / "memory"
    memory_reader = MemoryReader(
        preferences_dir=memory_dir / "preferences",
        profile_dir=memory_dir / "profile",
        default_proactivity=config.proactivity_level,
    )
    engine = ReflexEngine(
        preferences_dir=str(memory_dir / "preferences"),
        tool_registry=registry,
        context_reader=context_reader,
        memory_reader=memory_reader,
    )
    router = DomainRouter()
    router.register("home-service", HomeAgent(redis=r))
    writer = ScratchpadWriter(redis=r, queue_key=SCRATCHPAD_QUEUE)

    # Background tasks
    scratchpad_task = asyncio.create_task(writer.run())
    telemetry_task = asyncio.create_task(flush_telemetry_periodically(config))

    logger.info("Reflex Runner started. Listening on stream '%s'...", STREAM)

    try:
        while not _shutdown.is_set():
            entries: list[
                tuple[
                    bytes | str,
                    list[tuple[bytes | str, dict[bytes | str, bytes | str]]],
                ]
            ] = await r.xreadgroup(GROUP, CONSUMER, {STREAM: ">"}, count=10, block=5000)

            for _stream_key, stream_entries in entries:
                for entry_id, entry_data in stream_entries:
                    try:
                        await process_stream_entry(
                            entry_data=entry_data,
                            engine=engine,
                            agent=router,
                            redis=r,
                            result_stream=RESULT_STREAM,
                            scratchpad_queue=SCRATCHPAD_QUEUE,
                        )
                        # ACK only on success — retriable errors (Ollama down)
                        # propagate as exceptions and the message stays pending
                        # for redelivery on next XREADGROUP cycle.
                        await r.xack(STREAM, GROUP, entry_id)  # type: ignore[no-untyped-call]
                    except Exception as e:
                        logger.error("Error processing entry %s: %s — will retry", entry_id, e)
    finally:
        logger.info("Shutting down Reflex Runner...")
        scratchpad_task.cancel()
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
