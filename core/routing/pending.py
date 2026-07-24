"""Pending critical-action storage + confirmation republish.

Critical actions intercepted by the DomainRouter are parked here (TTL 5 min).
Confirmation — from the web endpoint or the Conscious `confirm_pending_action`
tool — republishes the request to ``alfred:actions`` with ``confirmed=True``;
the dispatch path executes marked requests without re-interception. Expiry is
silent (Redis TTL) — a confirm after expiry simply finds nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from bus.schemas.events import ActionRequest
from shared.streams import ACTIONS_STREAM, PENDING_ACTIONS_PREFIX, decode_stream_value

if TYPE_CHECKING:
    from shared.types import AioRedis

PENDING_TTL_SECONDS = 300


def pending_key(request_id: str) -> str:
    """Redis key holding a pending ActionRequest's JSON."""
    return f"{PENDING_ACTIONS_PREFIX}{request_id}"


async def store_pending_action(redis: AioRedis, action: ActionRequest) -> None:
    """Park an unconfirmed critical action with a 5-minute TTL."""
    await redis.set(
        pending_key(action.request_id),
        action.model_dump_json(),
        ex=PENDING_TTL_SECONDS,
    )


async def confirm_pending_action(redis: AioRedis, request_id: str) -> ActionRequest | None:
    """Confirm a pending action: atomically pop it and republish with the marker set.

    Uses GETDEL so concurrent confirms of the same id can never both see the
    value — only one caller gets the ActionRequest back and republishes;
    every other concurrent (or later) confirm gets None. This prevents a
    critical action (e.g. a door unlock) from executing twice.

    Returns the confirmed ActionRequest, or None when the pending entry is
    missing, expired, or was already consumed by a concurrent confirm.
    """
    raw: bytes | str | None = await redis.getdel(pending_key(request_id))
    if raw is None:
        return None
    action = ActionRequest.model_validate_json(decode_stream_value(raw))
    confirmed = action.model_copy(update={"confirmed": True})
    await redis.xadd(ACTIONS_STREAM, {"event": confirmed.model_dump_json()})
    logger.info("Pending action {} confirmed → republished '{}'", request_id, confirmed.tool_name)
    return confirmed
