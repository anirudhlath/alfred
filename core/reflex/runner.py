"""Reflex Runner — orchestration loop for the System 1 pipeline.

Reads events from Redis Streams (consumer group), runs the Reflex Engine, and records its
decisions. Shadow mode (#285): nothing executes — proposals that stand and invalid ones are
published as observations, and every decision and its outcome are counted. Each decision is
kept in the decision log, and each change a person makes to a device Reflex sees is noted, so
the engine can see what a person just changed.

A proposal that stands is then put to the owner, or held for a named reason: ``ask_or_hold`` is
the one step, between the engine and the record, that both origins (a state change and a fired
trigger) take. Nothing executes without the owner's yes.
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import redis.asyncio as aioredis

from bus.schemas.events import ReflexObservation, ReflexOutcome, StateChangedEvent
from core.attribution.cause import cause_of
from core.attribution.log import record_change
from core.reflex.asks import gate, open_ask
from core.reflex.availability import bridge_availability
from core.reflex.outcome import publishes
from core.reflex.prompt import HOUSE_DOMAINS, _name, render_change
from core.reflex.reversal import _split_tool, resolve_targets
from core.reflex.settings import load_settings
from core.routing.risk import tool_risk
from shared.redis_streams import REPLAY_MARKER_TTL_SECONDS
from shared.streams import (
    OBSERVED_ENTITY_PREFIX,
    REFLEX_DECIDED_PREFIX,
    REFLEX_DECISIONS_PREFIX,
    decode_stream_value,
)
from shared.types import AioRedis as AioRedis  # noqa: TC001  # re-export for backward compat
from shared.usertime import get_user_timezone

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import date
    from typing import Literal

    from pydantic import BaseModel

    from bus.schemas.events import ActionRequest, ReflexProposal, TriggerFired
    from core.notifications.dnd import DNDChecker
    from core.notifications.publisher import NotificationPublisher
    from core.reflex.attention import AttentionSet
    from core.reflex.decision_log import DecisionLog
    from core.reflex.engine import ReflexEngine
    from core.reflex.outcome import Decided
    from core.reflex.prompt import LiveEntity
    from core.reflex.reversal import PersonChanges
    from core.routing.domain_router import DomainAgent

logger = logging.getLogger(__name__)

# The declared risks a yes can act on: the router refuses a critical or an unknown tool even
# when it is confirmed. An elevated tool is asked about as a script, with its own wording.
_ASKABLE_RISKS: Final = frozenset({"benign", "elevated"})
_SCRIPT_RISK: Final = "elevated"


def utc_now() -> datetime:
    """The current moment, aware, in UTC: the clock Reflex's recorders and asks default to."""
    return datetime.now(UTC)


def _debounce_default() -> int:
    """Read the debounce window from the env, tolerating garbage.

    This module is imported at module scope by several unrelated services
    (for ``ensure_consumer_group``), so a malformed value must never raise
    at import time and take them down with it.
    """
    raw = os.getenv("OBSERVATION_DEBOUNCE_SECONDS", "").strip()
    if not raw:
        return 300
    try:
        seconds = int(raw)
    except ValueError:
        logger.warning("Invalid OBSERVATION_DEBOUNCE_SECONDS %r — using 300", raw)
        return 300
    if seconds < 1:
        # Silently clamping made "0 to disable" look like it worked while
        # actually recording on a 1-second window.
        logger.warning(
            "OBSERVATION_DEBOUNCE_SECONDS %r is below the 1s minimum — clamping to 1. "
            "Passive observation cannot be disabled this way.",
            raw,
        )
        return 1
    return seconds


# Per-entity window for passive observation. Deliberately separate from the
# attention gate's 5-second cooldown: that one asks "is this SLM call worth
# making", this one asks "is this worth remembering". Values differ by two
# orders of magnitude. Tunable without a rebuild — see the review point in
# docs/superpowers/specs/2026-09-03-passive-observation-design.md.
OBSERVATION_DEBOUNCE_SECONDS = _debounce_default()


