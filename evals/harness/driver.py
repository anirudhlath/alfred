"""Play one scenario variant against a running stack and collect its Evidence."""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, assert_never
from uuid import uuid4
from zoneinfo import ZoneInfo

from bus.schemas.events import AlfredResponse, UserRequest
from core.conscious.identity import IDENTITY_GUEST, IDENTITY_SIR
from evals.harness.bus import zone_for_hour
from evals.harness.checks.home import satisfies
from evals.harness.collect import (
    deferred_records,
    is_fire_of,
    notification_records,
    trigger_records,
)
from evals.harness.errors import HarnessError as HarnessError  # re-exported for tasks.py
from evals.harness.evidence import (
    Advance,
    ClockSet,
    Evidence,
    Reply,
    StatePush,
    TranscriptTurn,
)
from evals.harness.reflex import judges, reflex_calls
from evals.harness.scenario import (
    Actor,
    AdvanceTriggerStep,
    ClockStep,
    DndStep,
    HaEventStep,
    ScenarioVariant,
    UserStep,
    WaitStep,
    step_kind,
)
from evals.harness.stack import (
    CONSCIOUS_SOURCE,
    EVAL_GUEST_SIGNAL_NUMBER,
    EVAL_SIGNAL_NUMBER,
    EVAL_SOURCE,
)

if TYPE_CHECKING:
    from evals.harness.bus import Bus
    from evals.harness.checks.home import HaCalledParams
    from evals.harness.evidence import HaCall, LlmCall
    from evals.harness.fake_ha import FakeHA
    from evals.harness.proxy import LlmProxy
    from evals.harness.scenario import Scenario

SendFn = Callable[[UserRequest, float], Awaitable[AlfredResponse]]
TIMES = "\N{MULTIPLICATION SIGN}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def clock_wait_s(now: datetime) -> float:
    """How long to wait before a clock step, so the hour it sets is still the hour when
    Reflex reads it. Zero, except in an hour's last two minutes, when the wait runs one
    second into the next hour."""
    if now.minute < 58:
        return 0.0
    return float((60 - now.minute) * 60 - now.second + 1)


@dataclass
class PlayContext:
    send: SendFn
    fake_ha: FakeHA
    proxy: LlmProxy
    bus: Bus
    reply_timeout_s: float = 120.0
    settle_s: float = 2.0
    restore_settle_s: float = 2.0
    # After an ha_event: how long to wait for the call_service a golden expects, and the
    # quiet window when it expects none. A step's ``settle`` replaces either.
    ha_call_timeout_s: float = 30.0
    ha_event_window_s: float = 5.0
    # After the last step: how long an LLM call still upstream may take to be recorded.
    llm_idle_timeout_s: float = 120.0
    signal_number: str = EVAL_SIGNAL_NUMBER
    # A golden that watches Reflex: how long to wait for System 1 after an ha_event (a
    # step's ``settle`` replaces it), how long System 1 must be quiet before the first step
    # so Reflex's 5 s attention cooldowns have run out, and the cap on that wait.
    reflex_timeout_s: float = 15.0
    reflex_cooldown_s: float = 6.0
    reflex_settle_cap_s: float = 60.0
    # How long a trigger brought forward may take to fire.
    fire_timeout_s: float = 15.0
    now: Callable[[], datetime] = _utc_now


def build_request(actor: Actor, text: str, session_id: str, signal_number: str) -> UserRequest:
    """A request shaped the way the real channels send one.

    Every production channel sends ``authenticated=False`` with a server-derived claim,
    so the claim alone picks sir or guest: on signal it is the sender's number, and on
    every other channel the identity name.
    """
    if actor.who == "sir":
        claim = signal_number if actor.channel == "signal" else IDENTITY_SIR
    else:
        claim = EVAL_GUEST_SIGNAL_NUMBER if actor.channel == "signal" else IDENTITY_GUEST
    return UserRequest(
        source=EVAL_SOURCE,
        channel=actor.channel,
        session_id=session_id,
        identity_claim=claim,
        authenticated=False,
        content_type="text",
        content=text,
        timezone=actor.tz,
    )


