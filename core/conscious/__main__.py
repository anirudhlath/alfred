"""Entry point for the Conscious Engine service.

Usage: python -m core.conscious
"""

from __future__ import annotations

import asyncio
import signal

import redis.asyncio as aioredis

from bus.schemas.events import UserRequest
from core.conscious.context_assembler import ContextAssembler
from core.conscious.cost import CostTracker
from core.conscious.engine import ConsciousEngine
from core.conscious.identity import IdentityGate
from core.conscious.session import SessionManager
from core.reflex.context_reader import ContextReader
from core.reflex.runner import AioRedis, ensure_consumer_group
from core.reflex.tool_registry import ToolRegistry
from core.routing.domain_router import DomainRouter
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

    engine = ConsciousEngine(
        redis=r,
        identity_gate=IdentityGate(registered_phone=config.signal_phone_number),
        session_mgr=SessionManager(redis=r, timeout_minutes=config.session_timeout_minutes),
        cost_tracker=CostTracker(redis=r, daily_cap_usd=config.daily_cost_cap_usd),
        context_assembler=ContextAssembler(),
        domain_router=router,
        tool_registry=ToolRegistry(r),
        context_reader=ContextReader(redis=r),
        claude_model=config.claude_model,
        claude_api_key=config.claude_api_key,
    )

    log.info("Conscious Engine started. Listening on '{}'...", stream)

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
                            continue
                        event_str = raw.decode() if isinstance(raw, bytes) else raw
                        request = UserRequest.model_validate_json(event_str)

                        response = await engine.process_request(request)

                        await r.xadd(  # type: ignore[misc,unused-ignore]
                            USER_RESPONSES_STREAM,
                            {"event": response.model_dump_json()},
                        )
                        await r.xack(stream, group, entry_id)  # type: ignore[no-untyped-call]
                    except Exception as e:
                        log.error("Error processing request {}: {}", entry_id, e)
    finally:
        log.info("Shutting down Conscious Engine...")
        await r.close()


def main() -> None:
    config = AlfredConfig.from_env()
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