async def publish_observation(
    redis: AioRedis,
    stream: str,
    origin: Literal["state_change", "trigger_fired"],
    trigger_event: BaseModel,
    action: ActionRequest,
    result: BaseModel,
) -> None:
    """Publish a ReflexObservation to the observation stream."""
    observation = ReflexObservation(
        source="reflex-engine",
        origin=origin,
        trigger_event=trigger_event.model_dump(),
        action=action,
        result=result,
    )
    await redis.xadd(stream, {"event": observation.model_dump_json()})


DECISION_COUNT_TTL_SECONDS = 30 * 24 * 3600


def decisions_key(day: date) -> str:
    """The hash that counts one UTC day's Reflex decisions."""
    return f"{REFLEX_DECISIONS_PREFIX}{day.isoformat()}"


async def count_decision(redis: AioRedis, decision: str, *, now: datetime | None = None) -> None:
    """Count one decision for its UTC day. Best-effort: failures are logged, never raised.

    "none" observations are debounced per entity, so the stream undercounts them; these
    counters are the shadow report's true totals.
    """
    key = decisions_key((now or datetime.now(UTC)).astimezone(UTC).date())
    try:
        await redis.hincrby(key, decision, 1)
        await redis.expire(key, DECISION_COUNT_TTL_SECONDS)
    except Exception as e:
        logger.warning("Decision count failed (%s): %s", decision, e)


async def count_outcome(
    redis: AioRedis, outcome: ReflexOutcome, *, now: datetime | None = None
) -> None:
    """Count one outcome in its UTC day's hash. Best-effort: failures are logged, never raised.

    The field is ``outcome:<kind>``, and ``<kind>:<reason>`` too when the outcome has a reason
    (a held ask says why). They sit beside the decision counts: the model's own answer
    (``act``, ``ask``, ``none``, ``invalid``) is still counted as it said it.
    """
    key = decisions_key((now or datetime.now(UTC)).astimezone(UTC).date())
    fields = [f"outcome:{outcome.kind}"]
    if outcome.reason is not None:
        fields.append(f"{outcome.kind}:{outcome.reason}")
    try:
        for field in fields:
            await redis.hincrby(key, field, 1)
        await redis.expire(key, DECISION_COUNT_TTL_SECONDS)
    except Exception as e:
        logger.warning("Outcome count failed (%s): %s", outcome.kind, e)


async def record_decision(
    redis: AioRedis,
    decision_log: DecisionLog | None,
    origin: Literal["state_change", "trigger_fired"],
    event: StateChangedEvent | TriggerFired,
    decided: Decided,
) -> None:
    """Keep and count one decision: the decision log, the decision, then its outcome.

    The one step both origins take, ahead of their own publish or passive observation and the
    ``mark_decided`` that follows. Best-effort throughout: none of the three writes raises.
    """
    if decision_log is not None:
        await decision_log.record(origin, event, decided)
    await count_decision(redis, decided.proposal.decision)
    await count_outcome(redis, decided.outcome)


def decided_key(event_id: str) -> str:
    """The marker that Reflex has decided one event."""
    return f"{REFLEX_DECIDED_PREFIX}{event_id}"


async def already_decided(redis: AioRedis, event_id: str) -> bool:
    """Whether Reflex has already decided *event_id* and counted and published the decision.

    A redelivery of an entry (the reclaim pass replays one left un-ACKed) must not ask the
    model about the same event again. Raises when Redis cannot answer: the entry then stays
    pending and is replayed, where guessing "not decided" could decide it twice.
    """
    return await redis.exists(decided_key(event_id)) == 1


async def mark_decided(redis: AioRedis, event_id: str) -> None:
    """Mark *event_id* decided for twice the replay window (``SET NX EX``).

    Call it once the decision is counted and published, so a crash before then decides the
    event again. Best-effort: under Redis's maxmemory ``SET`` is refused while ``XACK`` still
    works, and failing the entry would only decide it a second time. A refusal is logged.
    """
    try:
        await redis.set(decided_key(event_id), 1, nx=True, ex=REPLAY_MARKER_TTL_SECONDS)
    except Exception as e:
        logger.warning("Decided marker not saved for %s: %s", event_id, e)