def upstream_failures(calls: list[LlmCall]) -> str:
    """``"; the LLM upstream returned 502 <times>2"`` for the non-2xx answers among *calls*,
    or ``""``: a reply timeout then says whether vLLM itself was failing."""
    statuses = Counter(c.status for c in calls if not 200 <= c.status < 300)
    if not statuses:
        return ""
    counts = ", ".join(f"{s} {TIMES}{n}" for s, n in sorted(statuses.items()))
    return f"; the LLM upstream returned {counts}"


def still_upstream(stamps: list[float], now: float) -> str:
    """``"; 2 LLM calls still upstream after 95s"`` for the calls in flight (*stamps*, the
    oldest first), or ``""``: a hung vLLM answers nothing, so only its in-flight calls say
    a reply timeout was the LLM's."""
    if not stamps:
        return ""
    calls = "call" if len(stamps) == 1 else "calls"
    return f"; {len(stamps)} LLM {calls} still upstream after {now - stamps[0]:.0f}s"


def outstanding_calls(
    scenario: Scenario, index: int, ev: Evidence, fake_ha: FakeHA
) -> list[HaCalledParams]:
    """The ``ha_called`` checks still waiting, at step *index*, for a call this step could
    make: those that could count one, and that no call so far in their range satisfies."""
    now = time.monotonic()
    outstanding = []
    for p in scenario.ha_called_counting(index):
        start = ev.started_at if p.after_step is None else ev.step_start(p.after_step)
        if not any(satisfies(p, c) for c in fake_ha.calls_between(start, now)):
            outstanding.append(p)
    return outstanding


def _satisfies_any(checks: list[HaCalledParams], call: HaCall) -> bool:
    return any(satisfies(p, call) for p in checks)


def session_id_for(sample_id: str, epoch: int) -> str:
    return f"eval-{sample_id}-e{epoch}-{uuid4().hex[:6]}"


def _is_system1(call: LlmCall) -> bool:
    return call.role == "system1"


def _system1_upstream_failures(calls: list[LlmCall]) -> str:
    """``upstream_failures`` for System 1's 5xx answers; a 4xx is Reflex's request refused."""
    return upstream_failures([c for c in calls if _is_system1(c) and c.upstream_failed])


def _answered(call: LlmCall) -> float:
    return call.t if call.answered_at is None else call.answered_at


async def _settle_reflex(ctx: PlayContext, since: float, restored_at: float) -> None:
    """Wait until System 1 has been quiet for the attention cooldown, so no entity is still
    cooling down when the golden pushes it.

    Reflex judges one event at a time, and an entity's cooldown runs from when Reflex
    judged its event, not from the restore's push. With several restored entities queued,
    the last one is judged a System 1 call or more after the push. So the quiet runs from
    the last System 1 answer since the restore (*since*), after every call in flight has
    come back. A restore Reflex does not attend to makes no call: the quiet then runs from
    *restored_at*, which also covers the cooldown the previous sample's last event started.
    """
    quiet_s = max(ctx.restore_settle_s, ctx.reflex_cooldown_s)
    deadline = restored_at + ctx.reflex_settle_cap_s
    while (now := time.monotonic()) < deadline:
        if ctx.proxy.in_flight_since(since):
            await ctx.proxy.wait_idle(since, deadline - now)
            continue
        answers = [_answered(c) for c in ctx.proxy.calls if _is_system1(c) and c.t >= since]
        until = max([restored_at, *answers]) + quiet_s
        if until <= now:
            return
        if until > deadline:
            break
        # A System 1 call that arrives in the meantime is either recorded (ending this
        # wait) or still in flight when it ends; either way the next pass sees it.
        await ctx.proxy.wait_for_call(now, until - now, _is_system1)
    now = time.monotonic()
    raise HarnessError(
        f"Reflex did not settle after the restore: System 1 was not quiet for {quiet_s:.0f}s "
        f"within {ctx.reflex_settle_cap_s:.0f}s of it"
        + still_upstream(ctx.proxy.in_flight_since(since), now)
    )


async def _set_clock(ctx: PlayContext, ev: Evidence, index: int, hour: int) -> None:
    if (wait := clock_wait_s(ctx.now())) > 0:
        await asyncio.sleep(wait)
    now = ctx.now()
    zone = zone_for_hour(hour, now)
    await ctx.bus.set_user_timezone(zone)
    ev.clocks.append(ClockSet(step=index, hour=hour, tz=zone))
    local = now.astimezone(ZoneInfo(zone))
    ev.transcript.append(TranscriptTurn(role="event", text=f"it is now {local:%H:%M}"))


