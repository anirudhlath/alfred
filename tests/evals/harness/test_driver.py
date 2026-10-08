from __future__ import annotations

import asyncio
import re
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import pytest

from bus.schemas.events import AlfredResponse, UserRequest
from evals.harness.driver import (
    HarnessError,
    PlayContext,
    build_request,
    clock_wait_s,
    outstanding_calls,
    play,
)
from evals.harness.evidence import ClockSet, Evidence, HaCall, LlmCall
from evals.harness.fake_ha import FakeHA
from evals.harness.proxy import LlmProxy
from evals.harness.scenario import Actor, Scenario, expand_variants
from evals.harness.world import load_world
from tests.evals.harness.factories import FakeBus, evidence

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from evals.harness.driver import SendFn


def scenario(**kw) -> Scenario:  # type: ignore[no-untyped-def]
    base = {
        "id": "demo.case.one",
        "prd": ["x"],
        "status": "shipped",
        "steps": [{"user": "Turn on the bedroom lamp."}, {"user": "Thanks."}],
        "expect": [{"ha_not_called": {}}],
    }
    return Scenario.model_validate({**base, **kw})


class Recorder:
    """A stand-in for ``Stack.send``. Given a fake HA and a proxy, each turn also
    records one HA call and one LLM call, stamped inside the play window."""

    def __init__(
        self,
        source: str = "conscious-engine",
        ha: FakeHA | None = None,
        proxy: LlmProxy | None = None,
    ) -> None:
        self.requests: list[UserRequest] = []
        self.source = source
        self.ha = ha
        self.proxy = proxy

    async def __call__(self, request: UserRequest, timeout: float) -> AlfredResponse:
        self.requests.append(request)
        if self.ha is not None:
            self.ha.calls.append(
                HaCall(
                    t=time.monotonic(),
                    domain="light",
                    service="turn_on",
                    entity_ids=["light.bedroom_lamp"],
                )
            )
        if self.proxy is not None:
            self.proxy.calls.append(
                LlmCall(t=time.monotonic(), role="system2", latency_ms=1.0, status=200)
            )
        return AlfredResponse(
            source=self.source,
            channel=request.channel,
            session_id=request.session_id,
            text=f"Reply {len(self.requests)}, sir.",
        )


def ctx(  # type: ignore[no-untyped-def]
    send, ha: FakeHA | None = None, proxy: LlmProxy | None = None, bus: FakeBus | None = None, **kw
) -> PlayContext:
    return PlayContext(
        send=send,
        fake_ha=ha or FakeHA(load_world("apartment")),
        proxy=proxy or LlmProxy("http://x"),
        bus=bus or FakeBus(),
        settle_s=0,
        restore_settle_s=0,
        **kw,
    )


async def test_multi_turn_keeps_one_session_and_records_replies() -> None:
    send = Recorder()
    [variant] = expand_variants(scenario())
    ev = await play(ctx(send), variant, epoch=2)
    assert len({r.session_id for r in send.requests}) == 1
    assert ev.session_id == send.requests[0].session_id and "e2" in ev.session_id
    assert [r.text for r in ev.replies] == ["Reply 1, sir.", "Reply 2, sir."]
    assert [t.role for t in ev.transcript] == ["user", "alfred", "user", "alfred"]
    assert len(ev.step_started) == 2


@pytest.mark.parametrize(
    ("who", "channel", "claim"),
    [
        ("sir", "web_pwa", "sir"),
        ("sir", "signal", "+15550100"),
        ("guest", "web_pwa", "guest"),
        ("guest", "signal", "+15550199"),
    ],
)
def test_identity_claims(who: str, channel: str, claim: str) -> None:
    req = build_request(Actor(who=who, channel=channel), "hi", "s", "+15550100")  # type: ignore[arg-type]
    # Every real channel sends a server-derived claim with authenticated=False; the
    # claim alone picks sir or guest, through the same identity path production uses.
    assert (req.identity_claim, req.authenticated, req.channel) == (claim, False, channel)
    assert req.timezone == "America/Denver" and req.source == "alfred-evals"


async def test_a_step_actor_overrides_the_scenario_actor() -> None:
    send = Recorder()
    s = scenario(steps=[{"user": "Hello."}, {"user": "Who am I?", "as": {"who": "guest"}}])
    [variant] = expand_variants(s)
    await play(ctx(send), variant, epoch=1)
    assert [r.identity_claim for r in send.requests] == ["sir", "guest"]