async def publish_proposal(
    redis: AioRedis,
    stream: str,
    origin: Literal["state_change", "trigger_fired"],
    trigger_event: BaseModel,
    proposal: ReflexProposal,
    *,
    outcome: ReflexOutcome | None = None,
) -> None:
    """Publish an observation carrying a proposal Reflex did not execute, and what it came to."""
    observation = ReflexObservation(
        source="reflex-engine",
        origin=origin,
        trigger_event=trigger_event.model_dump(),
        proposal=proposal,
        outcome=outcome,
    )
    await redis.xadd(stream, {"event": observation.model_dump_json()})


async def observe_passively(
    redis: AioRedis,
    stream: str,
    event: StateChangedEvent,
    debounce_seconds: int = OBSERVATION_DEBOUNCE_SECONDS,
) -> bool:
    """Record an event the Reflex Engine saw but took no action on.

    Debounced per entity so one flapping device cannot flood episodic
    memory. Returns True if an observation was published.
    """
    seen_key = f"{OBSERVED_ENTITY_PREFIX}{event.entity_id}"
    # Redis rejects a zero TTL outright ("invalid expire time"), which under
    # the no-action path would raise on every event it is meant to record.
    if not await redis.set(seen_key, "1", nx=True, ex=max(1, debounce_seconds)):
        logger.debug("Observation debounced: %s", event.entity_id)
        return False

    observation = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event=event.model_dump(),
    )
    try:
        await redis.xadd(stream, {"event": observation.model_dump_json()})
    except Exception:
        # Release the window, or the redelivered event finds the key already
        # set and the retry silently records nothing. Suppressed: a failing DEL
        # would replace the original exception and hide why the publish failed.
        with contextlib.suppress(Exception):
            await redis.delete(seen_key)
        raise
    logger.debug("Observed: %s (%s → %s)", event.entity_id, event.old_state, event.new_state)
    return True


async def ensure_consumer_group(
    redis: AioRedis,
    stream: str,
    group: str,
    *,
    start_id: str = "0",
) -> None:
    """Create a consumer group if it doesn't already exist.

    A new group reads from *start_id*: ``"0"``, the default, delivers the stream's whole
    history; ``"$"`` only what is added after the group is created.
    """
    try:
        await redis.xgroup_create(stream, group, id=start_id, mkstream=True)
        logger.info("Created consumer group '%s' on stream '%s'", group, stream)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" in str(e):
            logger.debug("Consumer group '%s' already exists", group)
        else:
            raise


def _asked_targets(action: ActionRequest, snapshot: Mapping[str, LiveEntity]) -> list[str]:
    """What an ask is about: the action's target resolved against the live state.

    A target that names nothing there (a tool of a service that is not the home) stands for
    itself; an action with no usable target has none.
    """
    target = action.parameters.get("target")
    if not isinstance(target, str) or not target.strip():
        return []
    return resolve_targets(action.tool_name, target, snapshot) or [target]


