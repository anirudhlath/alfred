"""Entry point for the Trigger Engine service.

Usage: python -m core.triggers
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from shared.types import AioRedis

import redis.asyncio as aioredis
import uvicorn

import core.triggers.types  # noqa: F401  — register all trigger types
from bus.schemas.events import ActionRequest
from core.reflex.runner import ensure_consumer_group
from core.triggers.engine import TriggerEngine
from core.triggers.feature import TriggerFeature, TriggerFeatureContext
from core.triggers.models import BaseTrigger, TriggerContext
from core.triggers.server import create_app
from core.triggers.store import TriggerStore
from sdk.alfred_sdk.client import AlfredClient
from shared.config import AlfredConfig
from shared.logging import configure_logging
from shared.streams import ACTIONS_STREAM, HOME_STATE_STREAM, decode_stream_value

logger = logging.getLogger(__name__)

GROUP = "trigger-engine"
CONSUMER = "worker-1"
SNAPSHOT_DIR = Path("core/memory/triggers")

# ACTIONS_STREAM consumer (internal trigger actions from admin API). A distinct
# consumer GROUP so this process sees every entry independently of the home-agent
# and conscious-engine groups; we only act on target_service="trigger-engine"
# entries and ack-and-skip everything else.
ACTIONS_GROUP = "triggers-internal"
ACTIONS_CONSUMER = "worker-1"
TARGET_SERVICE = "trigger-engine"

_shutdown = asyncio.Event()


async def _resolve_trigger(store: TriggerStore, trigger_id: str, action: str) -> BaseTrigger | None:
    """Fetch a trigger by id, refreshing the cache once on a miss.

    The trigger may have been created in the conscious process and written to
    Redis <60 s ago, before this process's cache last refreshed. One targeted
    refresh is cheap and closes that cross-process visibility window. Returns
    None (and logs a warning) if the trigger still cannot be found.
    """
    trigger = await store.get(trigger_id)
    if trigger is None:
        await store.refresh()
        trigger = await store.get(trigger_id)
    if trigger is None:
        logger.warning("%s: unknown trigger '%s'", action, trigger_id)
    return trigger


async def _handle_fire_trigger(
    store: TriggerStore, engine: TriggerEngine, parameters: dict[str, object]
) -> None:
    """Fire a trigger by id via the real TriggerEngine (YAML-consistent)."""
    trigger_id = str(parameters.get("trigger_id", ""))
    trigger = await _resolve_trigger(store, trigger_id, "fire_trigger")
    if trigger is None:
        return
    await engine.fire(trigger, TriggerContext(now=datetime.now(UTC)), fired_by="admin")


async def _handle_set_trigger_enabled(store: TriggerStore, parameters: dict[str, object]) -> None:
    """Toggle a trigger's enabled flag via TriggerStore (Redis + YAML)."""
    trigger_id = str(parameters.get("trigger_id", ""))
    enabled = bool(parameters.get("enabled", False))
    trigger = await _resolve_trigger(store, trigger_id, "set_trigger_enabled")
    if trigger is None:
        return
    await store.save(trigger.model_copy(update={"enabled": enabled}))
    logger.info("Trigger '%s' enabled=%s (admin)", trigger_id, enabled)


async def actions_loop(store: TriggerStore, engine: TriggerEngine, r: AioRedis) -> None:
    """Consume internal trigger actions (fire/enable) from ACTIONS_STREAM.

    Mirrors the conscious-engine internal action consumer: a dedicated group,
    XREADGROUP loop, target_service filter, dispatch by tool_name, ack always.
    """
    await ensure_consumer_group(r, ACTIONS_STREAM, ACTIONS_GROUP)

    while not _shutdown.is_set():
        try:
            entries = await r.xreadgroup(
                ACTIONS_GROUP, ACTIONS_CONSUMER, {ACTIONS_STREAM: ">"}, count=10, block=5000
            )
        except Exception as e:
            if not _shutdown.is_set():
                logger.error("Actions read error: %s", e)
                await asyncio.sleep(1)
            continue

        for _stream_key, stream_entries in entries:
            for entry_id, entry_data in stream_entries:
                # A transient Redis error (e.g. a blip inside xack) must not escape and
                # kill the consumer task permanently — log and move on to the next entry.
                try:
                    await _process_action_entry(store, engine, r, entry_id, entry_data)
                except Exception as e:
                    logger.error("Trigger action entry %s failed: %s", entry_id, e)


async def _process_action_entry(
    store: TriggerStore,
    engine: TriggerEngine,
    r: AioRedis,
    entry_id: str,
    entry_data: dict[Any, Any],
) -> None:
    raw_event = entry_data.get("event") or entry_data.get(b"event")
    if raw_event is None:
        await r.xack(ACTIONS_STREAM, ACTIONS_GROUP, entry_id)
        return

    try:
        action = ActionRequest.model_validate_json(decode_stream_value(raw_event))
    except Exception:
        await r.xack(ACTIONS_STREAM, ACTIONS_GROUP, entry_id)
        return

    if action.target_service != TARGET_SERVICE:
        # Not for us — ack and skip (other groups handle their own).
        await r.xack(ACTIONS_STREAM, ACTIONS_GROUP, entry_id)
        return

    if action.tool_name == "fire_trigger":
        await _handle_fire_trigger(store, engine, action.parameters)
    elif action.tool_name == "set_trigger_enabled":
        await _handle_set_trigger_enabled(store, action.parameters)
    else:
        logger.warning("No handler for trigger action '%s'", action.tool_name)

    await r.xack(ACTIONS_STREAM, ACTIONS_GROUP, entry_id)