async def test_a_wait_step_pauses_and_is_not_transcribed() -> None:
    send = Recorder()
    s = scenario(steps=[{"user": "Hi."}, {"wait": 0.01}, {"user": "Still there?"}])
    [variant] = expand_variants(s)
    ev = await play(ctx(send), variant, epoch=1)
    assert len(ev.step_started) == 3
    assert ev.step_started[2] - ev.step_started[1] >= 0.01
    assert [t.role for t in ev.transcript] == ["user", "alfred", "user", "alfred"]
    assert [r.step for r in ev.replies] == [0, 2]


async def test_no_conscious_reply_is_a_harness_error() -> None:
    [variant] = expand_variants(scenario())
    with pytest.raises(HarnessError, match="no reply from System 2"):
        await play(ctx(Recorder(source="channels")), variant, epoch=1)


async def test_no_reply_names_the_llm_upstreams_failures_in_that_window() -> None:
    proxy = LlmProxy("http://x")
    proxy.calls.append(LlmCall(t=0.0, role="system2", latency_ms=1.0, status=503))  # earlier

    async def vllm_down(request: UserRequest, timeout: float) -> AlfredResponse:
        now = time.monotonic()
        proxy.calls.extend(
            LlmCall(t=now, role="system2", latency_ms=1.0, status=status)
            for status in (502, 200, 502, 500)
        )
        return AlfredResponse(
            source="channels", channel=request.channel, session_id=request.session_id, text=""
        )

    [variant] = expand_variants(scenario())
    with pytest.raises(HarnessError, match="no reply from System 2") as err:
        await play(ctx(vllm_down, proxy=proxy), variant, epoch=1)
    assert str(err.value).endswith("; the LLM upstream returned 500 ×1, 502 ×2")  # noqa: RUF001


async def test_no_reply_without_upstream_failures_says_nothing_about_the_llm() -> None:
    [variant] = expand_variants(scenario())
    with pytest.raises(HarnessError) as err:
        await play(ctx(Recorder(source="channels")), variant, epoch=1)
    assert "upstream" not in str(err.value)


async def test_ha_event_step_changes_state_and_is_transcribed() -> None:
    ha = FakeHA(load_world("apartment"))
    s = scenario(
        steps=[
            {"ha_event": {"entity_id": "light.living_room_ceiling", "state": "on"}, "settle": 0},
            {"user": "Is the ceiling light on?"},
        ]
    )
    [variant] = expand_variants(s)
    ev = await play(ctx(Recorder(), ha), variant, epoch=1)
    assert ev.ha_states["light.living_room_ceiling"].state == "on"
    assert ev.transcript[0].role == "event"


LAMP_ON = {"ha_event": {"entity_id": "light.living_room_ceiling", "state": "on"}}
EXPECTS_CALL = [{"ha_called": {"domain": "light", "service": "turn_off"}}]


def ha_call(t: float) -> HaCall:
    return HaCall(t=t, domain="light", service="turn_off", entity_ids=["light.living_room_ceiling"])


async def timed_play(play_ctx: PlayContext, s: Scenario) -> tuple[float, Evidence]:
    [variant] = expand_variants(s)
    t0 = time.monotonic()
    ev = await play(play_ctx, variant, epoch=1)
    return time.monotonic() - t0, ev


async def test_an_ha_event_waits_for_the_call_the_golden_expects() -> None:
    ha = FakeHA(load_world("apartment"))
    play_ctx = ctx(Recorder(), ha)
    play_ctx.ha_call_timeout_s = 10.0

    async def react() -> None:  # System 1 answering the event, a moment later
        await asyncio.sleep(0.1)
        ha.record(ha_call(time.monotonic()))

    reacting = asyncio.create_task(react())
    elapsed, ev = await timed_play(play_ctx, scenario(steps=[LAMP_ON], expect=EXPECTS_CALL))
    await reacting
    # It returned on the call, long before the timeout, and the call is in the window.
    assert 0.1 <= elapsed < 5 and [c.service for c in ev.ha_calls] == ["turn_off"]


async def test_an_ha_event_waits_out_the_timeout_when_the_call_never_comes() -> None:
    play_ctx = ctx(Recorder())
    play_ctx.ha_call_timeout_s = 0.1
    elapsed, ev = await timed_play(play_ctx, scenario(steps=[LAMP_ON], expect=EXPECTS_CALL))
    assert elapsed >= 0.1 and ev.ha_calls == []  # no error: ha_called fails on its own


