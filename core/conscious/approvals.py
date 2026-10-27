"""Parking a System 2 run that deferred its critical calls, and resuming it (#317 PR 5).

A run that ends in ``DeferredToolRequests`` is held as one ``ParkedRun`` under one approval:
approving or denying applies to every call it deferred. The approval id is derived from the
request's key, and the record is written with ``SET NX``, so a redelivered turn cannot park
twice: it finds the record the owner is already looking at and answers from that. The owner
is told at least once: the request's ledger, written last, says it was done. These are the
primitives; the engine decides when to use them.
"""

from __future__ import annotations

import json
import re
import unicodedata
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
    "PARAM_COUNT_MAX",
    "PARAM_KEY_MAX",
    "PARAM_VALUE_MAX",
    "REASON_CLAUSE_MAX",
    "Decision",
    "Notifier",
    "approval_id_for",
    "claimable_run",
    "confirmation_reply",
    "describe_call",
    "park",
    "parked_calls",
    "results_for",
    "summary_of",
]

APPROVAL_NAMESPACE: Final = uuid5(NAMESPACE_URL, "alfred:approvals")
# The owner's answer to a parked run; "other" is a reply that is neither yes nor no.
Decision = Literal["approve", "deny", "other"]

# What a description shows of a call, each cut at this many characters, ellipsis included:
# the model's reason as a quoted aside, and every parameter's name and value. A structured
# value (a dict or list) is the exception: it is shown whole, because what a call acts on
# can sit anywhere in it (``call_service``'s free ``data`` carries an ``entity_id``).
REASON_CLAUSE_MAX: Final = 120
PARAM_KEY_MAX: Final = 40
PARAM_VALUE_MAX: Final = 60
# At most this many parameters are shown; the rest are counted ("+2 more"). The gate lets a
# critical call carry only the parameters its tool declares, so this caps a long schema.
PARAM_COUNT_MAX: Final = 8
_WHITESPACE: Final = re.compile(r"\s+")
# Quote marks that could visually end a quoted text early; format characters (Unicode
# category Cf: bidi controls, zero-width characters) are dropped as well.
_QUOTES: Final = frozenset('"“”„‟«»')
# What the reason loses besides: parentheses, which would open a group of its own next to the
# parameters', and the look-alikes of a quote that no Unicode quote category covers (double
# prime, full-width quotation mark, Hebrew punctuation gershayim, modifier letter double
# apostrophe), plus every initial and final quotation mark (categories Pi and Pf).
_REASON_DROPPED: Final = frozenset("()\u2033\uff02\u05f4\u02ee")
_REASON_DROPPED_CATEGORIES: Final = frozenset({"Pi", "Pf"})
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


def _cut(line: str, cap: int) -> str:
    """*line* if it fits in *cap* characters, else cut so it ends with an ellipsis."""
    return line if len(line) <= cap else line[: cap - 1].rstrip() + "…"


def _clean(text: str, cap: int | None, *, reason: bool = False) -> str:
    """*text* as one plain line of at most *cap* characters (no limit when *cap* is None).

    Quote marks and format characters (bidi controls, zero-width characters) are removed,
    runs of whitespace, newlines included, become a single space, and a longer text is cut
    and ends with an ellipsis. What is shown cannot hide where it ends, or break the line.
    The model's *reason* loses more, since it sits inside quotes after the parameters:
    parentheses and every kind of quotation mark, so it cannot close its own quotes, or
    pass for a parameter group of its own.
    """

    def kept(ch: str) -> bool:
        category = unicodedata.category(ch)
        if ch in _QUOTES or category == "Cf":
            return False
        return not (reason and (ch in _REASON_DROPPED or category in _REASON_DROPPED_CATEGORIES))

    line = _WHITESPACE.sub(" ", "".join(filter(kept, text))).strip()
    return line if cap is None else _cut(line, cap)


def _key_shown(key: str) -> str:
    """A parameter's name: as it is when it is plain, else JSON-escaped so it stays visible.

    Cleaning a name would make a decoy (a zero-width character, a Cyrillic letter, a quote
    inside) read exactly like the real one beside it, so a name that is not plain printable
    ASCII, or that cleaning would change, shows its escapes (``tar\\u200bget``) instead.
    """
    if key.isascii() and key.isprintable() and _clean(key, None) == key:
        return _cut(key, PARAM_KEY_MAX)
    return _cut(json.dumps(key, ensure_ascii=True)[1:-1], PARAM_KEY_MAX)


def _value_shown(value: object) -> str:
    """A parameter's value as one line: a string in quotes, anything else as compact JSON.

    A string loses its own quote marks, so it cannot close its quotes and pass a ``)`` or a
    ``, key=`` off as structure (#397), and is cut at ``PARAM_VALUE_MAX``. A dict or list is
    not cut: it is ASCII JSON, whose quotes and escapes keep every nested string and name,
    look-alikes included, visible and in its place.
    """
    if isinstance(value, str):
        return f'"{_clean(value, PARAM_VALUE_MAX)}"'
    if isinstance(value, dict | list):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=True, default=str)
    return _clean(json.dumps(value, default=str), PARAM_VALUE_MAX)


def describe_call(call: ParkedCall) -> str:
    """What one deferred call will do, in words the owner approves and the classifier reads.

    The tool and every parameter it will run with are facts the code owns (the real tools
    take a ``target``, or a ``domain``, ``service`` and ``entity_id``; none is singled out
    here, so none can be missed). The model's reason is only a quoted aside after them, on
    one short line, so it can add context but never stand in for what runs: a reason of
    "lock the front door" on an unlock still reads as an unlock (I1)::

        call service (domain="lock", service="unlock", entity_id="lock.front") ("lock it")

    Past ``PARAM_COUNT_MAX`` parameters the rest are counted, not shown.
    """
    described = humanise(call.tool_name)
    parameters = [(key, value) for key, value in call.parameters.items() if key != "reason"]
    pairs = [
        f"{_key_shown(key)}={_value_shown(value)}" for key, value in parameters[:PARAM_COUNT_MAX]
    ]
    if len(parameters) > PARAM_COUNT_MAX:
        pairs.append(f"+{len(parameters) - PARAM_COUNT_MAX} more")
    if pairs:
        described = f"{described} ({', '.join(pairs)})"
    reason = _clean(call.reason or "", REASON_CLAUSE_MAX, reason=True)
    return f'{described} ("{reason}")' if reason else described


def summary_of(calls: list[ParkedCall]) -> str:
    """What the owner is asked to confirm: every call, as ``describe_call`` words it."""
    return "; ".join(describe_call(call) for call in calls)


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
    except ValueError as exc:  # malformed JSON or a payload that no longer validates
        # The type only: the record holds the owner's history and the calls' parameters, and
        # a validation message (like a traceback's frames) quotes them.
        logger.warning(
            "Pending record {} is unreadable ({}) — not claimable",
            approval_id,
            type(exc).__name__,
        )
        return None
    if isinstance(record, ParkedRun) and record.identity.get("identity") == _OWNER:
        return record
    return None
