# tests/evals/harness/test_driver.py
from __future__ import annotations

import pytest

from bus.schemas.events import AlfredResponse, UserRequest
from evals.harness.driver import HarnessError, PlayContext, build_request, play
from evals.harness.evidence import HaCall
from evals.harness.fake_ha import FakeHA
from evals.harness.proxy import LlmProxy
from evals.harness.scenario import Actor, Scenario, expand_variants
from evals.harness.world import load_world


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
    def __init__(self, source: str = "conscious-engine") -> None:
        self.requests: list[UserRequest] = []
        self.source = source

    async def __call__(self, request: UserRequest, timeout: float) -> AlfredResponse:
        self.requests.append(request)
        return AlfredResponse(
            source=self.source,
            channel=request.channel,
            session_id=request.session_id,
            text=f"Reply {len(self.requests)}, sir.",
        )


def ctx(send, ha: FakeHA | None = None) -> PlayContext:  # type: ignore[no-untyped-def]
    return PlayContext(
        send=send,
        fake_ha=ha or FakeHA(load_world("apartment")),
        proxy=LlmProxy("http://x"),
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
    ("who", "channel", "claim", "authenticated"),
    [
        ("sir", "web_pwa", "sir", True),
        ("sir", "signal", "+15550100", False),
        ("guest", "web_pwa", "guest", False),
        ("guest", "signal", "+15550199", False),
    ],
)
def test_identity_claims(who: str, channel: str, claim: str, authenticated: bool) -> None:
    req = build_request(Actor(who=who, channel=channel), "hi", "s", "+15550100")  # type: ignore[arg-type]
    assert (req.identity_claim, req.authenticated, req.channel) == (claim, authenticated, channel)
    assert req.timezone == "America/Denver" and req.source == "alfred-evals"


async def test_no_conscious_reply_is_a_harness_error() -> None:
    [variant] = expand_variants(scenario())
    with pytest.raises(HarnessError, match="no reply from System 2"):
        await play(ctx(Recorder(source="channels")), variant, epoch=1)


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


async def test_world_is_restored_and_only_in_window_calls_are_kept() -> None:
    ha = FakeHA(load_world("apartment"))
    await ha.set_state("light.bedroom_lamp", "on")
    ha.calls.append(HaCall(t=0.0, domain="light", service="turn_on", entity_ids=["light.x"]))
    [variant] = expand_variants(scenario())
    ev = await play(ctx(Recorder(), ha), variant, epoch=1)
    assert ev.ha_states["light.bedroom_lamp"].state == "off"
    assert ev.ha_calls == []