async def test_an_ha_event_waits_a_window_when_the_golden_expects_no_call() -> None:
    play_ctx = ctx(Recorder())
    play_ctx.ha_call_timeout_s = 10.0
    play_ctx.ha_event_window_s = 0.1
    elapsed, _ = await timed_play(play_ctx, scenario(steps=[LAMP_ON]))  # ha_not_called
    assert 0.1 <= elapsed < 5


async def test_an_ha_event_takes_the_window_when_the_expected_call_already_came() -> None:
    ha = FakeHA(load_world("apartment"))

    async def turns_it_off(request: UserRequest, timeout: float) -> AlfredResponse:
        ha.record(ha_call(time.monotonic()))  # ha_called is satisfied before the event
        return AlfredResponse(
            source="conscious-engine",
            channel=request.channel,
            session_id=request.session_id,
            text="Done, sir.",
        )

    play_ctx = ctx(turns_it_off, ha)
    play_ctx.ha_call_timeout_s = 10.0
    play_ctx.ha_event_window_s = 0.1
    s = scenario(steps=[{"user": "Ceiling light off."}, LAMP_ON], expect=EXPECTS_CALL)
    elapsed, ev = await timed_play(play_ctx, s)
    assert 0.1 <= elapsed < 5 and [c.service for c in ev.ha_calls] == ["turn_off"]


async def test_an_unrelated_call_does_not_end_the_wait_for_the_expected_one() -> None:
    ha = FakeHA(load_world("apartment"))
    play_ctx = ctx(Recorder(), ha)
    play_ctx.ha_call_timeout_s = 10.0

    async def react() -> None:
        await asyncio.sleep(0.05)  # a stray System 1 call first
        ha.record(HaCall(t=time.monotonic(), domain="light", service="turn_on", entity_ids=[]))
        await asyncio.sleep(0.3)
        ha.record(ha_call(time.monotonic()))

    reacting = asyncio.create_task(react())
    elapsed, ev = await timed_play(play_ctx, scenario(steps=[LAMP_ON], expect=EXPECTS_CALL))
    await reacting
    assert 0.35 <= elapsed < 5
    assert [c.service for c in ev.ha_calls] == ["turn_on", "turn_off"]


@pytest.mark.parametrize("expect", [EXPECTS_CALL, [{"ha_not_called": {}}]], ids=["call", "none"])
async def test_an_ha_event_settle_bounds_the_wait_either_way(expect: list[object]) -> None:
    play_ctx = ctx(Recorder())
    play_ctx.ha_call_timeout_s = play_ctx.ha_event_window_s = 10.0
    step = {**LAMP_ON, "settle": 0.05}
    elapsed, _ = await timed_play(play_ctx, scenario(steps=[step], expect=expect))
    assert 0.05 <= elapsed < 5


SYSTEM1_CALL = {
    "model": "m",
    "messages": [{"role": "system", "content": "You are Alfred's Reflex Engine"}],
}


@asynccontextmanager
async def background_llm_call(
    answer: Callable[[httpx.Request], Awaitable[httpx.Response]],
) -> AsyncIterator[tuple[LlmProxy, SendFn]]:
    """A proxy, and a ``send`` that replies at once while it leaves a System 1 call (one
    *answer* answers) upstream: System 1 reacting in the background."""
    proxy = LlmProxy("http://vllm.test", transport=httpx.MockTransport(answer))
    await proxy.start()
    calls: list[asyncio.Task[httpx.Response]] = []
    async with httpx.AsyncClient() as client:

        async def send(request: UserRequest, timeout: float) -> AlfredResponse:
            url = f"{proxy.url}/v1/chat/completions"
            calls.append(asyncio.create_task(client.post(url, json=SYSTEM1_CALL)))
            while not proxy.in_flight:
                await asyncio.sleep(0.005)
            return AlfredResponse(
                source="conscious-engine",
                channel=request.channel,
                session_id=request.session_id,
                text="Done, sir.",
            )

        try:
            yield proxy, send
        finally:
            for call in calls:
                call.cancel()
            await asyncio.gather(*calls, return_exceptions=True)
            await proxy.stop()


async def test_an_llm_call_still_in_flight_at_the_end_lands_in_the_evidence() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async with background_llm_call(slow) as (proxy, send):
        [variant] = expand_variants(scenario(steps=[{"user": "Lamp on."}]))
        ev = await play(ctx(send, proxy=proxy), variant, epoch=1)
    assert [c.role for c in ev.llm_calls] == ["system1"]