async def _advance(
    ctx: PlayContext, ev: Evidence, index: int, step: AdvanceTriggerStep, started_wall: float
) -> None:
    """Make the sample's newest one-time trigger due now, then wait for it to fire."""
    created, _ = trigger_records(await ctx.bus.events(started_wall), ev.started_at, started_wall)
    name = step.advance_trigger.name
    candidates = [
        r
        for r in created
        if r.conditions.get("run_at") is not None
        and (name is None or name.lower() in r.name.lower())
    ]
    if not candidates:
        ev.transcript.append(
            TranscriptTurn(role="event", text="time passes, but no reminder was set")
        )
        return
    trigger = candidates[-1]
    t, t_wall = time.monotonic(), time.time()
    if not await ctx.bus.advance_trigger(trigger.trigger_id, ctx.now()):
        ev.transcript.append(
            TranscriptTurn(role="event", text=f"time passes, but {trigger.name!r} is gone")
        )
        return
    ev.advances.append(Advance(step=index, trigger_id=trigger.trigger_id, name=trigger.name, t=t))
    ev.transcript.append(TranscriptTurn(role="event", text=f"time passes: {trigger.name!r} is due"))

    # Read from the advance on: an earlier fire of the trigger (a repeating one's, or the
    # one an earlier advance brought) is not the fire this advance waits for. Either kind
    # of fire ends the wait: a TriggerFired, or the ActionRequest of a trigger with an action.
    timeout = ctx.fire_timeout_s if step.settle is None else step.settle
    if await ctx.bus.wait_for_event(t_wall, timeout, partial(is_fire_of, trigger=trigger)):
        await asyncio.sleep(ctx.settle_s)  # for what the fire sets off: a notification, a call


async def _collect(ctx: PlayContext, ev: Evidence, started_wall: float) -> list[str]:
    """Fill in the bus's evidence. Returns the ids of every trigger the sample created."""
    created, fired = trigger_records(
        await ctx.bus.events(started_wall),
        ev.started_at,
        started_wall,
        actions=await ctx.bus.actions(started_wall),
    )
    ev.triggers_created, ev.triggers_fired = created, fired
    ev.notifications = notification_records(
        await ctx.bus.notifications(started_wall), ev.started_at, started_wall
    )
    ev.deferred = deferred_records(await ctx.bus.deferred())
    if any(_is_system1(c) for c in ev.llm_calls):
        tools = await ctx.bus.reflex_tools()
        ev.reflex = reflex_calls(ev.llm_calls, tools, ctx.fake_ha.world)
    return [r.trigger_id for r in created]


async def _clean_up(
    ctx: PlayContext, created: list[str], touched_dnd: bool, tz_before: str | None
) -> None:
    """Leave the container as the sample found it, for the next sample in it."""
    if created:
        await ctx.bus.delete_triggers(created)
    if touched_dnd:
        await ctx.bus.clear_dnd()
    if await ctx.bus.user_timezone() != tz_before:  # a clock step, or an actor's tz
        await ctx.bus.set_user_timezone(tz_before)