async def ask_or_hold(
    redis: AioRedis,
    notifier: NotificationPublisher | None,
    dnd: DNDChecker | None,
    origin: Literal["state_change", "trigger_fired"],
    event: StateChangedEvent | TriggerFired,
    decided: Decided,
    *,
    clock: Callable[[], datetime],
) -> Decided:
    """*decided*, with a standing proposal put to the owner or held for a named reason.

    The one step between the engine and the record, for both origins. Only a ``shadow`` outcome
    is gated: any other comes back as it is, and nothing is resolved or read for it. So does a
    ``shadow`` one when *notifier* or *dnd* is missing (a caller that wires no asks). Otherwise
    the owner's settings, the tool's declared risk and their time zone are read and the ask gate
    run (``core.reflex.asks.gate``). A tool whose risk is not benign or elevated is never asked
    about, because the router refuses it even when confirmed. In ``shadow`` mode the outcome
    stays ``shadow`` with the reason a limit would have given; in ``ask`` mode the limits make
    it ``held`` with their reason, or ``open_ask`` parks it and sends the notification and the
    outcome is ``asked`` with the ask's id. An ask that lost the claim to another is ``held``
    for ``ask_open``.

    A failure in any of those reads, in the gate or in ``open_ask`` propagates, so the event
    stays pending and is retried (fail closed, as with ``PersonChanges.recent``). A retry finds
    the ask open, so it is held and never asked twice; ``open_ask`` notifies last, so the
    owner hears of an ask at most once.
    """
    action = decided.proposal.action
    if decided.outcome.kind != "shadow" or action is None or notifier is None or dnd is None:
        return decided
    snapshot = decided.snapshot
    settings = await load_settings(redis)
    risk = await tool_risk(redis, action.target_service, action.tool_name)
    tz_name = await get_user_timezone(redis)
    now = clock()
    targets = _asked_targets(action, snapshot)
    outcome = await gate(
        redis,
        settings,
        decided.outcome,
        decided.proposal,
        targets=targets,
        tz_name=tz_name,
        now=now,
        dnd=dnd,
        askable=risk in _ASKABLE_RISKS,
    )
    if outcome.kind == "asked":
        split = _split_tool(action.tool_name, snapshot)
        ask = await open_ask(
            redis,
            notifier,
            decided.proposal,
            settings=settings,
            origin=origin,
            noticed=(
                render_change(event, snapshot)
                if isinstance(event, StateChangedEvent)
                else event.trigger_name
            ),
            targets=targets,
            names={t: _name(t, snapshot[t].attributes) if t in snapshot else t for t in targets},
            script=risk == _SCRIPT_RISK,
            now=now,
            tz_name=tz_name,
            service=None if split is None else split[1],
        )
        outcome = (
            ReflexOutcome(kind="held", reason="ask_open")
            if ask is None
            else ReflexOutcome(kind="asked", ask_id=ask.ask_id)
        )
    return dataclasses.replace(decided, outcome=outcome)


