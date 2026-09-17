"""Pending critical-action storage + confirmation republish.

Critical actions intercepted by the DomainRouter are parked here (TTL 5 min).
Confirmation — from the web endpoint or the Conscious `confirm_pending_action`
tool — republishes the request to ``alfred:actions`` with ``confirmed=True``;
the dispatch path executes marked requests without re-interception. Expiry is
silent (Redis TTL) — a confirm after expiry simply finds nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

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


async def get_pending_action(redis: AioRedis, request_id: str) -> tuple[ActionRequest, int] | None:
    """Read a pending action and its remaining TTL without consuming it.

    Returns None only when the GET misses — the entry is absent or already
    expired. A key that expires between the GET and the TTL still yields the
    action, with its TTL of -2 clamped to 0; so does the never-expected -1
    (a pending key with no expiry set).
    """
    raw: bytes | str | None = await redis.get(pending_key(request_id))
    if raw is None:
        return None
    action = ActionRequest.model_validate_json(decode_stream_value(raw))
    ttl = await redis.ttl(pending_key(request_id))
    return action, max(int(ttl), 0)


def _sort_timestamp(pair: tuple[ActionRequest, int]) -> datetime:
    """Sort key for a pending entry, tolerating a naive stored timestamp.

    ``BaseEvent.timestamp`` is a bare ``datetime``, so a hand-written or
    legacy entry can carry a naive one — comparing it against an aware one
    raises. Treat naive timestamps as UTC rather than failing the whole list.
    """
    ts = pair[0].timestamp
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


async def list_pending_actions(redis: AioRedis) -> list[tuple[ActionRequest, int]]:
    """Every pending action with its remaining TTL, oldest request first.

    One unreadable entry must not take the whole list down, so a value that
    fails to parse is skipped with a warning rather than raised.
    """
    found: list[tuple[ActionRequest, int]] = []
    # The GET+TTL per key is an N+1, deliberately: the pending set is a handful
    # of unconfirmed critical actions at most, each with a 5-minute TTL.
    async for key in redis.scan_iter(match=f"{PENDING_ACTIONS_PREFIX}*", count=100):
        request_id = decode_stream_value(key).removeprefix(PENDING_ACTIONS_PREFIX)
        try:
            item = await get_pending_action(redis, request_id)
        except ValueError:  # malformed JSON or a payload that no longer validates
            logger.warning("Skipping unreadable pending action {}", request_id)
            continue
        if item is not None:  # may have expired mid-scan
            found.append(item)
    found.sort(key=_sort_timestamp)
    return found


def pending_action_payload(action: ActionRequest, ttl_seconds: int) -> dict[str, Any]:
    """JSON shape the web clients render for one pending action."""
    return {
        "request_id": action.request_id,
        "tool_name": action.tool_name,
        "target_service": action.target_service,
        "parameters": action.parameters,
        "reason": action.reason,
        "source": action.source,
        "timestamp": action.timestamp.isoformat(),
        "ttl_seconds": ttl_seconds,
        "expires_at": (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat(),
    }
