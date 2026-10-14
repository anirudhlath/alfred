"""What a failed System 2 turn means, and where the ones we give up on go.

A turn's exception is either worth another delivery (TRANSIENT: the provider was busy,
the connection dropped) or not (PERMANENT: the request itself is wrong, and will be
wrong every time). Unknown exceptions are TRANSIENT — the delivery cap bounds them.
See "6b. Retries" in docs/superpowers/specs/2026-10-08-pydantic-ai-adoption-design.md.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final

from loguru import logger
from pydantic_ai.exceptions import ModelHTTPError, UserError

from shared.streams import DEAD_LETTER_STREAM

if TYPE_CHECKING:
    from shared.types import AioRedis

__all__ = [
    "FAILED_REPLY",
    "MAX_DELIVERIES",
    "FailureKind",
    "classify",
    "dead_letter",
]

FAILED_REPLY: Final = (
    "I'm afraid I couldn't finish that, sir — my reasoning service isn't answering. "
    "Please try again in a moment."
)

# The delivery that fails for the third time is answered and dead-lettered.
MAX_DELIVERIES: Final = 3

# 4xx statuses a second try can still succeed on: request timeout, conflict, rate limit.
_RETRYABLE_CLIENT_STATUSES: Final = frozenset({408, 409, 429})

# Approximate cap on DEAD_LETTER_STREAM (XADD MAXLEN ~).
_DEAD_LETTER_MAXLEN: Final = 1000


class FailureKind(StrEnum):
    PERMANENT = "permanent"
    TRANSIENT = "transient"


def classify(exc: BaseException) -> FailureKind:
    """PERMANENT only for a usage mistake or a non-retryable 4xx; everything else waits."""
    if isinstance(exc, ModelHTTPError):
        status = exc.status_code
        if 400 <= status < 500 and status not in _RETRYABLE_CLIENT_STATUSES:
            return FailureKind.PERMANENT
        return FailureKind.TRANSIENT
    if isinstance(exc, UserError):
        return FailureKind.PERMANENT
    return FailureKind.TRANSIENT


async def dead_letter(
    redis: AioRedis,
    *,
    entry_id: str,
    event: str,
    exc: BaseException | None,
    deliveries: int,
    reason: str,
) -> None:
    """Record a turn we gave up on, and log it at ERROR.

    Called after the entry is answered and ACKed, so a failed write is logged, never
    raised: raising would only reach the consumer loop, with nothing left to retry.
    """
    error_type = type(exc).__name__ if exc is not None else ""
    error = str(exc) if exc is not None else ""
    logger.error(
        "Dead-lettered turn {} after {} deliveries ({}): {} {}",
        entry_id,
        deliveries,
        reason,
        error_type,
        error,
    )
    try:
        await redis.xadd(
            DEAD_LETTER_STREAM,
            {
                "entry_id": entry_id,
                "event": event,
                "error_type": error_type,
                "error": error,
                "deliveries": str(deliveries),
                "reason": reason,
            },
            maxlen=_DEAD_LETTER_MAXLEN,
            approximate=True,
        )
    except Exception as write_exc:
        logger.error("Could not write dead letter for {}: {}", entry_id, write_exc)
