"""Per-entry processing for the Conscious Engine's request loop.

The live path and the redelivery path run the *same* code. They once did not:
recovery called XAUTOCLAIM and only logged the count, leaving what it claimed
unprocessed and un-ACKed, to be reclaimed again a minute later forever.

Every request is answered exactly once (spec 6, 6b): a transient failure is left
pending for ``reclaim_due`` to redeliver; a permanent one, the last allowed delivery,
or a redelivery past the replay window is answered with ``FAILED_REPLY``, ACKed and
dead-lettered. The answered marker, set with the published response, makes a crash
between publishing and ACKing harmless.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from loguru import logger

from bus.schemas.events import AlfredResponse, UserRequest
from core.conscious.failures import (
    FAILED_REPLY,
    MAX_DELIVERIES,
    FailureKind,
    classify,
    dead_letter,
)
from shared.redis_streams import is_replayable
from shared.streams import ANSWERED_KEY, USER_RESPONSES_STREAM, decode_stream_value

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from shared.types import AioRedis

__all__ = ["process_request_entry"]

# Outlives the replay window many times over: no delivery can arrive after it lapses.
ANSWERED_TTL_S: Final = 3600


async def process_request_entry(
    entry_id: bytes | str,
    entry_data: dict[bytes | str, bytes | str],
    *,
    engine: Any,
    redis: AioRedis,
    stream: str,
    group: str,
    delivery: int = 1,
    now_ms: int | None = None,
    after_answer: Callable[[], Awaitable[object]] | None = None,
) -> None:
    """Process one UserRequest entry; *delivery* counts this delivery (1 for a live read).

    ACKs every entry that is answered or can never be answered. Only a transient
    failure before the last delivery stays pending.
    """
    raw = entry_data.get("event") or entry_data.get(b"event")
    if raw is None:
        logger.warning("Entry {} has no 'event' field — dropping", entry_id)
        await redis.xack(stream, group, entry_id)
        return
    event = decode_stream_value(raw)

    try:
        request = UserRequest.model_validate_json(event)
    except Exception as exc:
        logger.warning("Entry {} is not a valid UserRequest ({}) — dropping", entry_id, exc)
        await redis.xack(stream, group, entry_id)
        return

    turn = _Turn(redis, engine, request, entry_id, event, stream, group, delivery)
    if await redis.exists(turn.answered_key):
        logger.info("Request {} was already answered — ACKing entry {}", request.event_id, entry_id)
        await redis.xack(stream, group, entry_id)
        return

    now = now_ms if now_ms is not None else int(time.time() * 1000)
    if delivery > 1 and not is_replayable(entry_id, now_ms=now):
        await turn.give_up(None, "expired")
        return

    try:
        response = await engine.process_request(request)
    except Exception as exc:
        if classify(exc) is FailureKind.PERMANENT:
            await turn.give_up(exc, "permanent")
        elif delivery >= MAX_DELIVERIES:
            await turn.give_up(exc, "exhausted")
        else:
            # No ACK: reclaim_due redelivers it once its backoff has elapsed.
            logger.warning(
                "Request {} failed on delivery {}/{} ({}: {}) — will retry",
                entry_id,
                delivery,
                MAX_DELIVERIES,
                type(exc).__name__,
                exc,
            )
        return

    if await turn.answer(response) and after_answer is not None:
        try:
            await after_answer()
        except Exception as exc:
            logger.error("after_answer failed for request {}: {}", entry_id, exc)


@dataclass(frozen=True)
class _Turn:
    """One delivery of one request: everything answering it needs."""

    redis: AioRedis
    engine: Any
    request: UserRequest
    entry_id: bytes | str
    event: str
    stream: str
    group: str
    delivery: int

    @property
    def answered_key(self) -> str:
        return ANSWERED_KEY.format(self.request.event_id)

    async def answer(self, response: AlfredResponse) -> bool:
        """Publish *response* and set the answered marker in one transaction, then ACK.

        A failure is logged, not raised: the entry stays pending, and its redelivery
        either finds the marker (published, so it only ACKs) or runs again (nothing was
        published).
        """
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.xadd(USER_RESPONSES_STREAM, {"event": response.model_dump_json()})
                pipe.set(self.answered_key, "1", ex=ANSWERED_TTL_S)
                await pipe.execute()
            await self.redis.xack(self.stream, self.group, self.entry_id)
        except Exception as exc:
            logger.error("Could not publish or ACK the answer to {}: {}", self.entry_id, exc)
            return False
        return True

    async def give_up(
        self, exc: BaseException | None, reason: str, text: str = FAILED_REPLY
    ) -> None:
        """Answer *text* once, ACK, and dead-letter the entry."""
        if await self.answer(self.engine.failure_reply(self.request, text)):
            await dead_letter(
                self.redis,
                entry_id=decode_stream_value(self.entry_id),
                event=self.event,
                exc=exc,
                deliveries=self.delivery,
                reason=reason,
            )
