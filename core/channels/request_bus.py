"""Shared request/response bus helper for user-facing channels."""

from __future__ import annotations

import time

from loguru import logger

from bus.schemas.events import AlfredResponse, UserRequest
from shared.redis_streams import read, revrange
from shared.streams import USER_REQUESTS_STREAM, USER_RESPONSES_STREAM, decode_stream_value
from shared.types import AioRedis  # noqa: TC001


async def publish_and_wait(
    redis: AioRedis,
    request: UserRequest,
    session_id: str,
    timeout: float = 30.0,
) -> AlfredResponse:
    """Publish request and block-read the responses stream for a matching response.

    Anchors the XREAD lower-bound to the response stream's own last entry ID
    (via XREVRANGE) instead of a wall-clock timestamp, to avoid scanning
    history. XREAD's lower bound is EXCLUSIVE — it only returns entries with
    an ID strictly greater than what's given. A wall-clock-derived ID like
    f"{now_ms}-0" can collide with a response Redis auto-assigns the exact
    same ID (its own "-0" sequence slot) in that same millisecond, which
    silently drops that response and burns the full timeout. It's also a
    clock-skew hazard, since Redis assigns stream IDs from ITS clock, not the
    caller's. Reading the stream's actual tail sidesteps both: "0-0" on an
    empty/nonexistent stream is correct here since there's no history to scan.
    Returns the full AlfredResponse so callers can forward actions_taken and mood.
    """
    tail = await revrange(redis, USER_RESPONSES_STREAM, count=1)
    tail_id = tail[0][0] if tail else None
    last_id = decode_stream_value(tail_id) if tail_id is not None else "0-0"

    await redis.xadd(USER_REQUESTS_STREAM, {"event": request.model_dump_json()})

    start = time.monotonic()
    while (time.monotonic() - start) < timeout:
        entries = await read(redis, {USER_RESPONSES_STREAM: last_id}, count=10, block=1000)
        for _stream, stream_entries in entries:
            for entry_id, entry_data in stream_entries:
                last_id = decode_stream_value(entry_id)
                raw = entry_data.get(b"event") or entry_data.get("event")
                if raw:
                    resp = AlfredResponse.model_validate_json(decode_stream_value(raw))
                    if resp.session_id == session_id:
                        return resp

    logger.warning(
        "No response for session {} within {}s timeout — returning fallback", session_id, timeout
    )
    return AlfredResponse(
        source="channels",
        channel=request.channel,
        session_id=session_id,
        text="I apologize, sir — I seem to be taking longer than expected.",
    )
