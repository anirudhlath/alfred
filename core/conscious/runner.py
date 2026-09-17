"""Per-entry processing for the Conscious Engine's request loop.

Extracted so the PEL-recovery path and the live path run the *same* code. They
did not: recovery called XAUTOCLAIM and only logged the count, leaving what it
claimed unprocessed and un-ACKed, to be reclaimed again a minute later forever.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from bus.schemas.events import UserRequest
from shared.streams import USER_RESPONSES_STREAM, decode_stream_value

if TYPE_CHECKING:
    from shared.types import AioRedis

__all__ = ["process_request_entry"]


async def process_request_entry(
    entry_id: bytes | str,
    entry_data: dict[bytes | str, bytes | str],
    *,
    engine: Any,
    redis: AioRedis,
    stream: str,
    group: str,
    cost_tracker: Any | None = None,
) -> None:
    """Process one UserRequest entry: answer it, publish, ACK.

    ACKs on success and on entries that can never be parsed — an entry that is
    never ACKed is reclaimed on every recovery pass forever. A genuine processing
    failure (LLM unreachable) is left pending on purpose so the next reclaim can
    retry it.
    """
    raw = entry_data.get("event") or entry_data.get(b"event")
    if raw is None:
        logger.warning("Entry {} has no 'event' field — dropping", entry_id)
        await redis.xack(stream, group, entry_id)
        return

    try:
        request = UserRequest.model_validate_json(decode_stream_value(raw))
    except Exception as exc:
        logger.warning("Entry {} is not a valid UserRequest ({}) — dropping", entry_id, exc)
        await redis.xack(stream, group, entry_id)
        return

    try:
        response = await engine.process_request(request)
        await redis.xadd(  # type: ignore[misc,unused-ignore]
            USER_RESPONSES_STREAM,
            {"event": response.model_dump_json()},
        )
        await redis.xack(stream, group, entry_id)
        if cost_tracker is not None:
            await cost_tracker.send_alert_if_needed()
    except Exception as exc:
        # No ACK — stays pending for the next reclaim pass to retry.
        logger.error("Error processing request {}: {}", entry_id, exc)
