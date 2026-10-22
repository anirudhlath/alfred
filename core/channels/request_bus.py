"""Shared request/response bus helper for user-facing channels."""

from __future__ import annotations

import time
from typing import Final

from loguru import logger

from bus.schemas.events import AlfredResponse, UserRequest
from shared.redis_streams import read, revrange
from shared.streams import (
    FOLLOWUP_KEY,
    USER_REQUESTS_STREAM,
    USER_RESPONSES_STREAM,
    decode_stream_value,
)
from shared.types import AioRedis  # noqa: TC001

# How long a registered wait lives, and so how long a late answer is still delivered.
FOLLOWUP_TTL_S: Final = 600
LATE_REPLY: Final = "This is taking a little longer, sir — I'll reply as soon as it's ready."
# The registration's values: a channel still waiting, or one that gave up and got LATE_REPLY.
FOLLOWUP_WAITING: Final = "waiting"
FOLLOWUP_LATE: Final = "late"

_READ_COUNT = 10


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

    A response that names its request (``in_reply_to``) matches only that request, so a
    redelivered answer that lands after this channel stopped waiting for it is never
    returned as the next request's answer. One without it matches by session.

    The wait is registered under ``FOLLOWUP_KEY`` before the request goes out (#307). On
    a timeout the registration turns ``late`` and the caller gets ``LATE_REPLY``; the
    channels' follow-up worker (``core/channels/followups.py``) then delivers the answer
    when it lands. One last read covers an answer that landed as the wait ended: this
    caller and the worker each need their ``DEL`` of the key to win before they show a
    late answer, so it is shown once.
    """
    tail = await revrange(redis, USER_RESPONSES_STREAM, count=1)
    tail_id = tail[0][0] if tail else None
    last_id = decode_stream_value(tail_id) if tail_id is not None else "0-0"

    key = FOLLOWUP_KEY.format(request.event_id)
    await redis.set(key, FOLLOWUP_WAITING, ex=FOLLOWUP_TTL_S)
    await redis.xadd(USER_REQUESTS_STREAM, {"event": request.model_dump_json()})

    start = time.monotonic()
    while (time.monotonic() - start) < timeout:
        resp, last_id = await _read_answer(redis, request, session_id, last_id, block=1000)
        if resp is not None:
            await redis.delete(key)
            return resp

    logger.warning(
        "No response for session {} within {}s timeout — returning fallback", session_id, timeout
    )
    # XX: an expired registration stays gone. The TTL restarts: the key lives as long as a
    # late answer is still worth delivering.
    await redis.set(key, FOLLOWUP_LATE, ex=FOLLOWUP_TTL_S, xx=True)
    resp, _ = await _read_answer(redis, request, session_id, last_id, block=None)
    if resp is not None and await redis.delete(key) == 1:
        return resp
    return AlfredResponse(
        source="channels",
        channel=request.channel,
        session_id=session_id,
        text=LATE_REPLY,
        in_reply_to=request.event_id,
    )


async def _read_answer(
    redis: AioRedis,
    request: UserRequest,
    session_id: str,
    last_id: str,
    *,
    block: int | None,
) -> tuple[AlfredResponse | None, str]:
    """One read of the responses stream after *last_id*: the answer to *request* if it
    holds one, and the ID to read on from. ``block=None`` reads without blocking, and then
    reads everything after *last_id*."""
    count = _READ_COUNT if block is not None else None
    entries = await read(redis, {USER_RESPONSES_STREAM: last_id}, count=count, block=block)
    for _stream, stream_entries in entries:
        for entry_id, entry_data in stream_entries:
            last_id = decode_stream_value(entry_id)
            raw = entry_data.get(b"event") or entry_data.get("event")
            if raw:
                resp = AlfredResponse.model_validate_json(decode_stream_value(raw))
                if _answers(resp, request, session_id):
                    return resp, last_id
    return None, last_id


def _answers(resp: AlfredResponse, request: UserRequest, session_id: str) -> bool:
    """Whether *resp* answers *request*: by ``in_reply_to`` when the producer set it, so a
    late answer to an earlier request in the same session is skipped; else, for a producer
    that does not set it, by session as before. A follow-up answers nobody's wait: the
    follow-up worker delivers it."""
    if resp.followup:
        return False
    if resp.in_reply_to is not None:
        if resp.in_reply_to == request.event_id:
            return True
        if resp.session_id == session_id:
            logger.info(
                "Skipping a late answer to request {} while waiting on {} (session {})",
                resp.in_reply_to,
                request.event_id,
                session_id,
            )
        return False
    return resp.session_id == session_id