async def test_an_llm_call_that_never_finishes_is_a_harness_error() -> None:
    never = asyncio.Event()

    async def hang(request: httpx.Request) -> httpx.Response:
        await never.wait()  # set only once the test is done with it
        return httpx.Response(200, json={})

    async with background_llm_call(hang) as (proxy, send):
        play_ctx = ctx(send, proxy=proxy)
        play_ctx.llm_idle_timeout_s = 0.05
        [variant] = expand_variants(scenario(steps=[{"user": "Lamp on."}]))
        with pytest.raises(HarnessError, match="LLM call still in flight"):
            await play(play_ctx, variant, epoch=1)
        never.set()


@asynccontextmanager
async def hung_vllm() -> AsyncIterator[tuple[LlmProxy, Callable[[], Awaitable[None]]]]:
    """A proxy whose upstream never answers, and a coroutine that sends it one call and
    returns once the call is in flight."""
    never = asyncio.Event()

    async def hang(request: httpx.Request) -> httpx.Response:
        await never.wait()  # set only once the test is done with it
        return httpx.Response(200, json={})

    proxy = LlmProxy("http://vllm.test", transport=httpx.MockTransport(hang))
    await proxy.start()
    calls: list[asyncio.Task[httpx.Response]] = []
    async with httpx.AsyncClient() as client:

        async def call() -> None:
            before = len(proxy.in_flight_since(0.0))
            url = f"{proxy.url}/v1/chat/completions"
            calls.append(asyncio.create_task(client.post(url, json=SYSTEM1_CALL)))
            while len(proxy.in_flight_since(0.0)) == before:
                await asyncio.sleep(0.005)

        try:
            yield proxy, call
        finally:
            never.set()
            await asyncio.gather(*calls, return_exceptions=True)
            await proxy.stop()


async def test_an_orphan_llm_call_from_before_the_sample_neither_delays_nor_errors_it() -> None:
    # A call a killed container left upstream belongs to no sample: the window never sees it.
    async with hung_vllm() as (proxy, orphan):
        await orphan()
        play_ctx = ctx(Recorder(), proxy=proxy)
        play_ctx.llm_idle_timeout_s = 10.0
        elapsed, ev = await timed_play(play_ctx, scenario(steps=[{"user": "Lamp on."}]))
    assert elapsed < 5 and ev.llm_calls == []


async def test_a_reply_timeout_names_the_llm_calls_still_upstream() -> None:
    # vLLM hangs: nothing comes back to record, but the calls sit in the proxy.
    async with hung_vllm() as (proxy, call):
        await call()  # from before the step: not this step's

        async def times_out(request: UserRequest, timeout: float) -> AlfredResponse:
            await call()
            await call()
            return AlfredResponse(
                source="channels", channel=request.channel, session_id=request.session_id, text=""
            )

        [variant] = expand_variants(scenario())
        with pytest.raises(HarnessError, match="no reply from System 2") as err:
            await play(ctx(times_out, proxy=proxy), variant, epoch=1)
    assert re.search(r"\); 2 LLM calls still upstream after \d+s$", str(err.value))


async def test_world_is_restored_and_only_in_window_calls_are_kept() -> None:
    ha = FakeHA(load_world("apartment"))
    proxy = LlmProxy("http://x")
    await ha.set_state("light.bedroom_lamp", "on")
    ha.calls.append(HaCall(t=0.0, domain="light", service="turn_on", entity_ids=["light.x"]))
    proxy.calls.append(LlmCall(t=0.0, role="system2", latency_ms=1.0, status=200))
    [variant] = expand_variants(scenario())
    ev = await play(ctx(Recorder(ha=ha, proxy=proxy), ha, proxy), variant, epoch=1)
    assert ev.ha_states["light.bedroom_lamp"].state == "off"
    # One call of each kind per turn lands in the window; the stale t=0.0 ones stay out.
    assert len(ev.ha_calls) == 2 and all(c.t > 0.0 for c in ev.ha_calls)
    assert [c.entity_ids for c in ev.ha_calls] == [["light.bedroom_lamp"]] * 2
    assert len(ev.llm_calls) == 2 and all(c.t > 0.0 for c in ev.llm_calls)


