"""Play one scenario variant against a running stack and collect its Evidence."""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never
from uuid import uuid4

from bus.schemas.events import AlfredResponse, UserRequest
from core.conscious.identity import IDENTITY_GUEST, IDENTITY_SIR
from evals.harness.evidence import Evidence, Reply, TranscriptTurn
from evals.harness.scenario import Actor, HaEventStep, ScenarioVariant, UserStep, WaitStep
from evals.harness.stack import (
    CONSCIOUS_SOURCE,
    EVAL_GUEST_SIGNAL_NUMBER,
    EVAL_SIGNAL_NUMBER,
    EVAL_SOURCE,
)

if TYPE_CHECKING:
    from evals.harness.evidence import LlmCall
    from evals.harness.fake_ha import FakeHA
    from evals.harness.proxy import LlmProxy

SendFn = Callable[[UserRequest, float], Awaitable[AlfredResponse]]
TIMES = "\N{MULTIPLICATION SIGN}"


class HarnessError(RuntimeError):
    """The harness, not Alfred, failed. The sample scores E and is retried once."""


@dataclass
class PlayContext:
    send: SendFn
    fake_ha: FakeHA
    proxy: LlmProxy
    reply_timeout_s: float = 120.0
    settle_s: float = 2.0
    restore_settle_s: float = 2.0
    signal_number: str = EVAL_SIGNAL_NUMBER


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


def session_id_for(sample_id: str, epoch: int) -> str:
    return f"eval-{sample_id}-e{epoch}-{uuid4().hex[:6]}"


async def play(ctx: PlayContext, variant: ScenarioVariant, epoch: int) -> Evidence:
    scenario = variant.scenario
    if await ctx.fake_ha.restore_world():
        await asyncio.sleep(ctx.restore_settle_s)
    session_id = session_id_for(variant.sample_id, epoch)
    started = time.monotonic()
    ev = Evidence(
        scenario_id=scenario.id,
        variant=variant.variant,
        epoch=epoch,
        session_id=session_id,
        started_at=started,
        ended_at=started,
    )
    for index, step in enumerate(variant.steps):
        ev.step_started.append(time.monotonic())
        match step:
            case UserStep():
                actor = step.actor or scenario.actor
                ev.transcript.append(TranscriptTurn(role="user", text=step.user))
                sent = time.monotonic()
                request = build_request(actor, step.user, session_id, ctx.signal_number)
                response = await ctx.send(request, ctx.reply_timeout_s)
                if response.source != CONSCIOUS_SOURCE:
                    window = ctx.proxy.calls_between(sent, time.monotonic())
                    raise HarnessError(
                        f"step {index}: no reply from System 2 within {ctx.reply_timeout_s:.0f}s "
                        f"(got {response.source!r}: {response.text[:120]!r})"
                        + upstream_failures(window)
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
                await ctx.fake_ha.set_state(e.entity_id, e.state, e.attributes)
                ev.transcript.append(
                    TranscriptTurn(role="event", text=f"{e.entity_id} → {e.state}")
                )
                await asyncio.sleep(step.settle)
            case WaitStep():
                await asyncio.sleep(step.wait)
            case _:
                assert_never(step)
    ended = time.monotonic()
    ev.ended_at = ended
    ev.ha_calls = ctx.fake_ha.calls_between(started, ended)
    ev.llm_calls = ctx.proxy.calls_between(started, ended)
    ev.ha_states = ctx.fake_ha.states()
    return ev
