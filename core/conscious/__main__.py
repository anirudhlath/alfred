"""Entry point for the Conscious Engine service.

Usage: python -m core.conscious
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis

# Import modules to trigger @register decorators
import core.integrations.apple_calendar
import core.integrations.apple_health
import core.integrations.robinhood
import core.integrations.weather
import core.triggers.types  # noqa: F401  # trigger type registrations
from bus.schemas.events import UserRequest
from core.conscious.context_assembler import ContextAssembler
from core.conscious.cost import CostTracker
from core.conscious.engine import ConsciousEngine
from core.conscious.identity import IdentityGate
from core.conscious.memory_reader import MemoryReader
from core.conscious.session import SessionManager
from core.memory.episodic.store import EpisodicStore
from core.memory.routines.store import RoutineStore
from core.memory.scratchpad_writer import ScratchpadWriter
from core.notifications.publisher import NotificationPublisher
from core.reflex.context_reader import ContextReader
from core.reflex.runner import AioRedis, ensure_consumer_group
from core.reflex.tool_registry import ToolRegistry
from core.routing.domain_router import DomainRouter
from core.triggers.feature import TriggerFeature, TriggerFeatureContext
from core.triggers.store import TriggerStore
from domains.home.home_agent import HomeAgent
from shared.config import AlfredConfig
from shared.logging import configure_logging
from shared.otel import init_tracing
from shared.streams import USER_REQUESTS_STREAM, USER_RESPONSES_STREAM

_shutdown = asyncio.Event()


def _handle_signal() -> None:
    _shutdown.set()


async def run(config: AlfredConfig) -> None:
    log = configure_logging(service="conscious")
    init_tracing(
        service_name="conscious",
        endpoint=config.otel_endpoint if config.signoz_enabled else None,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    r: AioRedis = aioredis.from_url(config.redis_url)

    stream = USER_REQUESTS_STREAM
    group = "conscious-engine"
    consumer = "worker-1"

    await ensure_consumer_group(r, stream, group)

    # Setup components
    router = DomainRouter()
    router.register("home-service", HomeAgent(redis=r))

    # Memory components
    memory_dir = Path(__file__).resolve().parent.parent / "memory"
    episodic_store = EpisodicStore(
        redis=r,
        db_path=str(memory_dir / "episodic.db"),
        hot_days=config.episodic_hot_days,
    )
    routine_store = RoutineStore(
        routines_dir=str(memory_dir / "routines"),
    )
    memory_reader = MemoryReader(
        preferences_dir=memory_dir / "preferences",
        profile_dir=memory_dir / "profile",
        default_proactivity=config.proactivity_level,
    )

    notifier = NotificationPublisher(redis=r)
    cost_tracker = CostTracker(redis=r, daily_cap_usd=config.daily_cost_cap_usd, notifier=notifier)

    # Trigger feature — system-level, called directly (not via HTTP)
    trigger_store = TriggerStore(
        redis=r,
        snapshot_dir=str(Path(__file__).resolve().parent.parent / "memory" / "triggers"),
    )
    trigger_feature = TriggerFeature(TriggerFeatureContext(store=trigger_store, redis=r))

    engine = ConsciousEngine(
        redis=r,
        identity_gate=IdentityGate(registered_phone=config.signal_phone_number),
        session_mgr=SessionManager(redis=r, timeout_minutes=config.session_timeout_minutes),
        cost_tracker=cost_tracker,
        context_assembler=ContextAssembler(),
        domain_router=router,
        tool_registry=ToolRegistry(r),
        context_reader=ContextReader(redis=r),
        claude_model=config.claude_model,
        claude_api_key=config.claude_api_key,
        memory_reader=memory_reader,
        episodic_store=episodic_store,
        routine_store=routine_store,
        trigger_feature=trigger_feature,
    )

    # Start scratchpad writer as a background task so observations drain to disk
    # even when the Conscious Engine runs standalone (without the Reflex Runner).
    scratchpad_writer = ScratchpadWriter(
        redis=r,
        scratchpad_path=str(memory_dir / "scratchpad.md"),
    )
    writer_task = asyncio.create_task(scratchpad_writer.run())

    log.info("Conscious Engine started. Listening on '{}'...", stream)

    pel_counter = 0  # Check PEL every N iterations

    try:
        while not _shutdown.is_set():
            entries: list[
                tuple[
                    bytes | str,
                    list[tuple[bytes | str, dict[bytes | str, bytes | str]]],
                ]
            ] = await r.xreadgroup(  # type: ignore[misc,unused-ignore]
                group, consumer, {stream: ">"}, count=1, block=5000
            )

            for _stream_key, stream_entries in entries:
                for entry_id, entry_data in stream_entries:
                    try:
                        raw = entry_data.get("event") or entry_data.get(b"event")
                        if raw is None:
                            await r.xack(stream, group, entry_id)  # type: ignore[no-untyped-call]
                            continue
                        event_str = raw.decode() if isinstance(raw, bytes) else raw
                        request = UserRequest.model_validate_json(event_str)

                        response = await engine.process_request(request)

                        await r.xadd(  # type: ignore[misc,unused-ignore]
                            USER_RESPONSES_STREAM,
                            {"event": response.model_dump_json()},
                        )
                        await r.xack(stream, group, entry_id)  # type: ignore[no-untyped-call]

                        # Check budget alert after successful processing
                        await cost_tracker.send_alert_if_needed()
                    except Exception as e:
                        log.error("Error processing request {}: {}", entry_id, e)
                        # Message stays in PEL for recovery — no xack on failure

            # Periodically reclaim stale pending messages (PEL recovery)
            pel_counter += 1
            if pel_counter >= 12:  # ~every 60s at 5s block
                pel_counter = 0
                try:
                    claimed: Any = await r.xautoclaim(  # type: ignore[misc,unused-ignore]
                        stream, group, consumer, min_idle_time=60000, start_id="0-0", count=5
                    )
                    if claimed and len(claimed) > 1 and claimed[1]:
                        log.info("Reclaimed %d stale PEL messages", len(claimed[1]))
                except Exception:
                    pass  # xautoclaim not supported on older Redis — skip gracefully
    finally:
        log.info("Shutting down Conscious Engine...")
        writer_task.cancel()
        await r.close()


def main() -> None:
    config = AlfredConfig.from_env()
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
