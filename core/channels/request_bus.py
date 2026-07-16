"""Shared request/response bus helper for user-facing channels."""

from __future__ import annotations

import time

from loguru import logger

from bus.schemas.events import AlfredResponse, UserRequest
from shared.streams import USER_REQUESTS_STREAM, USER_RESPONSES_STREAM, decode_stream_value
from shared.types import AioRedis  # noqa: TC001


async def publish_and_wait(
    redis: AioRedis,
    request: UserRequest,
    session_id: str,
    timeout: float = 30.0,
) -> AlfredResponse:
    """Publish request and block-read the responses stream for a matching response.

    Captures the latest stream ID before publishing to avoid scanning history.
    Returns the full AlfredResponse so callers can forward actions_taken and mood.
    """
    last_id = f"{int(time.time() * 1000)}-0"

    await redis.xadd(USER_REQUESTS_STREAM, {"event": request.model_dump_json()})

    start = time.monotonic()
    while (time.monotonic() - start) < timeout:
        entries = await redis.xread({USER_RESPONSES_STREAM: last_id}, count=10, block=1000)
        for _stream, stream_entries in entries:
            for entry_id, entry_data in stream_entries:
                last_id = entry_id
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
