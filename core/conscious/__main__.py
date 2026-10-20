"""Entry point for the Conscious Engine service.

Usage: python -m core.conscious
"""

from __future__ import annotations

import asyncio
import os
import signal
from functools import partial
from typing import TYPE_CHECKING, Any, Final

# Import modules to trigger @register decorators
import core.integrations.apple_calendar
import core.integrations.apple_health
import core.integrations.robinhood
import core.integrations.weather
import core.triggers.types  # noqa: F401  # trigger type registrations
from bus.schemas.events import ActionRequest, ActionResult
from core.conscious.agent import build_system2_agent, system2_toolsets
from core.conscious.approval_classifier import ApprovalClassifier
from core.conscious.engine import ConsciousConfig, ConsciousDeps, ConsciousEngine
from core.conscious.identity import IdentityGate
from core.conscious.runner import process_approval_entry, process_request_entry
from core.conscious.session import SessionManager
from core.memory.context_index import ContextIndexManager
from core.memory.embedding_backend import build_embedding_provider
from core.memory.episodic.memory import EpisodicMemory
from core.memory.paths import (
    episodic_cold_path,
    preferences_dir,
    profile_dir,
    triggers_snapshot_dir,
)
from core.memory.redis_vector_store import RedisVectorStore
from core.memory.routines.store import RoutineStore
from core.memory.scratchpad_writer import ScratchpadWriter
from core.memory.significance import SignificanceScorer
from core.memory.sqlite_vec_store import SqliteVecStore
from core.notifications.publisher import NotificationPublisher
from core.notifications.schema import Urgency
from core.reflex.context_reader import ContextReader
from core.reflex.runner import ensure_consumer_group
from core.reflex.tool_registry import ToolRegistry
from core.routing.action_results import publish_action_result
from core.routing.domain_router import DomainRouter
from core.shutdown import teardown
from core.triggers.feature import TriggerFeature, TriggerFeatureContext
from core.triggers.store import TriggerStore
from core.warmup import start_warmup
from domains.home.home_agent import HomeAgent
from shared.agents import resolve_model
from shared.config import AlfredConfig, apply_model_env
from shared.logging import configure_logging
from shared.otel import init_tracing
from shared.prompts.store import PromptStore
from shared.redis_streams import create_redis, read_group, reclaim_due
from shared.spend import ALERT_SOURCE, alert_if_needed, build_spend_limits, local_clock
from shared.streams import (
    ACTIONS_STREAM,
    APPROVALS_STREAM,
    USER_REQUESTS_STREAM,
    decode_stream_value,
)
from shared.usertime import last_user_timezone

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from core.conscious.runner import EntryRunner
    from shared.types import AioRedis

_shutdown = asyncio.Event()

# Internal action handlers: tool_name → async callable
_INTERNAL_HANDLERS: dict[str, Any] = {}

# The source of a confirmed action's outcome: its notification, or a failure result.
OUTCOME_SOURCE = "conscious-engine"

# The streams the conscious loop answers, each with the runner function for its entries:
# a user's request, and the owner's decision from the Door on a parked run.
_ENTRY_RUNNERS: Final[dict[str, EntryRunner]] = {
    USER_REQUESTS_STREAM: process_request_entry,
    APPROVALS_STREAM: process_approval_entry,
}


def _handle_signal() -> None:
    _shutdown.set()


