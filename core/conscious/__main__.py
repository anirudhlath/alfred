"""Entry point for the Conscious Engine service.

Usage: python -m core.conscious
"""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import redis.asyncio as aioredis

# Import modules to trigger @register decorators
import core.integrations.apple_calendar
import core.integrations.apple_health
import core.integrations.robinhood
import core.integrations.weather
import core.triggers.types  # noqa: F401  # trigger type registrations
from bus.schemas.events import ActionRequest, UserRequest
from core.conscious.context_assembler import ContextAssembler
from core.conscious.cost import CostTracker
from core.conscious.engine import ConsciousConfig, ConsciousDeps, ConsciousEngine
from core.conscious.identity import IdentityGate
from core.conscious.session import SessionManager
from core.memory.context_index import ContextIndexManager
from core.memory.embedding_provider import SentenceTransformerProvider
from core.memory.episodic.memory import EpisodicMemory
from core.memory.redis_vector_store import RedisVectorStore
from core.memory.routines.store import RoutineStore
from core.memory.scratchpad_writer import ScratchpadWriter
from core.memory.significance import SignificanceScorer
from core.memory.sqlite_vec_store import SqliteVecStore
from core.notifications.publisher import NotificationPublisher
from core.reflex.context_reader import ContextReader
from core.reflex.runner import ensure_consumer_group
from core.reflex.tool_registry import ToolRegistry
from core.routing.domain_router import DomainRouter
from core.triggers.feature import TriggerFeature, TriggerFeatureContext
from core.triggers.store import TriggerStore
from core.warmup import start_warmup
from domains.home.home_agent import HomeAgent
from shared.config import AlfredConfig
from shared.logging import configure_logging
from shared.otel import init_tracing
from shared.streams import (
    ACTIONS_STREAM,
    USER_REQUESTS_STREAM,
    USER_RESPONSES_STREAM,
    decode_stream_value,
)

if TYPE_CHECKING:
    from shared.types import AioRedis

_shutdown = asyncio.Event()

# Internal action handlers: tool_name → async callable
_INTERNAL_HANDLERS: dict[str, Any] = {}


def _handle_signal() -> None:
    _shutdown.set()