def _free_port(port: int) -> None:
    """Kill any process holding *port* so we can bind cleanly."""
    try:
        out = subprocess.check_output(
            ["lsof", "-ti", f":{port}"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return  # nothing on the port or lsof unavailable

    my_pid = os.getpid()
    for pid_str in out.splitlines():
        pid = int(pid_str)
        if pid == my_pid:
            continue
        logger.warning("Killing stale process %d on port %d", pid, port)
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)


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
    r: AioRedis,
) -> None:
    """Event listener loop for sensor-based trigger evaluation.

    Consumes HOME_STATE_STREAM — the stream the MQTT bridge publishes real
    state changes to. (EVENTS_STREAM only carries the engine's own
    TriggerFired/TriggerCreated events, which are not sensor input.)
    """
    await ensure_consumer_group(r, HOME_STATE_STREAM, GROUP)

    while not _shutdown.is_set():
        try:
            entries = await r.xreadgroup(
                GROUP, CONSUMER, {HOME_STATE_STREAM: ">"}, count=10, block=5000
            )
        except Exception as e:
            logger.error("Event read error: %s", e)
            continue

        for _stream_key, stream_entries in entries:
            for entry_id, entry_data in stream_entries:
                await _process_event_entry(engine, r, entry_id, entry_data)


async def _process_event_entry(
    engine: TriggerEngine,
    r: AioRedis,
    entry_id: str,
    entry_data: dict[Any, Any],
) -> None:
    from bus.schemas.events import StateChangedEvent

    raw_event = entry_data.get("event") or entry_data.get(b"event")
    if raw_event is None:
        logger.warning("Event entry %s has no 'event' field — skipping", entry_id)
        await r.xack(HOME_STATE_STREAM, GROUP, entry_id)
        return

    try:
        event = StateChangedEvent.model_validate_json(decode_stream_value(raw_event))
    except Exception as e:
        logger.warning(
            "Event entry %s is not a valid StateChangedEvent (%s) — skipping", entry_id, e
        )
        await r.xack(HOME_STATE_STREAM, GROUP, entry_id)
        return

    try:
        await engine.evaluate_event(event)
    except Exception as e:
        logger.error("Event evaluation error: %s", e)

    await r.xack(HOME_STATE_STREAM, GROUP, entry_id)


async def _periodic(
    fn: Callable[[], Awaitable[None]],
    interval: float,
    label: str,
) -> None:
    """Generic periodic task loop — sleeps, calls fn, logs."""
    while not _shutdown.is_set():
        await asyncio.sleep(interval)
        try:
            await fn()
            logger.debug("%s complete", label)
        except Exception as e:
            logger.error("%s error: %s", label, e)


async def run(config: AlfredConfig) -> None:
    """Main Trigger Engine loop."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    r: AioRedis = aioredis.from_url(config.redis_url)

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
    features = client.discover_features_from_classes([TriggerFeature], ctx=ctx)
    assert isinstance(features[0], TriggerFeature)
    feature: TriggerFeature = features[0]
    await client.register()
    logger.info("Registered trigger CRUD tools in tool registry")

    # Build FastAPI app + uvicorn server (kill stale port holders, retry bind)
    app = create_app(client=client, feature=feature)
    trigger_port = int(os.getenv("TRIGGER_PORT", "8001"))

    _free_port(trigger_port)

    uvi_config = uvicorn.Config(app, host="0.0.0.0", port=trigger_port, log_level="info")
    uvi_server = uvicorn.Server(uvi_config)

    async def _serve_with_retry() -> None:
        for attempt in range(5):
            try:
                server = uvicorn.Server(uvi_config) if attempt > 0 else uvi_server
                await server.serve()
                return
            except (OSError, SystemExit) as e:
                is_addr_in_use = (isinstance(e, OSError) and e.errno == 48) or (
                    isinstance(e, SystemExit) and e.code != 0
                )
                if is_addr_in_use and attempt < 4:
                    wait = attempt + 1
                    logger.warning("Port %d in use, retrying in %ds...", trigger_port, wait)
                    _free_port(trigger_port)
                    await asyncio.sleep(wait)
                else:
                    raise

    tasks = [
        asyncio.create_task(tick_loop(engine)),
        asyncio.create_task(event_loop(engine, r)),
        asyncio.create_task(actions_loop(store, engine, r)),
        asyncio.create_task(_periodic(store.snapshot_all, 300.0, "Trigger snapshot")),
        asyncio.create_task(_periodic(store.refresh, 60.0, "Cache refresh")),
        asyncio.create_task(_serve_with_retry()),
    ]

    logger.info("Trigger Engine started")

    try:
        await _shutdown.wait()
    finally:
        logger.info("Shutting down Trigger Engine...")
        uvi_server.should_exit = True
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.unregister()
        await r.aclose()


def main() -> None:
    configure_logging(service="triggers")
    config = AlfredConfig.from_env()
    from shared.otel import init_tracing

    init_tracing(
        service_name="triggers",
        endpoint=config.otel_endpoint if config.signoz_enabled else None,
    )
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