async def _report_outcome(
    redis: AioRedis,
    log: Any,
    notifier: NotificationPublisher,
    action: ActionRequest,
    result: ActionResult,
) -> None:
    """Tell the clients and the owner how a confirmed action went.

    The result goes to the stream the PWA's Door waits on; the owner is notified either
    way, and a failure is logged at WARNING. Neither delivery can fail the consumer: the
    action has already run, so its entry is acked regardless.
    """
    if result.status == "error":
        log.warning(
            "Confirmed action '{}' on {} failed: {}",
            action.tool_name,
            action.target_service,
            result.error,
        )
        title = "That didn't work"
        body = (
            f"'{action.tool_name}' on {action.target_service} failed: "
            f"{result.error or 'no reason given'}"
        )
    else:
        log.info("Confirmed action '{}' executed (status={})", action.tool_name, result.status)
        title = "Done"
        body = f"I ran '{action.tool_name}' on {action.target_service}, sir."
    try:
        await publish_action_result(redis, result)
    except Exception as e:
        log.error("Could not publish the result of confirmed action '{}': {}", action.tool_name, e)
    try:
        await notifier.publish(
            title=title, body=body, source=OUTCOME_SOURCE, urgency=Urgency.IMPORTANT
        )
    except Exception as e:
        log.error("Could not notify the outcome of confirmed action '{}': {}", action.tool_name, e)


async def _consume_internal_actions(
    redis: AioRedis,
    log: Any,
    router: DomainRouter | None,
    notifier: NotificationPublisher,
) -> None:
    """Background consumer for ACTIONS_STREAM.

    Dispatches conscious-engine internal actions (e.g.
    drain_deferred_notifications) to registered handlers, and executes
    CONFIRMED domain actions (republished by the confirmation flow) via the
    DomainRouter — which passes confirmed requests through without
    re-interception — then reports the outcome (result stream + notification).
    All other target_services are acked and skipped.
    """
    stream = ACTIONS_STREAM
    group = "conscious-engine"
    consumer = "internal-actions-1"

    await ensure_consumer_group(redis, stream, group)

    while not _shutdown.is_set():
        try:
            entries = await read_group(redis, group, consumer, {stream: ">"}, count=1, block=5000)

            for _stream_key, stream_entries in entries:
                for entry_id, entry_data in stream_entries:
                    raw = entry_data.get("event") or entry_data.get(b"event")
                    if raw is None:
                        await redis.xack(stream, group, entry_id)
                        continue

                    event_str = decode_stream_value(raw)
                    action = ActionRequest.model_validate_json(event_str)

                    if action.target_service != "conscious-engine":
                        if (
                            router is not None
                            and action.confirmed
                            and action.target_service in router.registered_services
                        ):
                            try:
                                result = await router.route(action)
                            except Exception as e:
                                log.error("Confirmed action '{}' raised: {}", action.tool_name, e)
                                result = ActionResult(
                                    source=OUTCOME_SOURCE,
                                    request_id=action.request_id,
                                    tool_name=action.tool_name,
                                    status="error",
                                    error=str(e) or type(e).__name__,
                                )
                            await _report_outcome(redis, log, notifier, action, result)
                        # Ack either way — not-ours or handled above
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


async def _serve_once(
    redis: AioRedis,
    engine: ConsciousEngine,
    *,
    group: str,
    consumer: str,
    after_answer: Callable[[], Awaitable[object]] | None,
) -> None:
    """One pass of the conscious loop over requests and Door decisions.

    One read covers both streams; then each stream's due redeliveries are reclaimed
    (XREADGROUP '>' never redelivers, so a failed turn left un-ACKed only comes back
    through a reclaim, once its backoff has elapsed). Every entry goes to its stream's
    runner, which caps its deliveries, one at a time: a decision and a turn on the same
    session never race.
    """
    entries = await read_group(
        redis,
        group,
        consumer,
        {stream: ">" for stream in _ENTRY_RUNNERS},
        count=1,
        block=5000,
    )
    # (stream, entry id, fields, delivery): a live read is the first delivery.
    batch = [
        (decode_stream_value(stream_key), entry_id, entry_data, 1)
        for stream_key, stream_entries in entries
        for entry_id, entry_data in stream_entries
    ]
    for stream in _ENTRY_RUNNERS:
        batch.extend(
            (stream, red.entry_id, red.fields, red.delivery)
            for red in await reclaim_due(redis, stream, group, consumer)
        )

    for stream, entry_id, entry_data, delivery in batch:
        await _ENTRY_RUNNERS[stream](
            entry_id,
            entry_data,
            engine=engine,
            redis=redis,
            stream=stream,
            group=group,
            consumer=consumer,
            delivery=delivery,
            after_answer=after_answer,
        )


