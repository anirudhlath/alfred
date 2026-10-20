"""The door step: the harness decides a pending action through the web API, as the PWA's
Door does, and reads the answer the resumed run publishes."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from bus.schemas.events import AlfredResponse
from evals.harness.checks import run_check
from evals.harness.driver import MISSING_PENDING_ACTION_ID
from evals.harness.evidence import HaCall
from evals.harness.fake_ha import FakeHA
from evals.harness.world import load_world
from tests.evals.harness.factories import FakeBus
from tests.evals.harness.test_checks_pending import pending_action
from tests.evals.harness.test_driver import Recorder, ctx, played, scenario

if TYPE_CHECKING:
    from bus.schemas.events import UserRequest
    from evals.harness.evidence import Evidence

UNLOCK = {"user": "Unlock the front door."}
SELECTOR = {"at_step": 0, "tool": "home_lock_unlock"}
CONFIRM = {"door": "confirm", "pending_action": SELECTOR, "settle": 0}
DENY = {"door": "deny", "pending_action": SELECTOR, "settle": 0}
UNLOCKED = "The front door is unlocked, sir."


def followup(request_id: str, text: str = UNLOCKED, session_id: str = "") -> AlfredResponse:
    return AlfredResponse(
        source="conscious-engine",
        channel="web_pwa",
        session_id=session_id,
        text=text,
        in_reply_to=request_id,
        followup=True,
    )


class Door:
    """A stack where the first user step parks a lock call, and a Door decision resumes the
    run: it clears the record, makes the call on approval, and publishes the follow-up."""

    def __init__(self, bus: FakeBus, ha: FakeHA, *, answer: bool = True) -> None:
        self.bus, self.ha, self.answer = bus, ha, answer
        bus.on_decided = self.decided
        self.request_ids: list[str] = []
        self.sessions: list[str] = []
        self.tasks: list[asyncio.Task[None]] = []

    async def send(self, request: UserRequest, timeout: float) -> AlfredResponse:
        self.request_ids.append(request.event_id)
        self.sessions.append(request.session_id)
        if len(self.request_ids) == 1:
            self.bus.pending.append(pending_action("real-id"))
        return await Recorder()(request, timeout)

    def decided(self, request_id: str, decision: str) -> None:
        self.tasks.append(asyncio.create_task(self._resume(decision)))

    async def _resume(self, decision: str) -> None:
        await asyncio.sleep(0.02)
        # A follow-up to some other request lands first: it is not this step's reply.
        self.bus.respond(followup("another-request", "Something else, sir."))
        if decision == "confirm":
            self.ha.record(HaCall(t=time.monotonic(), domain="lock", service="unlock"))
            await self.ha.set_state("lock.front_door", "unlocked")
        if self.answer:
            text = UNLOCKED if decision == "confirm" else "Understood, sir. I left it locked."
            self.bus.respond(followup(self.request_ids[0], text, self.sessions[0]))


def checks(ev: Evidence, *expect: dict[str, object]) -> list[str]:
    s = scenario(steps=[{"user": "a"}, CONFIRM], expect=list(expect))
    return [run_check(c.name, c.params, ev).status for c in s.expect]


async def test_a_confirmed_door_step_records_the_followup_as_the_steps_reply() -> None:
    bus, ha = FakeBus(), FakeHA(load_world("apartment"))
    door = Door(bus, ha)
    ev = await played(
        ctx(door.send, ha=ha, bus=bus, ha_call_timeout_s=0.5, reply_timeout_s=1),
        steps=[UNLOCK, CONFIRM],
        expect=[{"ha_not_called": {"at_step": 0}}],
    )
    await asyncio.gather(*door.tasks)
    assert bus.decisions == [("real-id", "confirm")]
    # The tail is taken before the decision, so only what the decision causes is read.
    assert bus.door_log == ["responses_tail", "decide_pending", "followup"]
    assert ev.step_kinds == ["user", "door"]
    assert [(r.step, r.text) for r in ev.replies] == [(0, "Reply 1, sir."), (1, UNLOCKED)]
    assert ev.replies[1].source == "conscious-engine" and ev.replies[1].latency_ms > 0
    assert [(t.role, t.text) for t in ev.transcript] == [
        ("user", "Unlock the front door."),
        ("alfred", "Reply 1, sir."),
        (
            "event",
            "step 1: pending selector at step 0 for home_lock_unlock matched 1 actions; "
            "selected real-id",
        ),
        ("event", "door confirm → 200"),
        ("alfred", UNLOCKED),
    ]
    assert ev.pending[0].actions and ev.pending[1].actions == []
    assert ev.ha_states["lock.front_door"].state == "unlocked"
    assert checks(
        ev,
        {"ha_called": {"domain": "lock", "service": "unlock", "at_step": 1, "count": 1}},
        {"pending_action_absent": {"at_step": 1, "tool": "home.lock_unlock"}},
        {"reply_contains": {"regex": r"\bunlock(ed)?\b", "step": 1}},
    ) == ["pass", "pass", "pass"]


async def test_a_denied_door_step_posts_the_deny_and_records_the_reply() -> None:
    bus, ha = FakeBus(), FakeHA(load_world("apartment"))
    door = Door(bus, ha)
    ev = await played(
        ctx(door.send, ha=ha, bus=bus, reply_timeout_s=1),
        steps=[UNLOCK, DENY],
        expect=[{"ha_not_called": {}}],
    )
    await asyncio.gather(*door.tasks)
    assert bus.decisions == [("real-id", "deny")]
    assert [t.text for t in ev.transcript if t.role == "event"][-1] == "door deny → 200"
    assert [r.text for r in ev.replies] == ["Reply 1, sir.", "Understood, sir. I left it locked."]
    assert ev.ha_calls == []


async def test_a_404_door_step_records_no_reply_and_does_not_fail_the_run() -> None:
    bus = FakeBus()
    sent: list[str] = []

    async def send(request: UserRequest, timeout: float) -> AlfredResponse:
        sent.append(request.content)
        return await Recorder()(request, timeout)

    ev = await played(
        ctx(send, bus=bus, reply_timeout_s=5),  # a wait on a follow-up would outlast the test
        steps=[UNLOCK, CONFIRM],
        expect=[{"ha_not_called": {}}],
    )
    # The step 0 snapshot holds nothing, so the selector names the fixed absent id.
    assert bus.decisions == [(MISSING_PENDING_ACTION_ID, "confirm")]
    assert bus.door_log == ["responses_tail", "decide_pending"]
    assert [r.step for r in ev.replies] == [0]
    assert [t.text for t in ev.transcript if t.role == "event"][-1] == "door confirm → 404"
    assert checks(ev, {"reply_contains": {"regex": r"\bunlock(ed)?\b", "step": 1}}) == ["fail"]


async def test_a_door_step_the_store_refuses_records_no_reply_and_leaves_the_record() -> None:
    bus = FakeBus()
    bus.door_status = 503
    door = Door(bus, FakeHA(load_world("apartment")))
    ev = await played(
        ctx(door.send, bus=bus, reply_timeout_s=5),
        steps=[UNLOCK, CONFIRM],
        expect=[{"ha_not_called": {}}],
    )
    assert bus.decisions == [("real-id", "confirm")]
    assert [r.step for r in ev.replies] == [0]
    assert [t.text for t in ev.transcript if t.role == "event"][-1] == "door confirm → 503"
    assert [a.request_id for a in ev.pending[1].actions] == ["real-id"]
    assert checks(ev, {"pending_action_absent": {"at_step": 1, "tool": "home.lock_unlock"}}) == [
        "fail"
    ]


async def test_a_200_whose_followup_never_comes_records_no_reply() -> None:
    bus, ha = FakeBus(), FakeHA(load_world("apartment"))
    door = Door(bus, ha, answer=False)
    ev = await played(
        ctx(door.send, ha=ha, bus=bus, reply_timeout_s=0.2, ha_call_timeout_s=0.5),
        steps=[UNLOCK, CONFIRM],
        expect=[{"ha_not_called": {"at_step": 0}}],
    )
    await asyncio.gather(*door.tasks)
    assert [r.step for r in ev.replies] == [0]
    assert [t.role for t in ev.transcript][-1] == "event"
    assert checks(ev, {"reply_contains": {"regex": r"\bunlock(ed)?\b", "step": 1}}) == ["fail"]


async def test_a_door_step_waits_for_a_call_that_lags_the_followup() -> None:
    bus, ha = FakeBus(), FakeHA(load_world("apartment"))
    door = Door(bus, ha)
    effects: list[asyncio.Task[None]] = []

    async def lagging_call() -> None:
        await asyncio.sleep(0.1)
        ha.record(HaCall(t=time.monotonic(), domain="lock", service="unlock"))

    def decided(request_id: str, decision: str) -> None:
        bus.respond(followup(door.request_ids[0], "Done, sir."))
        effects.append(asyncio.create_task(lagging_call()))

    bus.on_decided = decided
    ev = await played(
        ctx(door.send, ha=ha, bus=bus, ha_call_timeout_s=2, reply_timeout_s=1),
        steps=[UNLOCK, CONFIRM],
        expect=[{"ha_called": {"domain": "lock", "service": "unlock", "at_step": 1, "count": 1}}],
    )
    await asyncio.gather(*effects)
    assert [(c.domain, c.service) for c in ev.ha_calls] == [("lock", "unlock")]
    assert checks(
        ev, {"ha_called": {"domain": "lock", "service": "unlock", "at_step": 1, "count": 1}}
    ) == ["pass"]


async def test_a_door_step_settles_for_its_own_time() -> None:
    started = time.monotonic()
    await played(
        ctx(Recorder(), bus=FakeBus(), reply_timeout_s=0.01),
        steps=[UNLOCK, {**CONFIRM, "settle": 0.3}],
        expect=[{"ha_not_called": {}}],
    )
    assert time.monotonic() - started >= 0.3