async def process_stream_entry(
    entry_id: bytes | str,
    entry_data: Mapping[str | bytes, str | bytes],
    engine: ReflexEngine,
    agent: DomainAgent,
    redis: AioRedis,
    result_stream: str,
    observation_stream: str,
    attention: AttentionSet | None = None,
    *,
    decision_log: DecisionLog | None = None,
    person_changes: PersonChanges | None = None,
    notifier: NotificationPublisher | None = None,
    dnd: DNDChecker | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> bool:
    """Process a single Redis Stream entry. Returns True if an action was taken — never,
    in shadow mode (#285); ``agent`` and ``result_stream`` wait for #286.

    Raises on retriable errors (e.g., the model server down) so the caller can choose not
    to ACK the message. Returns False — and is ACKed by the caller — for
    malformed events, availability blips (``core/reflex/availability.py``),
    attention-gated events, and events the engine chose not to act on. That
    last branch is not a no-op: it records a debounced passive observation,
    best-effort, so a failed write never blocks the ACK. So does a proposal that would
    reverse a person's change (its outcome is ``would_reverse``). Every other proposal is
    recorded too, undebounced and best-effort, with its outcome.

    A change to a device Reflex sees is noted in *person_changes* (when given) ahead of the
    gate, so Reflex knows what a person just did even where it did not decide. The decision,
    once made, goes to *decision_log* (when given).

    A proposal that stands is then put to the owner or held (``ask_or_hold``), when *notifier*
    and *dnd* are given: without either, a ``shadow`` outcome is recorded as it is. *clock*
    gives the moment the ask limits are read at.

    A redelivery is not decided again: after the gate, an event already marked decided
    (``already_decided``) is skipped, and the event is marked once its decision is counted and
    published. A delivery that raised before then leaves it unmarked, so a redelivery decides:
    one that raised after an ask went out finds it open and records the proposal as held.
    """
    raw_event = entry_data.get("event") or entry_data.get(b"event")
    if raw_event is None:
        logger.warning("Stream entry missing 'event' field: %s", entry_data)
        return False

    event_str = decode_stream_value(raw_event)

    try:
        event = StateChangedEvent.model_validate_json(event_str)
    except Exception as e:
        logger.error("Failed to parse event: %s — %s", e, event_str[:200])
        return False

    # Availability bridge — a device dropping off the network and coming back is
    # not a change. Ahead of the gate, so a blip costs no inference and does not
    # start the cooldown that would swallow a real change right behind it.
    bridged = await bridge_availability(redis, event, entry_id)
    if bridged is None:
        return False
    event = bridged

    # Count the change by its cause. Ahead of the gate, so the changes it drops are counted
    # too; once per entry, so a redelivery after a failed inference is not counted again.
    # Best-effort (record_change never raises): it never changes a decision or the ACK.
    await record_change(redis, event, entry_id=entry_id)

    # Note what a person just changed, so Reflex never proposes undoing it. Only the devices
    # Reflex sees and acts on (the House block's domains): every sensor update has the cause
    # "outside", and noting those would fill the record with every sensor. Ahead of the gate,
    # like the count above: a gated change is still the latest word on its device. Best-effort
    # (note never raises), and idempotent for a redelivery.
    if person_changes is not None and event.entity_id.partition(".")[0] in HOUSE_DOMAINS:
        await person_changes.note(event, cause_of(event.context))

    # Attention gate — only attention-set members on real transitions reach
    # the SLM. Gated events stay fully visible to triggers and context
    # (they consume the stream independently / via home-service snapshots).
    if attention is not None and not await attention.should_fire(event):
        logger.debug(
            "Attention-gated: %s (%s → %s)", event.entity_id, event.old_state, event.new_state
        )
        return False

    # A redelivered entry (an un-ACKed one the reclaim pass replays, or a crash between the
    # decision and the ACK) is not decided again: the model is asked once per event.
    if await already_decided(redis, event.event_id):
        logger.info("Event %s already decided — not asking the model again", event.event_id)
        return False

    # engine.process_event() calls the model. If it is down this raises
    # (httpx.ConnectError, etc.) and the caller does NOT ACK — Redis redelivers.
    decided = await engine.process_event(event)
    decided = await ask_or_hold(redis, notifier, dnd, "state_change", event, decided, clock=clock)
    await record_decision(redis, decision_log, "state_change", event, decided)
    await _record_outcome(redis, observation_stream, event, decided)
    # Counted and published, so a redelivery stops here; a crash before this line decides again.
    await mark_decided(redis, event.event_id)
    return False


async def _record_outcome(
    redis: AioRedis, observation_stream: str, event: StateChangedEvent, decided: Decided
) -> None:
    """Record what Reflex decided about a state change. Best-effort: nothing here raises."""
    proposal = decided.proposal
    if not publishes(decided.outcome):
        # Record it rather than dropping it. Without this Alfred remembers
        # only what it did, never what it saw, and pattern detection has
        # nothing to run over. A proposal that would reverse a person's change lands here
        # too: it becomes a "none" for everything downstream.
        #
        # Isolated — failures don't block ACK. Recording is bookkeeping for an
        # event the engine has already finished handling, and under Redis
        # maxmemory the deny-oom commands it needs (SET, XADD) are rejected
        # while XREADGROUP/XACK still succeed. Propagating would leave every
        # no-action event un-ACKed and feed each one back into a fresh SLM
        # inference on the next reclaim pass.
        try:
            await observe_passively(redis, observation_stream, event)
        except Exception as e:
            logger.warning("Passive observation failed for %s: %s", event.entity_id, e)
        return

    # Shadow mode (#285): record what Reflex would do and execute nothing. Not
    # debounced — proposals are rare and are the evidence this slice collects.
    # Isolated like the passive path, for the same maxmemory reason.
    if proposal.decision == "invalid":
        logger.warning("Invalid Reflex output for %s: %s", event.entity_id, proposal.problem)
    try:
        await publish_proposal(
            redis, observation_stream, "state_change", event, proposal, outcome=decided.outcome
        )
    except Exception as e:
        logger.warning("Proposal observation failed for %s: %s", event.entity_id, e)
        return
    tool = proposal.action.tool_name if proposal.action is not None else "-"
    logger.info(
        "Reflex %s for %s: %s (%s)", proposal.decision, event.entity_id, tool, decided.outcome.kind
    )
