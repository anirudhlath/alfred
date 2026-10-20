"""Pending approvals: the one store for everything waiting on the owner's yes or no.

Two kinds of record live under ``PENDING_PREFIX`` (TTL 5 min), told apart by their
``kind``: a ``ParkedRun`` (a System 2 run that deferred its critical calls) and a
``ParkedAction`` (a critical action the DomainRouter intercepted from another source).
Every resolution path — the web endpoints, a reply in the conversation — claims the
record with ``GETDEL``, so a Door tap and a "yes" in chat can never both act on it.
Expiry is silent (Redis TTL): a decision after expiry simply finds nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal

from loguru import logger

from bus.schemas.events import (
    PENDING_RECORD,
    ActionRequest,
    ApprovalDecision,
    ParkedAction,
    ParkedRun,
    PendingRecord,
)
from shared.streams import (
    ACTIONS_STREAM,
    APPROVALS_STREAM,
    PENDING_PREFIX,
    decode_stream_value,
)

if TYPE_CHECKING:
    from shared.types import AioRedis

PENDING_TTL_SECONDS: Final = 300
CONFIRMATION_TITLE: Final = "Confirmation required"


def pending_key(record_id: str) -> str:
    """Redis key holding one pending record's JSON."""
    return f"{PENDING_PREFIX}{record_id}"


def confirmation_body(tool_name: str, target_service: str) -> str:
    """The notification body that asks the owner to confirm a critical call."""
    return f"Alfred wants to run '{tool_name}' on {target_service} — confirm?"


def confirmation_metadata(
    record_id: str, tool_name: str, parameters: dict[str, Any], reason: str | None
) -> dict[str, Any]:
    """The notification metadata clients use to render Confirm and Deny."""
    return {
        "pending_action_id": record_id,
        "tool_name": tool_name,
        "parameters": parameters,
        "reason": reason,
    }


def _parse(raw: bytes | str) -> PendingRecord:
    return PENDING_RECORD.validate_json(decode_stream_value(raw))


async def store_pending_action(redis: AioRedis, action: ActionRequest) -> None:
    """Park an unconfirmed critical action with a 5-minute TTL."""
    await redis.set(
        pending_key(action.request_id),
        ParkedAction(action=action).model_dump_json(),
        ex=PENDING_TTL_SECONDS,
    )


async def park_run(redis: AioRedis, run: ParkedRun) -> bool:
    """Park a run awaiting approval. True only when this call wrote the record.

    ``SET NX``: a redelivered turn that parks the same approval again must not
    replace (or extend) the record the owner is already looking at.
    """
    written = await redis.set(
        pending_key(run.approval_id),
        run.model_dump_json(),
        ex=PENDING_TTL_SECONDS,
        nx=True,
    )
    return bool(written)


async def read_pending(redis: AioRedis, record_id: str) -> PendingRecord | None:
    """The pending record without consuming it, or None when absent or expired."""
    raw: bytes | str | None = await redis.get(pending_key(record_id))
    return None if raw is None else _parse(raw)


async def claim_pending(redis: AioRedis, record_id: str) -> PendingRecord | None:
    """Atomically take the pending record (``GETDEL``).

    Concurrent claimers can never both see it, so only one caller resumes a run or
    republishes an action — a critical action (a door unlock) executes at most once.
    """
    raw: bytes | str | None = await redis.getdel(pending_key(record_id))
    return None if raw is None else _parse(raw)


async def get_pending(redis: AioRedis, record_id: str) -> tuple[PendingRecord, int] | None:
    """A pending record and its remaining TTL, without consuming it.

    None only when the GET misses. A key that expires between the GET and the TTL
    still yields the record, with its TTL of -2 clamped to 0; so does the
    never-expected -1 (a pending key with no expiry set).
    """
    record = await read_pending(redis, record_id)
    if record is None:
        return None
    ttl = await redis.ttl(pending_key(record_id))
    return record, max(int(ttl), 0)


def _created_at(record: PendingRecord) -> datetime:
    """Sort key for a pending record, tolerating a naive stored timestamp.

    ``BaseEvent.timestamp`` is a bare ``datetime``, so a hand-written or legacy
    record can carry a naive one — comparing it against an aware one raises. Treat
    naive timestamps as UTC rather than failing the whole list.
    """
    ts = record.created_at if isinstance(record, ParkedRun) else record.action.timestamp
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


async def list_pending(redis: AioRedis) -> list[tuple[PendingRecord, int]]:
    """Every pending record with its remaining TTL, oldest first.

    One unreadable record must not take the whole list down, so a value that fails
    to parse is skipped with a warning rather than raised.
    """
    found: list[tuple[PendingRecord, int]] = []
    # The GET+TTL per key is an N+1, deliberately: the pending set is a handful of
    # records at most, each with a 5-minute TTL.
    async for key in redis.scan_iter(match=f"{PENDING_PREFIX}*", count=100):
        record_id = decode_stream_value(key).removeprefix(PENDING_PREFIX)
        try:
            item = await get_pending(redis, record_id)
        except ValueError:  # malformed JSON or a payload that no longer validates
            logger.warning("Skipping unreadable pending record {}", record_id)
            continue
        if item is not None:  # may have expired mid-scan
            found.append(item)
    found.sort(key=lambda pair: _created_at(pair[0]))
    return found


def pending_payload(record: PendingRecord, ttl_seconds: int) -> dict[str, Any]:
    """JSON shape the web clients render for one pending record.

    A parked run shows its first call, with the run's summary as the reason.
    """
    first = record.action_requests()[0]
    return {
        "request_id": record.record_id,
        "tool_name": first.tool_name,
        "target_service": first.target_service,
        "parameters": first.parameters,
        "reason": record.summary if isinstance(record, ParkedRun) else first.reason,
        "source": first.source,
        "timestamp": first.timestamp.isoformat(),
        "ttl_seconds": ttl_seconds,
        "expires_at": (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat(),
    }


async def decide(redis: AioRedis, record_id: str, decision: Literal["approve", "deny"]) -> bool:
    """Resolve a pending record: claim it, then act on the decision.

    A parked run is handed, with the decision, to the conscious process through
    ``APPROVALS_STREAM``. A router-held action is republished confirmed on approval
    and dropped on denial. False when there was nothing to claim — absent, expired,
    already decided, or unreadable (never acted on).
    """
    try:
        record = await claim_pending(redis, record_id)
    except ValueError:  # claimed, but no longer parses: it is gone and acts on nothing
        logger.warning("Pending record {} is unreadable — discarded", record_id)
        return False
    if record is None:
        return False
    if isinstance(record, ParkedRun):
        event = ApprovalDecision(
            source="channels",
            approval_id=record.approval_id,
            decision=decision,
            run=record,
        )
        await redis.xadd(APPROVALS_STREAM, {"event": event.model_dump_json()})
        logger.info("Parked run {} decided: {}", record_id, decision)
    elif decision == "approve":
        confirmed = record.action.model_copy(update={"confirmed": True})
        await redis.xadd(ACTIONS_STREAM, {"event": confirmed.model_dump_json()})
        logger.info(
            "Pending action {} confirmed → republished '{}'", record_id, confirmed.tool_name
        )
    else:
        logger.info("Pending action {} denied → dropped '{}'", record_id, record.action.tool_name)
    return True
