"""Parking a System 2 run that deferred its critical calls, and resuming it (#317 PR 5).

A run that ends in ``DeferredToolRequests`` is held as one ``ParkedRun`` under one approval:
approving or denying applies to every call it deferred. The approval id is derived from the
request's key, and the record is written with ``SET NX``, so a redelivered turn cannot park
twice: it finds the record the owner is already looking at and answers from that. The owner
is told at least once: the request's ledger, written last, says it was done. These are the
primitives; the engine decides when to use them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, Literal, Protocol
from uuid import NAMESPACE_URL, uuid5

from loguru import logger
from pydantic_ai import DeferredToolResults, ToolApproved, ToolDenied

from bus.schemas.events import REASON_MAX_LEN, ParkedCall, ParkedRun
from core.conscious.effects import EffectLedger, humanise
from core.notifications.schema import Urgency
from core.routing.pending import (
    CONFIRMATION_TITLE,
    confirmation_body,
    confirmation_metadata,
    park_run,
    read_pending,
)

if TYPE_CHECKING:
    from pydantic_ai import DeferredToolRequests

    from shared.types import AioRedis

__all__ = [
    "APPROVAL_NAMESPACE",
    "Decision",
    "Notifier",
    "approval_id_for",
    "claimable_run",
    "confirmation_reply",
    "park",
    "parked_calls",
    "results_for",
    "summary_of",
]

APPROVAL_NAMESPACE: Final = uuid5(NAMESPACE_URL, "alfred:approvals")
# The owner's answer to a parked run; "other" is a reply that is neither yes nor no.
Decision = Literal["approve", "deny", "other"]

_DENIED: Final = "The user declined."
_NOT_CONFIRMED: Final = "Not confirmed; the user moved on."
_SOURCE: Final = "conscious-engine"
_OWNER: Final = "sir"


class Notifier(Protocol):
    """What ``park`` needs of the notification publisher (``NotificationPublisher``)."""

    async def publish(
        self,
        *,
        title: str,
        body: str,
        source: str,
        urgency: Urgency,
        metadata: dict[str, Any] | None = None,
    ) -> object: ...


def approval_id_for(key: str) -> str:
    """The approval id for a request's *key*: the same key always gives the same id."""
    return str(uuid5(APPROVAL_NAMESPACE, key))


def parked_calls(requests: DeferredToolRequests) -> list[ParkedCall]:
    """One ``ParkedCall`` per call awaiting approval, from the metadata the gate attached."""
    calls: list[ParkedCall] = []
    for part in requests.approvals:
        meta = requests.metadata.get(part.tool_call_id, {})
        reason = meta.get("reason")
        calls.append(
            ParkedCall(
                tool_call_id=part.tool_call_id,
                tool_name=meta["tool_name"],
                target_service=meta["target_service"],
                parameters=meta["parameters"],
                reason=None if reason is None else str(reason)[:REASON_MAX_LEN],
            )
        )
    return calls


def summary_of(calls: list[ParkedCall]) -> str:
    """What the owner is asked to confirm: each call's reason, or its tool's name."""
    return "; ".join(
        call.reason.rstrip(". ") if call.reason else humanise(call.tool_name) for call in calls
    )


def confirmation_reply(summary: str) -> str:
    """The turn's reply while the run waits: a fixed template, no model call."""
    return f"Before I do that, I need your confirmation: {summary}. Shall I, sir?"


def results_for(run: ParkedRun, decision: Decision) -> DeferredToolResults:
    """The verdict on every call the parked *run* deferred."""
    verdict: ToolApproved | ToolDenied
    match decision:
        case "approve":
            verdict = ToolApproved()
        case "deny":
            verdict = ToolDenied(_DENIED)
        case "other":
            verdict = ToolDenied(_NOT_CONFIRMED)
    return DeferredToolResults(approvals={call.tool_call_id: verdict for call in run.calls})


async def park(
    redis: AioRedis, notifier: Notifier, run: ParkedRun, *, ledger_key: str
) -> ParkedRun:
    """Hold *run* for the owner's decision; returns the run that is actually pending.

    Writes the record (``SET NX``), tells the owner unless the request's ledger already
    holds a parked call, then marks each call ``parked`` in that ledger, so a redelivery
    answers from the ledger instead of running the turn again. The ledger is written last,
    so a push that failed (the error propagates, and the redelivery tries again) or a
    process that died before the ledger is repeated rather than lost: at-least-once, with
    the same ``pending_action_id``, so a duplicate push is the same approval twice.

    When the record already exists it is the one the owner will approve, and a redelivered
    turn's model may have deferred other calls than it holds; so the push and the ledger
    describe the stored run, which is what comes back. Only a record that is gone or is not
    a parked run (claimed or expired in between) leaves *run* itself to be described.
    """
    ledger = EffectLedger(redis)
    recorded = sum(1 for effect in await ledger.read(ledger_key) if effect.status == "parked")
    if not await park_run(redis, run):
        stored = await read_pending(redis, run.approval_id)
        if isinstance(stored, ParkedRun):
            run = stored
    if not recorded:
        first = run.calls[0]
        await notifier.publish(
            title=CONFIRMATION_TITLE,
            body=confirmation_body(first.tool_name, first.target_service),
            source=_SOURCE,
            urgency=Urgency.URGENT,
            metadata=confirmation_metadata(
                run.approval_id, first.tool_name, first.parameters, run.summary
            ),
        )
    for call in run.calls[recorded:]:
        await ledger.parked_call(ledger_key, call.tool_name, call.parameters)
    return run


async def claimable_run(redis: AioRedis, approval_id: str) -> ParkedRun | None:
    """The parked run behind *approval_id*, if it is the owner's to decide; unclaimed.

    A run parked for anyone else is never resumed by a reply in the conversation. Nor is a
    record that no longer reads (a format change with a record in flight): the turn goes on
    as a normal one rather than failing on every delivery until the record expires.
    """
    try:
        record = await read_pending(redis, approval_id)
    except ValueError:  # malformed JSON or a payload that no longer validates
        logger.warning("Pending record {} is unreadable — not claimable", approval_id)
        return None
    if isinstance(record, ParkedRun) and record.identity.get("identity") == _OWNER:
        return record
    return None
