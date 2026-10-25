"""The attribution logs: what Alfred asked a service to do, and who caused each change.

Three private records, each kept ``ATTRIBUTION_MAX_AGE``:

- ``alfred:attribution:calls``: every successful call Alfred made, with the context id the
  service reported for it, so a later change can be tied back to the call that caused it.
- ``alfred:attribution:changes``: every state change with a nameable cause (a person, Alfred
  or an automation), with its context. A change from a switch, an app or a device is only
  counted, not kept: there is nobody to attribute it to.
- ``alfred:attribution:causes:<UTC date>``: a hash counting every change by cause, plus
  ``unknown`` for an event that carried no context (an older home-service).

Writes are best-effort: a refused write logs one warning and never raises into routing or
Reflex, and never changes what they decide.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import BaseModel

from bus.schemas.events import ChangeContext  # noqa: TC001  # pydantic field type
from core.attribution.cause import ATTRIBUTED, Cause, cause_of
from shared.redis_streams import REPLAY_MARKER_TTL_SECONDS, append_capped
from shared.streams import (
    ATTRIBUTION_CALLS_MAXLEN,
    ATTRIBUTION_CALLS_STREAM,
    ATTRIBUTION_CAUSES_PREFIX,
    ATTRIBUTION_CHANGES_MAXLEN,
    ATTRIBUTION_CHANGES_STREAM,
    ATTRIBUTION_COUNTED_PREFIX,
    ATTRIBUTION_MAX_AGE,
    decode_stream_value,
)

if TYPE_CHECKING:
    from bus.schemas.events import ActionRequest, ActionResult, StateChangedEvent
    from shared.types import AioRedis

_UNKNOWN = "unknown"
_COUNT_TTL_SECONDS = int(ATTRIBUTION_MAX_AGE.total_seconds())


class AlfredCall(BaseModel):
    """One successful call Alfred made to a service."""

    time: datetime
    source: str  # ActionRequest.source: reflex-engine, conscious-engine, trigger-engine, admin-api
    request_id: str
    target_service: str
    tool_name: str
    parameters: dict[str, Any]
    reason: str | None
    context_id: str | None  # None when the service returned none (an older home-service)


class AttributedChange(BaseModel):
    """One state change whose cause is a person, Alfred or an automation."""

    time: datetime  # the event's timestamp
    entity_id: str
    old_state: str | None
    new_state: str
    cause: Cause
    context: ChangeContext


def causes_key(day: date) -> str:
    """The hash counting *day*'s (UTC) changes by cause."""
    return f"{ATTRIBUTION_CAUSES_PREFIX}{day.isoformat()}"


def call_context_id(result: ActionResult) -> str | None:
    """The context id the service reported for a call, or ``None`` when it sent none.

    HomeAgent stores home-service's whole MCP response in ``result.result``, so the tool's
    own return sits one level down, under ``"result"``.
    """
    outer = result.result
    inner = outer.get("result") if outer else None
    context_id = inner.get("context_id") if isinstance(inner, dict) else None
    return context_id if isinstance(context_id, str) and context_id else None


async def record_call(
    redis: AioRedis,
    action: ActionRequest,
    result: ActionResult,
    *,
    now: datetime | None = None,
) -> None:
    """Log *action* as one of Alfred's calls when it succeeded. Best-effort: never raises."""
    if result.status != "success":
        return
    try:
        call = AlfredCall(
            time=now or datetime.now(UTC),
            source=action.source,
            request_id=action.request_id,
            target_service=action.target_service,
            tool_name=action.tool_name,
            parameters=action.parameters,
            reason=action.reason,
            context_id=call_context_id(result),
        )
        await append_capped(
            redis,
            ATTRIBUTION_CALLS_STREAM,
            {"call": call.model_dump_json()},
            maxlen=ATTRIBUTION_CALLS_MAXLEN,
            max_age=ATTRIBUTION_MAX_AGE,
            now=call.time,
        )
    except Exception as e:
        logger.warning("Attribution call log failed ({}): {}", action.tool_name, e)


async def record_change(
    redis: AioRedis,
    event: StateChangedEvent,
    *,
    entry_id: bytes | str | None = None,
    now: datetime | None = None,
) -> Cause | None:
    """Count *event* by its cause and, when it names an actor, log it. Returns the cause.

    Given the stream *entry_id* it was read from, an entry is counted once however often it is
    delivered: a replay finds the first delivery's marker and writes nothing.

    The cause comes from the event alone, so it is returned even when a write is refused:
    best-effort bookkeeping never changes what the caller decides.
    """
    when = (now or datetime.now(UTC)).astimezone(UTC)
    cause = cause_of(event.context)
    try:
        if entry_id is not None:
            marker = f"{ATTRIBUTION_COUNTED_PREFIX}{decode_stream_value(entry_id)}"
            if not await redis.set(marker, 1, nx=True, ex=REPLAY_MARKER_TTL_SECONDS):
                return cause
    except Exception as e:
        logger.warning("Attribution change log failed ({}): {}", event.entity_id, e)
        return cause
    # The count and the log are separate writes: one refused does not drop the other.
    try:
        key = causes_key(when.date())
        await redis.hincrby(key, cause or _UNKNOWN, 1)
        await redis.expire(key, _COUNT_TTL_SECONDS)
    except Exception as e:
        logger.warning("Attribution change count failed ({}): {}", event.entity_id, e)
    if cause in ATTRIBUTED and event.context is not None:
        try:
            change = AttributedChange(
                time=event.timestamp,
                entity_id=event.entity_id,
                old_state=event.old_state,
                new_state=event.new_state,
                cause=cause,
                context=event.context,
            )
            await append_capped(
                redis,
                ATTRIBUTION_CHANGES_STREAM,
                {"change": change.model_dump_json()},
                maxlen=ATTRIBUTION_CHANGES_MAXLEN,
                max_age=ATTRIBUTION_MAX_AGE,
                now=when,
            )
        except Exception as e:
            logger.warning("Attribution change log failed ({}): {}", event.entity_id, e)
    return cause
