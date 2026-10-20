"""Follow-up delivery — replies that arrive after a channel stopped waiting (#307).

``publish_and_wait`` gives up after its timeout and answers with ``LATE_REPLY``, leaving its
wait registered as late under ``FOLLOWUP_KEY``. The conscious engine also answers runs the
owner approved or declined from the Door long after the request itself was answered
(``followup=True``). Nobody waits for either, so this worker reads every response once and
hands those two kinds to a channel-specific sink; everything else was already delivered by
the channel that waited for it (or, for Signal, by the bridge).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final, Protocol

from loguru import logger
from pydantic import ValidationError

from bus.schemas.events import AlfredResponse
from core.channels.request_bus import FOLLOWUP_LATE
from core.reflex.runner import ensure_consumer_group
from shared.redis_streams import read_group
from shared.streams import FOLLOWUP_KEY, USER_RESPONSES_STREAM, decode_stream_value

if TYPE_CHECKING:
    from shared.types import AioRedis

FOLLOWUP_GROUP: Final = "channels-followups"


class FollowupSink(Protocol):
    """Delivers one follow-up to the owner on the channel its request came from."""

    async def __call__(self, response: AlfredResponse) -> None: ...


async def followup_worker(
    redis: AioRedis,
    *,
    deliver: FollowupSink,
    consumer: str,
    shutdown: asyncio.Event,
) -> None:
    """Consume ``USER_RESPONSES_STREAM`` and deliver the follow-ups among the responses.

    The group starts at the stream's end: a follow-up is worth delivering now, never on the
    replay of a stream's history. Same worker pattern as
    core/notifications/delivery.py::notification_delivery_worker.
    """
    await ensure_consumer_group(redis, USER_RESPONSES_STREAM, FOLLOWUP_GROUP, start_id="$")

    while not shutdown.is_set():
        try:
            entries = await read_group(
                redis,
                FOLLOWUP_GROUP,
                consumer,
                {USER_RESPONSES_STREAM: ">"},
                count=10,
                block=5000,
            )
            for _stream_key, stream_entries in entries:
                for entry_id, entry_data in stream_entries:
                    try:
                        await _handle(redis, entry_data, deliver)
                    finally:
                        # Every entry is ACKed, delivered or not (plan ruling R8): delivery
                        # is best effort, so there is no PEL to reclaim — the deliberate
                        # exception to the reclaim rule in the root CLAUDE.md's Gotchas.
                        await redis.xack(USER_RESPONSES_STREAM, FOLLOWUP_GROUP, entry_id)
        except Exception as exc:
            if not shutdown.is_set():
                logger.error("Follow-up worker error: {}", exc)
                await asyncio.sleep(1)


async def _handle(
    redis: AioRedis, entry_data: dict[bytes | str, bytes | str], deliver: FollowupSink
) -> None:
    """Deliver one response entry if it is a follow-up this worker owns; a failed delivery is
    logged at WARNING and dropped."""
    raw = entry_data.get(b"event") or entry_data.get("event")
    if raw is None:
        return
    try:
        response = AlfredResponse.model_validate_json(decode_stream_value(raw))
    except (UnicodeDecodeError, ValidationError) as exc:
        logger.warning("Skipping an unreadable response entry: {}", exc)
        return
    if not await _claim(redis, response):
        return
    try:
        await deliver(response)
    except Exception as exc:
        logger.warning(
            "Could not deliver the follow-up to request {} (session {}): {}",
            response.in_reply_to,
            response.session_id,
            exc,
        )


async def _claim(redis: AioRedis, response: AlfredResponse) -> bool:
    """Whether this worker delivers *response*: always for a follow-up; for a late answer,
    only by winning the ``DEL`` of its late registration, which ``publish_and_wait``'s last
    read races for, so a late answer is shown exactly once. An answer a channel still waits
    for, or whose registration expired, is skipped."""
    if response.followup:
        return True
    if response.in_reply_to is None:
        return False
    key = FOLLOWUP_KEY.format(response.in_reply_to)
    state = await redis.get(key)
    if state is None or decode_stream_value(state) != FOLLOWUP_LATE:
        return False
    deleted: int = await redis.delete(key)
    return deleted == 1