async def run(config: AlfredConfig) -> None:
    log = configure_logging(service="conscious")
    init_tracing(
        service_name="conscious",
        endpoint=config.otel_endpoint if config.signoz_enabled else None,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    r: AioRedis = create_redis(config.redis_url)
    prompts = PromptStore.from_config(config, redis=r)
    log.info("Prompts: {}", prompts.describe())

    group = "conscious-engine"
    consumer = "worker-1"

    for stream in _ENTRY_RUNNERS:
        await ensure_consumer_group(r, stream, group)

    # Setup components
    # Memory components
    routine_store = RoutineStore()
    # New memory system (Phase 3): embedding-backed episodic + context index
    embedder = None
    context_index = None
    episodic_memory = None
    significance_scorer = None
    hot_store = None
    cold_store = None
    try:
        embedder = build_embedding_provider(config)
        hot_store = RedisVectorStore(redis=r, dim=config.embedding_dim)
        cold_store = SqliteVecStore(
            db_path=str(episodic_cold_path()),
            dim=config.embedding_dim,
        )
        episodic_memory = EpisodicMemory(hot=hot_store, cold=cold_store, embedder=embedder)
        context_index = ContextIndexManager(
            store=hot_store,
            embedder=embedder,
            semantic_dirs=[
                preferences_dir(),
                profile_dir(),
            ],
            archive=cold_store,  # memory_recall_memories reaches what decay moved
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
                "embedding model": warm_embedder.warmup,
                "redis vector index": warm_hot.ensure_index,
                "sqlite cold store": warm_cold._get_db,
            },
        )

    # Import only Signal adapter — WebSocket, APNs and Satellite deliver from the channels process
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
        snapshot_dir=triggers_snapshot_dir(),
    )
    # Warm the cache from Redis so tools see triggers created before this
    # process started, then subscribe: mutations here (e.g. new reminders)
    # poke the trigger process's scheduler instantly via pub/sub.
    await trigger_store.load()
    await trigger_store.start_sync()

    dnd_checker = DNDChecker(redis=r, calendar_adapter=calendar_adapter)
    dispatcher = NotificationDispatcher(
        redis=r, dnd_checker=dnd_checker, trigger_store=trigger_store
    )

    # Inject pre-built adapter instances that need constructor args.
    # Signal adapter lives here; WebSocket, APNs and Satellite in the channels process.
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

    router = DomainRouter(redis=r, notifier=notifier)
    router.register("home-service", HomeAgent(redis=r))

    # Register internal action handlers for ACTIONS_STREAM consumption
    _INTERNAL_HANDLERS["drain_deferred_notifications"] = dispatcher.drain_deferred

    # One budget for the process: System 2 and the in-process Librarian share it, and
    # its Redis counter is shared with every other process. The day is the user's: the
    # zone the engine last resolved for a request (shared.usertime).
    spend = build_spend_limits(
        r, cap_usd=config.daily_cost_cap_usd, clock=local_clock(last_user_timezone)
    )
    alert = partial(alert_if_needed, spend, r, notifier.urgent(ALERT_SOURCE))

    # Trigger feature — system-level, called directly (not via HTTP)
    trigger_feature = TriggerFeature(TriggerFeatureContext(store=trigger_store, redis=r))

    # Resolved once: a missing key or base URL logs one ERROR here, and every request
    # then gets the not-configured reply rather than a crash.
    apply_model_env(config)
    system2 = build_system2_agent(
        toolsets=system2_toolsets(trigger_feature),
        max_tokens=config.system2_max_tokens,
        timeout_s=config.system2_timeout_s,
        spend=spend,
    )
    engine = ConsciousEngine(
        deps=ConsciousDeps(
            redis=r,
            identity_gate=IdentityGate(registered_phone=config.signal_phone_number),
            session_mgr=SessionManager(redis=r, timeout_minutes=config.session_timeout_minutes),
            spend=spend,
            domain_router=router,
            tool_registry=ToolRegistry(r),
            context_reader=ContextReader(redis=r),
            agent=system2,
            model=resolve_model("SYSTEM2_MODEL", config.system2_model),
            notifier=notifier,
            approval=ApprovalClassifier(
                model=resolve_model("APPROVAL_MODEL", config.approval_model),
                prompts=prompts,
                spend=spend,
                max_tokens=config.system2_max_tokens,
                timeout_s=config.system2_timeout_s,
            ),
            routine_store=routine_store,
            context_index=context_index,
            config=ConsciousConfig(
                involuntary_recall_limit=config.involuntary_recall_limit,
                involuntary_recall_threshold=config.involuntary_recall_threshold,
            ),
            prompts=prompts,
        )
    )

    # Start scratchpad writer as a background task so observations drain to disk
    # even when the Conscious Engine runs standalone (without the Reflex Runner).
    scratchpad_writer = ScratchpadWriter(redis=r)
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
                model=resolve_model("LIBRARIAN_MODEL", config.librarian_model),
                spend=spend,
                notifier=notifier,
                prompts=prompts,
                conflict_min_observations=config.conflict_min_observations,
                conflict_min_days=config.conflict_min_days,
                decay_migration_threshold=config.decay_migration_threshold,
                pattern_min_occurrences=config.pattern_min_occurrences,
                pattern_min_days=config.pattern_min_days,
                pattern_confidence_threshold=config.pattern_confidence_threshold,
                routine_decay_per_cycle=config.routine_decay_per_cycle,
                routine_archive_threshold=config.routine_archive_threshold,
                routine_suggestion_cooldown_hours=config.routine_suggestion_cooldown_hours,
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
    internal_actions_task = asyncio.create_task(_consume_internal_actions(r, log, router, notifier))

    # Trigger cache reconciliation net (60s), mirroring the triggers process:
    # pub/sub keeps the cache coherent; the periodic refresh heals any missed message.
    async def _trigger_refresh_loop() -> None:
        while not _shutdown.is_set():
            try:
                await asyncio.wait_for(_shutdown.wait(), timeout=60)
                return  # shutdown signalled
            except TimeoutError:
                pass
            try:
                await trigger_store.refresh()
            except Exception as exc:
                log.error("Trigger cache refresh failed: {}", exc)

    trigger_refresh_task = asyncio.create_task(_trigger_refresh_loop())

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

    log.info("Conscious Engine started. Listening on {}...", ", ".join(_ENTRY_RUNNERS))

    try:
        while not _shutdown.is_set():
            await _serve_once(r, engine, group=group, consumer=consumer, after_answer=alert)
    finally:
        log.info("Shutting down Conscious Engine...")
        # Tasks are awaited, not merely cancelled: librarian_task runs consolidate()
        # -> embed(), so closing the provider while it is still in flight would
        # surface as a warning blaming EMBEDDING_HOST for a close we caused. Closing
        # the provider is unconditional because the EmbeddingProvider type does not
        # say which backend this is — the HTTP one owns a connection pool, the
        # in-process one owns nothing and no-ops.
        await teardown(
            tasks=[
                warmup_task,
                writer_task,
                librarian_task,
                internal_actions_task,
                trigger_refresh_task,
                routine_suggestion_task,
                delivery_task,
            ],
            closers={
                "trigger store sync": trigger_store.stop_sync,
                "embedding provider": embedder.aclose if embedder is not None else None,
                "prompt store": prompts.aclose,
                "redis": r.aclose,
            },
        )


def main() -> None:
    config = AlfredConfig.from_env()
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