def test_a_check_counting_from_a_step_not_yet_started_names_the_step() -> None:
    called = {"domain": "light", "service": "turn_on", "after_step": 1}
    s = scenario(steps=[{"user": "Hello."}, LAMP_ON], expect=[{"ha_called": called}])
    ev = evidence(step_started=[0.0])  # the driver has not started step 1
    with pytest.raises(IndexError, match="step 1 is outside the sample's 1 steps"):
        outstanding_calls(s, 1, ev, FakeHA(load_world("apartment")))


class Acting(Recorder):
    """A send that also does *act*, as Alfred would while answering."""

    def __init__(self, act: Callable[[], None], source: str = "conscious-engine") -> None:
        super().__init__(source)
        self.act = act

    async def __call__(self, request: UserRequest, timeout: float) -> AlfredResponse:
        self.act()
        return await super().__call__(request, timeout)


async def played(play_ctx: PlayContext, **fields: object) -> Evidence:
    [variant] = expand_variants(scenario(**fields))
    return await play(play_ctx, variant, epoch=1)


async def test_a_clock_step_sets_the_zone_for_its_hour_and_the_sample_puts_it_back() -> None:
    bus = FakeBus(tz="America/Denver")
    now = datetime(2026, 10, 8, 15, 10, tzinfo=UTC)
    ev = await played(
        ctx(Recorder(), bus=bus, now=lambda: now),
        steps=[{"clock": {"hour": 22}}, {"user": "Hello."}],
    )
    assert ev.clocks == [ClockSet(step=0, hour=22, tz="Etc/GMT-7")]
    assert ev.step_kinds == ["clock", "user"]
    assert ev.transcript[0].text == "it is now 22:10"
    assert bus.tz_set == ["Etc/GMT-7", "America/Denver"]


@pytest.mark.parametrize(
    ("minute", "second", "wait"),
    [(10, 0, 0.0), (57, 59, 0.0), (58, 0, 121.0), (59, 30, 31.0), (59, 59, 2.0)],
)
def test_clock_waits_out_the_last_minutes_of_an_hour(minute: int, second: int, wait: float) -> None:
    assert clock_wait_s(datetime(2026, 10, 8, 15, minute, second, tzinfo=UTC)) == wait


async def test_advance_brings_the_samples_trigger_forward_and_waits_for_its_fire() -> None:
    bus = FakeBus()
    ev = await played(
        ctx(Acting(lambda: bus.created("t1", "Laundry reminder")), bus=bus),
        steps=[{"user": "Remind me in 20 minutes to move the laundry."}, {"advance_trigger": None}],
    )
    assert bus.advanced == ["t1"]
    [advance] = ev.advances
    assert (advance.step, advance.name) == (1, "Laundry reminder")
    assert [f.trigger_id for f in ev.triggers_fired] == ["t1"]
    assert [n.title for n in ev.notifications] == ["Trigger: Laundry reminder"]
    assert ev.step_kinds == ["user", "advance_trigger"]
    assert bus.deleted == ["t1"]  # cleanup; a fired one-shot is already gone, which is fine
    assert bus.cleared == 0 and bus.tz_set == []  # neither DND nor the zone was touched


async def test_advance_without_a_trigger_is_alfreds_failure_not_the_harness() -> None:
    bus = FakeBus()
    ev = await played(
        ctx(Recorder(), bus=bus),
        steps=[{"user": "Remind me later."}, {"advance_trigger": None}],
    )
    assert ev.advances == [] and bus.advanced == []
    assert ev.transcript[-1].text == "time passes, but no reminder was set"


async def test_advancing_a_one_shot_that_already_fired_notes_it_is_gone() -> None:
    bus = FakeBus()
    ev = await played(
        ctx(Acting(lambda: bus.created("t1", "Laundry reminder")), bus=bus),
        steps=[
            {"user": "Remind me in 20 minutes to move the laundry."},
            {"advance_trigger": None},
            {"advance_trigger": None},  # the one-shot fired and was deleted at the first
        ],
    )
    assert bus.advanced == ["t1"] and [a.step for a in ev.advances] == [1]
    assert ev.transcript[-1].text == "time passes, but 'Laundry reminder' is gone"


