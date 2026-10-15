"""Per-entry processing for the Conscious Engine's request loop.

The live path and the redelivery path run the *same* code. They once did not:
recovery called XAUTOCLAIM and only logged the count, leaving what it claimed
unprocessed and un-ACKed, to be reclaimed again a minute later forever.

Every request is answered exactly once (spec 6, 6b): a transient failure is left
pending for ``reclaim_due`` to redeliver, its backoff restarted from the failure; a
permanent one, the last allowed delivery, or an entry past the replay window is
answered with ``FAILED_REPLY``, ACKed and dead-lettered. An entry older than the
answered marker's TTL is ACKed and dead-lettered unanswered. The marker, set with the
published response, makes a crash between publishing and ACKing harmless.

A request that already acted (its side-effect ledger holds calls, ``core.conscious.effects``)
is never run again: a delivery that finds a ledger, or a turn that fails after acting, is
answered from the ledger (``ledger_reply``) and ACKed, so no tool call runs twice (#304).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from loguru import logger

from bus.schemas.events import AlfredResponse, UserRequest
from core.conscious.effects import Effect, EffectLedger, ledger_reply
from core.conscious.failures import (
    FAILED_REPLY,
    MAX_DELIVERIES,
    FailureKind,
    classify,
    dead_letter,
)
from shared.redis_streams import entry_ms, is_replayable
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
    consumer: str,
    delivery: int = 1,
    now_ms: int | None = None,
    after_answer: Callable[[], Awaitable[object]] | None = None,
) -> None:
    """Process one UserRequest entry; *delivery* counts this delivery (1 for a live read).

    ACKs every entry that is answered or can never be answered. Only a transient
    failure before the last delivery stays pending. A Redis error is logged and the
    entry left pending, never raised into the consumer loop.
    """
    raw = entry_data.get("event") or entry_data.get(b"event")
    if raw is None:
        logger.warning("Entry {} has no 'event' field — dropping", entry_id)
        await _ack(redis, stream, group, entry_id)
        return
    event = decode_stream_value(raw)

    try:
        request = UserRequest.model_validate_json(event)
    except Exception as exc:
        logger.warning("Entry {} is not a valid UserRequest ({}) — dropping", entry_id, exc)
        await _ack(redis, stream, group, entry_id)
        return

    turn = _Turn(redis, engine, request, entry_id, event, stream, group, delivery)
    try:
        answered = bool(await redis.exists(turn.answered_key))
    except Exception as exc:
        logger.error("Could not check whether {} was answered: {}", entry_id, exc)
        return
    if answered:
        logger.info("Request {} was already answered — ACKing entry {}", request.event_id, entry_id)
        # Its ACK may have failed after a good publish, before the hook ran.
        if await _ack(redis, stream, group, entry_id):
            await _run_after_answer(after_answer, entry_id)
        return

    now = now_ms if now_ms is not None else int(time.time() * 1000)
    if not is_replayable(entry_id, now_ms=now, max_age_ms=ANSWERED_TTL_S * 1000):
        # Its marker may have lapsed, and an hours-late reply is noise.
        await turn.drop("stale")
        return
    effects = await turn.effects()
    if effects is None:
        return
    if effects:
        # A delivery before this one acted and died before it answered (#304).
        logger.info("Request {} already acted — answering from its ledger", request.event_id)
        # Dead-lettered here too: the failed delivery's own record may never have landed.
        await turn.answer_from_ledger(effects, None, "partial")
        return
    if not is_replayable(entry_id, now_ms=now):
        await turn.give_up(None, "expired")
        return
    if delivery > MAX_DELIVERIES:
        # The last delivery's FAILED_REPLY never got out; never run the turn again.
        await turn.give_up(None, "exhausted")
        return

    try:
        # The window is checked again when the turn acts (RecordEffects), on the same clock.
        response = await engine.process_request(request, received_ms=entry_ms(entry_id))
    except Exception as exc:
        effects = await turn.effects()
        if effects is None:
            # It may have acted: never FAILED_REPLY it. Its redelivery reads the ledger.
            await turn.restart_backoff(consumer)
            return
        if effects:
            await turn.answer_from_ledger(effects, exc, "partial")
        elif classify(exc) is FailureKind.PERMANENT:
            await turn.give_up(exc, "permanent")
        elif delivery >= MAX_DELIVERIES:
            await turn.give_up(exc, "exhausted")
        else:
            logger.warning(
                "Request {} failed on delivery {}/{} ({}: {}) — will retry",
                entry_id,
                delivery,
                MAX_DELIVERIES,
                type(exc).__name__,
                exc,
            )
            # No ACK: reclaim_due redelivers it once its backoff has elapsed.
            await turn.restart_backoff(consumer)
        return

    if await turn.answer(response):
        await _run_after_answer(after_answer, entry_id)


async def _ack(redis: AioRedis, stream: str, group: str, entry_id: bytes | str) -> bool:
    try:
        await redis.xack(stream, group, entry_id)
    except Exception as exc:
        logger.error("Could not ACK entry {}: {}", entry_id, exc)
        return False
    return True


async def _run_after_answer(
    after_answer: Callable[[], Awaitable[object]] | None, entry_id: bytes | str
) -> None:
    if after_answer is None:
        return
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
        True once published, whether or not the ACK lands.

        A failure is logged, not raised: the entry stays pending, and its redelivery
        either finds the marker (published, so it only ACKs) or runs again (nothing was
        published).
        """
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.xadd(USER_RESPONSES_STREAM, {"event": response.model_dump_json()})
                pipe.set(self.answered_key, "1", ex=ANSWERED_TTL_S)
                await pipe.execute()
        except Exception as exc:
            logger.error("Could not publish the answer to {}: {}", self.entry_id, exc)
            return False
        await _ack(self.redis, self.stream, self.group, self.entry_id)
        return True

    async def effects(self) -> list[Effect] | None:
        """The request's side-effect ledger, or None (logged) when it cannot be read."""
        try:
            return await EffectLedger(self.redis).read(self.request.event_id)
        except Exception as exc:
            logger.error("Could not read the ledger of {}: {}", self.entry_id, exc)
            return None

    async def answer_from_ledger(
        self, effects: list[Effect], exc: BaseException | None, reason: str
    ) -> None:
        """Answer from *effects*, ACK, and dead-letter the entry. The turn reaches the
        session only once published, so a failed publish and its redelivery store it once."""
        text = ledger_reply(effects)
        if await self.answer(self.engine.failure_reply(self.request, text)):
            await self.engine.record_partial_turn(self.request, text)
            await self._dead_letter(exc, reason)

    async def restart_backoff(self, consumer: str) -> None:
        """Reset the entry's idle clock, so its backoff counts from this failure rather
        than from its delivery (a turn that ran 20 s would otherwise be due at once).
        JUSTID leaves ``times_delivered`` alone."""
        try:
            await self.redis.xclaim(
                self.stream,
                self.group,
                consumer,
                min_idle_time=0,
                message_ids=[self.entry_id],
                justid=True,
            )
        except Exception as exc:
            logger.warning("Could not restart the backoff of {}: {}", self.entry_id, exc)

    async def drop(self, reason: str) -> None:
        """ACK and dead-letter the entry without answering it."""
        if await _ack(self.redis, self.stream, self.group, self.entry_id):
            await self._dead_letter(None, reason)

    async def give_up(
        self, exc: BaseException | None, reason: str, text: str = FAILED_REPLY
    ) -> None:
        """Answer *text* once, ACK, and dead-letter the entry."""
        if await self.answer(self.engine.failure_reply(self.request, text)):
            await self._dead_letter(exc, reason)

    async def _dead_letter(self, exc: BaseException | None, reason: str) -> None:
        await dead_letter(
            self.redis,
            entry_id=decode_stream_value(self.entry_id),
            event=self.event,
            exc=exc,
            deliveries=self.delivery,
            reason=reason,
        )
