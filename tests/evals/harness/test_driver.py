from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx
import pytest

from bus.schemas.events import AlfredResponse, UserRequest
from evals.harness.driver import HarnessError, PlayContext, build_request, play
from evals.harness.evidence import Evidence, HaCall, LlmCall
from evals.harness.fake_ha import FakeHA
from evals.harness.proxy import LlmProxy
from evals.harness.scenario import Actor, Scenario, expand_variants
from evals.harness.world import load_world

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


def ctx(send, ha: FakeHA | None = None, proxy: LlmProxy | None = None) -> PlayContext:  # type: ignore[no-untyped-def]
    return PlayContext(
        send=send,
        fake_ha=ha or FakeHA(load_world("apartment")),
        proxy=proxy or LlmProxy("http://x"),
        settle_s=0,
        restore_settle_s=0,
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