async def test_play_cleans_up_triggers_dnd_and_clock() -> None:
    bus = FakeBus()
    ev = await played(
        ctx(
            Acting(lambda: bus.created("t9")),
            bus=bus,
            now=lambda: datetime(2026, 10, 8, 3, 0, tzinfo=UTC),
        ),
        steps=[{"clock": {"hour": 22}}, {"dnd": True, "settle": 0}, {"user": "Remind me."}],
    )
    assert "do not disturb on" in [t.text for t in ev.transcript]
    assert bus.dnd == [True] and bus.cleared == 1
    assert bus.deleted == ["t9"]
    assert bus.tz_set[-1] is None  # nothing was stored before the sample


async def test_reflex_golden_waits_out_the_attention_cooldown_after_a_restore() -> None:
    ha = FakeHA(load_world("apartment"))
    # The bedroom lamp starts off and the golden sets it off, so the golden never drifts it.
    event = {"ha_event": {"entity_id": "light.bedroom_lamp", "state": "off"}, "settle": 0}
    watches = scenario(steps=[event], expect=[{"reflex_decision": {"decision": "none"}}])
    await ha.set_state("light.bedroom_lamp", "on")  # drifted: the restore pushes it back
    elapsed, _ = await timed_play(ctx(Recorder(), ha, reflex_cooldown_s=0.4), watches)
    assert elapsed >= 0.4
    # Nothing to restore now, and the wait still applies: the last sample's event started
    # a cooldown on the very entity this golden changes.
    elapsed, _ = await timed_play(ctx(Recorder(), ha, reflex_cooldown_s=0.4), watches)
    assert elapsed >= 0.4
    plain = scenario(steps=[event])
    elapsed, _ = await timed_play(ctx(Recorder(), ha, reflex_cooldown_s=0.4), plain)
    assert elapsed < 0.3  # a golden that does not watch Reflex does not wait


async def test_an_ha_event_waits_for_system1_when_a_reflex_check_watches() -> None:
    proxy = LlmProxy("http://x")

    async def system1_answers() -> None:
        await asyncio.sleep(0.2)
        proxy.record(
            LlmCall(
                t=time.monotonic(),
                role="system1",
                latency_ms=5.0,
                status=200,
                response_text='{"decision": "none", "reason": "quiet"}',
            )
        )

    answering = asyncio.create_task(system1_answers())
    elapsed, ev = await timed_play(
        ctx(Recorder(), proxy=proxy, reflex_cooldown_s=0, reflex_timeout_s=5),
        scenario(steps=[LAMP_ON], expect=[{"reflex_decision": {"decision": "none"}}]),
    )
    await answering
    assert 0.2 <= elapsed < 2
    assert [(c.decision, c.reason) for c in ev.reflex] == [("none", "quiet")]


async def test_an_ha_event_waits_out_the_reflex_timeout_when_system1_stays_quiet() -> None:
    elapsed, ev = await timed_play(
        ctx(Recorder(), reflex_cooldown_s=0, reflex_timeout_s=0.3),
        scenario(steps=[LAMP_ON], expect=[{"reflex_decision": {"decision": "none"}}]),
    )
    assert elapsed >= 0.3 and ev.reflex == []


async def test_sent_and_held_notifications_from_the_sample_are_evidence() -> None:
    bus = FakeBus()
    bus.notify("Trigger: Before the sample", wall=time.time() - 60)

    def alfred_notifies() -> None:
        bus.notify("Trigger: Vet", urgency="urgent")
        bus.hold("Trigger: Plants")

    ev = await played(ctx(Acting(alfred_notifies), bus=bus), steps=[{"user": "Hello."}])
    assert [(n.title, n.urgency) for n in ev.notifications] == [("Trigger: Vet", "urgent")]
    assert ev.started_at <= (ev.notifications[0].t or 0) <= ev.ended_at
    assert [n.title for n in ev.deferred] == ["Trigger: Plants"]


async def test_a_clock_step_in_an_hours_last_minutes_sets_the_next_hours_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def no_wait(delay: float) -> None:
        slept.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    # 15:59:59 before the wait, 16:00:01 after it.
    times = iter(
        [datetime(2026, 10, 8, 15, 59, 59, tzinfo=UTC), datetime(2026, 10, 8, 16, 0, 1, tzinfo=UTC)]
    )
    ev = await played(
        ctx(Recorder(), now=lambda: next(times)),
        steps=[{"clock": {"hour": 22}}, {"user": "Hello."}],
    )
    assert 2.0 in slept
    assert ev.clocks == [ClockSet(step=0, hour=22, tz="Etc/GMT-6")]
    assert ev.transcript[0].text == "it is now 22:00"