async def play(ctx: PlayContext, variant: ScenarioVariant, epoch: int) -> Evidence:
    scenario = variant.scenario
    before_restore = time.monotonic()
    restored = await ctx.fake_ha.restore_world()
    if scenario.watches_reflex:
        await _settle_reflex(ctx, before_restore, time.monotonic())
    elif restored:
        await asyncio.sleep(ctx.restore_settle_s)
    session_id = session_id_for(variant.sample_id, epoch)
    started = time.monotonic()
    started_wall = time.time()
    ev = Evidence(
        scenario_id=scenario.id,
        variant=variant.variant,
        epoch=epoch,
        session_id=session_id,
        started_at=started,
        ended_at=started,
    )
    tz_before = await ctx.bus.user_timezone()
    touched_dnd = False
    for index, step in enumerate(variant.steps):
        ev.step_started.append(time.monotonic())
        kind = step_kind(step)
        assert kind is not None  # a parsed step always has one
        ev.step_kinds.append(kind)
        match step:
            case UserStep():
                actor = step.actor or scenario.actor
                ev.transcript.append(TranscriptTurn(role="user", text=step.user))
                sent = time.monotonic()
                request = build_request(actor, step.user, session_id, ctx.signal_number)
                response = await ctx.send(request, ctx.reply_timeout_s)
                if response.source != CONSCIOUS_SOURCE:
                    now = time.monotonic()
                    raise HarnessError(
                        f"step {index}: no reply from System 2 within {ctx.reply_timeout_s:.0f}s "
                        f"(got {response.source!r}: {response.text[:120]!r})"
                        + upstream_failures(ctx.proxy.calls_between(sent, now))
                        + still_upstream(ctx.proxy.in_flight_since(sent), now)
                    )
                ev.replies.append(
                    Reply(
                        step=index,
                        text=response.text,
                        source=response.source,
                        actions_taken=list(response.actions_taken),
                        latency_ms=(time.monotonic() - sent) * 1000,
                    )
                )
                ev.transcript.append(TranscriptTurn(role="alfred", text=response.text))
                await asyncio.sleep(ctx.settle_s)
            case HaEventStep():
                e = step.ha_event
                pushed = time.monotonic()
                await ctx.fake_ha.set_state(e.entity_id, e.state, e.attributes)
                ev.state_pushes.append(StatePush(step=index, entity_id=e.entity_id, state=e.state))
                ev.transcript.append(
                    TranscriptTurn(role="event", text=f"{e.entity_id} → {e.state}")
                )
                # A golden that watches Reflex waits for System 1's answer to this event: a
                # call is recorded once it completes, stamped with when it arrived. A call
                # about another change (a restore's, a replay) does not end the wait.
                if scenario.watches_reflex:
                    timeout = ctx.reflex_timeout_s if step.settle is None else step.settle
                    about = partial(
                        judges, world=ctx.fake_ha.world, entity_id=e.entity_id, state=e.state
                    )
                    await ctx.proxy.wait_for_call(pushed, timeout, about)
                # Spec: wait for a call_service, or a 5 s window when the scenario expects
                # nothing. It still expects one only while an ha_called check is unmet.
                elif outstanding := outstanding_calls(scenario, index, ev, ctx.fake_ha):
                    wanted = partial(_satisfies_any, outstanding)
                    timeout = ctx.ha_call_timeout_s if step.settle is None else step.settle
                    if await ctx.fake_ha.wait_for_call(pushed, timeout, wanted):
                        await asyncio.sleep(ctx.settle_s)  # for the call's side effects
                else:
                    await asyncio.sleep(
                        ctx.ha_event_window_s if step.settle is None else step.settle
                    )
            case WaitStep():
                await asyncio.sleep(step.wait)
            case ClockStep():
                await _set_clock(ctx, ev, index, step.clock.hour)
            case AdvanceTriggerStep():
                await _advance(ctx, ev, index, step, started_wall)
            case DndStep():
                await ctx.bus.set_dnd(step.dnd)
                touched_dnd = True
                state = "on" if step.dnd else "off"
                ev.transcript.append(TranscriptTurn(role="event", text=f"do not disturb {state}"))
                await asyncio.sleep(step.settle)
            case _:
                assert_never(step)
    # A call is recorded when upstream answers, stamped with when it arrived: one sent
    # since ``started`` and still upstream belongs in this window, so wait for it before
    # reading the window. One from before ``started`` never enters it and is not waited on.
    if not await ctx.proxy.wait_idle(started, ctx.llm_idle_timeout_s):
        raise HarnessError(
            f"LLM call still in flight {ctx.llm_idle_timeout_s:.0f}s after the last step; "
            "the evidence would miss it"
        )
    ended = time.monotonic()
    ev.ended_at = ended
    ev.ha_calls = ctx.fake_ha.calls_between(started, ended)
    ev.llm_calls = ctx.proxy.calls_between(started, ended)
    if scenario.watches_reflex and (failures := _system1_upstream_failures(ev.llm_calls)):
        # Reflex does not ACK an event its model call failed on, and replays it about a
        # minute later, into a later sample. The restart this raise brings clears that.
        raise HarnessError(
            "System 1's LLM upstream failed, so Reflex judged nothing and will replay the "
            "event into a later sample" + failures
        )
    ev.ha_states = ctx.fake_ha.states()
    # Cleanup runs only on this path: a sample that raised leaves a dirty stack, and the
    # next sample restarts it (tasks.reset_or_recover).
    created = await _collect(ctx, ev, started_wall)
    await _clean_up(ctx, created, touched_dnd, tz_before)
    return ev