async def _consume_internal_actions(
    redis: AioRedis,
    log: Any,
) -> None:
    """Background consumer for ACTIONS_STREAM targeting 'conscious-engine'.

    Dispatches to registered internal handlers (e.g. drain_deferred_notifications).
    Other target_services are ignored — they belong to domain agents.
    """
    stream = ACTIONS_STREAM
    group = "conscious-engine"
    consumer = "internal-actions-1"

    await ensure_consumer_group(redis, stream, group)

    while not _shutdown.is_set():
        try:
            entries: list[
                tuple[
                    bytes | str,
                    list[tuple[bytes | str, dict[bytes | str, bytes | str]]],
                ]
            ] = await redis.xreadgroup(  # type: ignore[misc,unused-ignore]
                group, consumer, {stream: ">"}, count=1, block=5000
            )

            for _stream_key, stream_entries in entries:
                for entry_id, entry_data in stream_entries:
                    raw = entry_data.get("event") or entry_data.get(b"event")
                    if raw is None:
                        await redis.xack(stream, group, entry_id)
                        continue

                    event_str = decode_stream_value(raw)
                    action = ActionRequest.model_validate_json(event_str)

                    if action.target_service != "conscious-engine":
                        # Not for us — ack and skip
                        await redis.xack(stream, group, entry_id)
                        continue

                    handler = _INTERNAL_HANDLERS.get(action.tool_name)
                    if handler is not None:
                        try:
                            await handler()
                        except Exception as e:
                            log.error("Internal action '{}' failed: {}", action.tool_name, e)
                    else:
                        log.warning("No handler for internal action '{}'", action.tool_name)

                    await redis.xack(stream, group, entry_id)
        except Exception as e:
            if not _shutdown.is_set():
                log.error("Internal action consumer error: {}", e)
                await asyncio.sleep(1)


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
    routine_store = RoutineStore(
        routines_dir=str(memory_dir / "routines"),
    )
    # New memory system (Phase 3): embedding-backed episodic + context index
    embedder = None
    context_index = None
    episodic_memory = None
    significance_scorer = None
    hot_store = None
    cold_store = None
    try:
        embedder = SentenceTransformerProvider(config.embedding_model)
        hot_store = RedisVectorStore(redis=r, dim=config.embedding_dim)
        cold_store = SqliteVecStore(
            db_path=str(memory_dir / "episodic_cold.db"),
            dim=config.embedding_dim,
        )
        episodic_memory = EpisodicMemory(hot=hot_store, cold=cold_store, embedder=embedder)
        context_index = ContextIndexManager(
            store=hot_store,
            embedder=embedder,
            semantic_dirs=[
                memory_dir / "preferences",
                memory_dir / "profile",
            ],
        )
        significance_scorer = SignificanceScorer(redis=r, config=config)
        log.info(
            "Memory system initialized (model={}, dim={})",
            config.embedding_model,
            config.embedding_dim,
        )
    except Exception as exc:
        log.error("Memory system failed to initialize — running without memory: {}", exc)

    # Load memory components in the background so the first request doesn't pay
    # the embedding-model lazy-load cost; lazy init remains the fallback.
    warmup_task: asyncio.Task[None] | None = None
    if embedder is not None and hot_store is not None and cold_store is not None:
        warm_embedder, warm_hot, warm_cold = embedder, hot_store, cold_store
        warmup_task = start_warmup(
            "conscious",
            {
                "embedding model": lambda: warm_embedder.embed("warmup"),
                "redis vector index": warm_hot.ensure_index,
                "sqlite cold store": warm_cold._get_db,
            },
        )

    # Import only Signal adapter — WebSocket + Voice are delivered by the channels process
    import core.notifications.adapters.signal
    from core.channels.signal_bridge.bridge import SignalBridge
    from core.notifications.channels import ChannelRegistry
    from core.notifications.dispatcher import NotificationDispatcher
    from core.notifications.dnd import DNDChecker

    # Try to get calendar adapter for DND checks (optional)
    calendar_adapter = None
    try:
        from core.integrations.registry import IntegrationRegistry

        calendar_adapter = IntegrationRegistry.get("apple_calendar")
    except KeyError:
        log.info("Calendar adapter not available — DND calendar checks disabled")

    # Trigger store — created early so dispatcher can schedule drain triggers
    trigger_store = TriggerStore(
        redis=r,
        snapshot_dir=str(Path(__file__).resolve().parent.parent / "memory" / "triggers"),
    )
    # Publish/subscribe cache coherence: mutations here (e.g. new reminders)
    # poke the trigger process's scheduler instantly via pub/sub.
    await trigger_store.start_sync()

    dnd_checker = DNDChecker(redis=r, calendar_adapter=calendar_adapter)
    dispatcher = NotificationDispatcher(
        redis=r, dnd_checker=dnd_checker, trigger_store=trigger_store
    )

    # Inject pre-built adapter instances that need constructor args.
    # Signal adapter lives here; WebSocket + Voice in the channels process.
    # Notifications reach all channels via the dispatch stream (each process
    # runs a delivery worker with its own consumer group).
    signal_bridge = SignalBridge(redis=r, phone_number=config.signal_phone_number)
    ChannelRegistry.set_instance(
        "signal",
        core.notifications.adapters.signal.SignalChannelAdapter(
            bridge=signal_bridge, recipient=config.signal_phone_number
        ),
    )

    notifier = NotificationPublisher(dispatcher=dispatcher)

    # Register internal action handlers for ACTIONS_STREAM consumption
    _INTERNAL_HANDLERS["drain_deferred_notifications"] = dispatcher.drain_deferred

    cost_tracker = CostTracker(redis=r, daily_cap_usd=config.daily_cost_cap_usd, notifier=notifier)

    # Trigger feature — system-level, called directly (not via HTTP)
    trigger_feature = TriggerFeature(TriggerFeatureContext(store=trigger_store, redis=r))

    engine = ConsciousEngine(
        deps=ConsciousDeps(
            redis=r,
            identity_gate=IdentityGate(registered_phone=config.signal_phone_number),
            session_mgr=SessionManager(redis=r, timeout_minutes=config.session_timeout_minutes),
            cost_tracker=cost_tracker,
            context_assembler=ContextAssembler(),
            domain_router=router,
            tool_registry=ToolRegistry(r),
            context_reader=ContextReader(redis=r),
            routine_store=routine_store,
            trigger_feature=trigger_feature,
            embedder=embedder,
            context_index=context_index,
            config=ConsciousConfig(
                model=config.claude_model,
                api_key=config.claude_api_key,
                max_tokens=config.claude_max_tokens,
                involuntary_recall_limit=config.involuntary_recall_limit,
                involuntary_recall_threshold=config.involuntary_recall_threshold,
            ),
        )
    )

    # Start scratchpad writer as a background task so observations drain to disk
    # even when the Conscious Engine runs standalone (without the Reflex Runner).
    scratchpad_writer = ScratchpadWriter(
        redis=r,
        scratchpad_path=str(memory_dir / "scratchpad.md"),
    )
    writer_task = asyncio.create_task(scratchpad_writer.run())

    # Start Librarian scheduler as a background task to consolidate scratchpad
    # into structured memory on a periodic interval (default: 1hr).
    librarian_task: asyncio.Task[None] | None = None
    if (
        episodic_memory is not None
        and significance_scorer is not None
        and context_index is not None
    ):
        try:
            from core.librarian.consolidator import Librarian
            from core.librarian.scheduler import LibrarianScheduler

            librarian = Librarian(
                redis=r,
                episodic_memory=episodic_memory,
                routine_store=routine_store,
                significance_scorer=significance_scorer,
                context_index=context_index,
                preferences_dir=str(memory_dir / "preferences"),
                profile_dir=str(memory_dir / "profile"),
                claude_api_key=config.claude_api_key,
                claude_model=config.claude_model,
            )
            librarian_scheduler = LibrarianScheduler(
                librarian=librarian,
                interval_seconds=float(os.getenv("LIBRARIAN_INTERVAL_SECONDS", "3600")),
            )
            librarian_task = asyncio.create_task(librarian_scheduler.run())

            async def _run_librarian_now() -> None:
                summary = await librarian.consolidate()
                log.info("Manual Librarian run complete: {}", summary)

            _INTERNAL_HANDLERS["run_librarian"] = _run_librarian_now
        except Exception as exc:
            log.error("Librarian failed to initialize — running without consolidation: {}", exc)
    else:
        log.warning("Librarian skipped — memory system unavailable")

    # Start internal actions consumer (handles drain_deferred_notifications from triggers)
    internal_actions_task = asyncio.create_task(_consume_internal_actions(r, log))

    # Start proactive routine suggestion checker (every 15 minutes)
    routine_suggestion_task: asyncio.Task[None] | None = None
    if engine.has_routine_store:

        async def _routine_suggestion_loop() -> None:
            while not _shutdown.is_set():
                try:
                    await engine.check_routine_suggestions(notifier=notifier)
                except Exception as exc:
                    log.error("Routine suggestion check failed: {}", exc)
                # Wait 15 minutes or until shutdown (no polling)
                try:
                    await asyncio.wait_for(_shutdown.wait(), timeout=900)
                    return  # shutdown signalled
                except TimeoutError:
                    pass  # 15 min elapsed, loop again

        routine_suggestion_task = asyncio.create_task(_routine_suggestion_loop())

    # Start notification delivery worker (delivers via Signal adapter in this process)
    from core.notifications.delivery import notification_delivery_worker

    delivery_task = asyncio.create_task(
        notification_delivery_worker(r, group="conscious-delivery", shutdown=_shutdown)
    )

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
                            await r.xack(stream, group, entry_id)
                            continue
                        event_str = decode_stream_value(raw)
                        request = UserRequest.model_validate_json(event_str)

                        response = await engine.process_request(request)

                        await r.xadd(  # type: ignore[misc,unused-ignore]
                            USER_RESPONSES_STREAM,
                            {"event": response.model_dump_json()},
                        )
                        await r.xack(stream, group, entry_id)

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
                        log.info("Reclaimed {} stale PEL messages", len(claimed[1]))
                except Exception:
                    pass  # xautoclaim not supported on older Redis — skip gracefully
    finally:
        log.info("Shutting down Conscious Engine...")
        if warmup_task is not None:
            warmup_task.cancel()
        writer_task.cancel()
        if librarian_task is not None:
            librarian_task.cancel()
        internal_actions_task.cancel()
        if routine_suggestion_task is not None:
            routine_suggestion_task.cancel()
        delivery_task.cancel()
        await trigger_store.stop_sync()
        await r.aclose()


def main() -> None:
    config = AlfredConfig.from_env()
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