async def test_advance_picks_the_newest_trigger_with_a_run_at_or_the_one_named() -> None:
    bus = FakeBus()

    def alfred_sets_three() -> None:
        bus.created("t1", "Laundry reminder")
        bus.created("t2", "Morning briefing", {"cron": "0 7 * * *"})
        bus.created("t3", "Vet")

    ev = await played(
        ctx(Acting(alfred_sets_three), bus=bus),
        steps=[
            {"user": "Remind me about the laundry and the vet, and brief me every morning."},
            {"advance_trigger": {"name": "laundry"}},  # a substring, in any case
            {"advance_trigger": {"name": "briefing"}},  # a cron trigger has no run_at to pull
            {"advance_trigger": None},  # the newest with a run_at
        ],
    )
    assert bus.advanced == ["t1", "t3"]
    assert [(a.step, a.trigger_id) for a in ev.advances] == [(1, "t1"), (3, "t3")]
    assert "time passes, but no reminder was set" in [t.text for t in ev.transcript]


async def test_an_advance_waits_for_a_fire_after_it_not_an_earlier_one() -> None:
    bus = FakeBus(fires=False)  # the engine never gets to the advanced trigger

    def alfred_sets_it_and_it_fires() -> None:
        bus.created("t1", "Laundry reminder")
        bus.fire("t1")  # a repeating trigger's earlier fire, before the advance

    elapsed, ev = await timed_play(
        ctx(Acting(alfred_sets_it_and_it_fires), bus=bus, fire_timeout_s=0.3),
        scenario(steps=[{"user": "Remind me about the laundry."}, {"advance_trigger": None}]),
    )
    assert bus.advanced == ["t1"] and len(ev.advances) == 1
    assert elapsed >= 0.3  # the earlier fire did not end the wait


async def test_an_advance_wakes_on_the_fire_as_it_lands() -> None:
    bus = FakeBus(fires=False)

    async def the_engine_fires_later() -> None:
        await asyncio.sleep(0.3)
        bus.fire("t1")

    firing = asyncio.create_task(the_engine_fires_later())
    elapsed, ev = await timed_play(
        ctx(Acting(lambda: bus.created("t1")), bus=bus, fire_timeout_s=5),
        scenario(steps=[{"user": "Remind me about the laundry."}, {"advance_trigger": None}]),
    )
    await firing
    assert 0.3 <= elapsed < 2
    assert [f.trigger_id for f in ev.triggers_fired] == ["t1"]


async def test_a_zone_an_actor_left_is_put_back_without_a_clock_step() -> None:
    bus = FakeBus()

    def conscious_stores_the_zone() -> None:
        bus.tz = "America/Denver"  # as the conscious engine does with a request's zone

    await played(ctx(Acting(conscious_stores_the_zone), bus=bus), steps=[{"user": "Hello."}])
    assert bus.tz is None and bus.tz_set == [None]


async def test_a_sample_that_raises_skips_cleanup() -> None:
    bus = FakeBus()
    send = Acting(lambda: bus.created("t1"), source="channels")  # System 2 never answers
    with pytest.raises(HarnessError, match="no reply from System 2"):
        await played(
            ctx(send, bus=bus, now=lambda: datetime(2026, 10, 8, 3, 0, tzinfo=UTC)),
            steps=[{"clock": {"hour": 22}}, {"dnd": True, "settle": 0}, {"user": "Remind me."}],
        )
    # The failed sample's stack is restarted fresh, so nothing needs undoing; and cleanup
    # through a bus that may be what failed would only raise again, over the first error.
    assert bus.deleted == [] and bus.cleared == 0 and bus.tz_set == ["Etc/GMT+5"]


async def test_the_reflex_wait_counts_only_system1_calls_after_the_push() -> None:
    proxy = LlmProxy("http://x")

    def system1_answers_early() -> None:  # during the user step, before the event
        proxy.record(
            LlmCall(
                t=time.monotonic(),
                role="system1",
                latency_ms=5.0,
                status=200,
                response_text='{"decision": "none", "reason": "quiet"}',
            )
        )

    ev = await played(
        ctx(Acting(system1_answers_early), proxy=proxy, reflex_cooldown_s=0, reflex_timeout_s=0.3),
        steps=[{"user": "Hello."}, LAMP_ON],
        expect=[{"reflex_decision": {"decision": "none"}}],
    )
    assert ev.ended_at - ev.step_start(1) >= 0.3
