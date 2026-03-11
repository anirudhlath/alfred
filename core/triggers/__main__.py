"""Entry point for the Trigger Engine service.

Usage: python -m core.triggers
"""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import UTC, datetime
from pathlib import Path

import redis.asyncio as aioredis

import core.triggers.types  # noqa: F401  — register all trigger types
from core.reflex.runner import ensure_consumer_group
from core.triggers.engine import TriggerEngine
from core.triggers.feature import TriggerFeature, TriggerFeatureContext
from core.triggers.store import TriggerStore
from sdk.alfred_sdk.client import AlfredClient
from shared.config import AlfredConfig
from shared.streams import EVENTS_STREAM

logger = logging.getLogger(__name__)

GROUP = "trigger-engine"
CONSUMER = "worker-1"
SNAPSHOT_DIR = Path("core/memory/triggers")

_shutdown = asyncio.Event()


def _handle_signal() -> None:
    logger.info("Shutdown signal received")
    _shutdown.set()


async def tick_loop(engine: TriggerEngine) -> None:
    """1-second tick loop for time-based trigger evaluation."""
    while not _shutdown.is_set():
        try:
            await engine.evaluate_tick(datetime.now(UTC))
        except Exception as e:
            logger.error("Tick loop error: %s", e)
        await asyncio.sleep(1.0)


async def event_loop(
    engine: TriggerEngine,
    r: aioredis.Redis,
) -> None:
    """Event listener loop for sensor-based trigger evaluation."""
    from bus.schemas.events import StateChangedEvent

    await ensure_consumer_group(r, EVENTS_STREAM, GROUP)

    while not _shutdown.is_set():
        try:
            entries = await r.xreadgroup(
                GROUP, CONSUMER, {EVENTS_STREAM: ">"}, count=10, block=5000
            )
        except Exception as e:
            logger.error("Event read error: %s", e)
            continue

        for _stream_key, stream_entries in entries:
            for entry_id, entry_data in stream_entries:
                raw_event = entry_data.get("event") or entry_data.get(b"event")
                if raw_event is None:
                    await r.xack(EVENTS_STREAM, GROUP, entry_id)
                    continue

                event_str = raw_event.decode() if isinstance(raw_event, bytes) else raw_event

                try:
                    event = StateChangedEvent.model_validate_json(event_str)
                except Exception:
                    await r.xack(EVENTS_STREAM, GROUP, entry_id)
                    continue

                try:
                    await engine.evaluate_event(event)
                except Exception as e:
                    logger.error("Event evaluation error: %s", e)

                await r.xack(EVENTS_STREAM, GROUP, entry_id)


async def snapshot_loop(store: TriggerStore, interval: float = 300.0) -> None:
    """Periodic YAML snapshot (every 5 minutes)."""
    while not _shutdown.is_set():
        await asyncio.sleep(interval)
        try:
            await store.snapshot_all()
            logger.debug("Periodic trigger snapshot complete")
        except Exception as e:
            logger.error("Snapshot error: %s", e)


async def run(config: AlfredConfig) -> None:
    """Main Trigger Engine loop."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    r: aioredis.Redis = aioredis.from_url(config.redis_url)

    store = TriggerStore(redis=r, snapshot_dir=SNAPSHOT_DIR)
    triggers = await store.load()
    logger.info("Loaded %d triggers", len(triggers))

    engine = TriggerEngine(store=store, redis=r)

    # Register CRUD tools via public AlfredClient API
    client = AlfredClient(
        service_name="trigger-engine",
        service_endpoint="http://localhost:8001",
        redis_url=config.redis_url,
    )
    ctx = TriggerFeatureContext(store=store, redis=r)
    client.discover_features_from_classes([TriggerFeature], ctx=ctx)
    await client.register()
    logger.info("Registered trigger CRUD tools in tool registry")

    # Start concurrent tasks
    from core.triggers.server import run_server

    tasks = [
        asyncio.create_task(tick_loop(engine)),
        asyncio.create_task(event_loop(engine, r)),
        asyncio.create_task(snapshot_loop(store)),
        asyncio.create_task(run_server(client, port=8001)),
    ]

    logger.info("Trigger Engine started")

    try:
        await _shutdown.wait()
    finally:
        logger.info("Shutting down Trigger Engine...")
        for t in tasks:
            t.cancel()
        await client.unregister()
        await r.aclose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    config = AlfredConfig.from_env()
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
