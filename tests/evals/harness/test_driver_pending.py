from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import pytest

from evals.harness.bus import BusError
from evals.harness.checks import run_check
from evals.harness.driver import HarnessError, play
from evals.harness.evidence import HaCall
from evals.harness.fake_ha import FakeHA
from evals.harness.scenario import expand_variants
from evals.harness.world import load_world
from tests.evals.harness.factories import FakeBus
from tests.evals.harness.test_checks_pending import pending_action
from tests.evals.harness.test_driver import Recorder, ctx, played, scenario

if TYPE_CHECKING:
    from bus.schemas.events import AlfredResponse, UserRequest

CONFIRM = {
    "user": "Confirm action {pending_action_id}.",
    "pending_action": {"at_step": 0, "tool": "home_lock_unlock"},
}
LOCK_CALL = {"ha_called": {"domain": "lock", "service": "unlock", "at_step": 1, "count": 1}}


async def test_pending_snapshot_selects_real_id_and_waits_for_execution_after_consent() -> None:
    bus, ha = FakeBus(), FakeHA(load_world("apartment"))
    bus.pending = [pending_action("keep")]
    sent: list[str] = []
    effects: list[asyncio.Task[None]] = []

    async def execute() -> None:
        await asyncio.sleep(0.05)
        ha.record(HaCall(t=time.monotonic(), domain="lock", service="unlock"))
        await ha.set_state("lock.front_door", "unlocked")

    async def send(request: UserRequest, timeout: float) -> AlfredResponse:
        sent.append(request.content)
        if len(sent) == 1:
            bus.pending.append(pending_action("real-id"))
        elif len(sent) == 2:
            assert request.content == "Confirm action real-id."
            bus.pending = [a for a in bus.pending if a.request_id != "real-id"]
            effects.append(asyncio.create_task(execute()))
        return await Recorder()(request, timeout)

    ev = await played(
        ctx(send, ha=ha, bus=bus, ha_call_timeout_s=0.5),
        steps=[{"user": "Unlock the door."}, CONFIRM, CONFIRM],
        expect=[
            {"pending_action": {"at_step": 0, "tool": "home.lock_unlock"}},
            {"ha_not_called": {"at_step": 0}},
            LOCK_CALL,
            {"ha_not_called": {"at_step": 2}},
        ],
    )
    await asyncio.gather(*effects)
    assert [[a.request_id for a in s.actions] for s in ev.pending] == [["real-id"], [], []]
    assert sent[2] == sent[1]  # repeating consent names the consumed id, never another one
    assert [a.request_id for a in bus.pending] == ["keep"]
    assert ev.ha_states["lock.front_door"].state == "unlocked"
    assert all(
        run_check(c.name, c.params, ev).status == "pass"
        for c in scenario(
            steps=[{"user": "a"}, CONFIRM, CONFIRM],
            expect=[
                {"ha_not_called": {"at_step": 0}},
                LOCK_CALL,
                {"ha_not_called": {"at_step": 2}},
            ],
        ).expect
    )


@pytest.mark.parametrize("count", [0, 2])
async def test_pending_fixture_miss_sends_fixed_nonexistent_id_and_scores_product_failure(
    count: int,
) -> None:
    from evals.harness.driver import MISSING_PENDING_ACTION_ID

    bus = FakeBus()
    sent: list[str] = []

    async def send(request: UserRequest, timeout: float) -> AlfredResponse:
        sent.append(request.content)
        if len(sent) == 1:
            bus.pending = [pending_action(str(i)) for i in range(count)]
        return await Recorder()(request, timeout)

    s = scenario(
        steps=[{"user": "Unlock it."}, CONFIRM],
        expect=[{"pending_action": {"at_step": 0, "tool": "home.lock_unlock"}}, LOCK_CALL],
    )
    ev = await play(ctx(send, bus=bus, ha_call_timeout_s=0), expand_variants(s)[0], epoch=1)
    assert sent[1] == f"Confirm action {MISSING_PENDING_ACTION_ID}."
    assert all(run_check(c.name, c.params, ev).status == "fail" for c in s.expect)
    assert bus.pending == []


@pytest.mark.parametrize("cancelled", [False, True])
async def test_failure_or_cancellation_removes_unsnapshotted_pending_and_preserves_unrelated(
    cancelled: bool,
) -> None:
    bus = FakeBus()
    bus.pending = [pending_action("keep")]
    entered = asyncio.Event()

    async def fail(request: UserRequest, timeout: float) -> AlfredResponse:
        bus.pending.append(pending_action("owned"))
        entered.set()
        if cancelled:
            await asyncio.Event().wait()
        raise HarnessError("original failure")

    task = asyncio.create_task(played(ctx(fail, bus=bus), steps=[{"user": "Unlock it."}]))
    await entered.wait()
    if cancelled:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancelled else HarnessError):
        await task
    assert [a.request_id for a in bus.pending] == ["keep"]


async def test_repeated_cancellation_waits_for_pending_cleanup_to_finish() -> None:
    cleaning, release = asyncio.Event(), asyncio.Event()

    class SlowCleanup(FakeBus):
        async def delete_pending_actions(self, ids: list[str]) -> None:
            cleaning.set()
            await release.wait()
            await super().delete_pending_actions(ids)

    bus = SlowCleanup()

    async def send(request: UserRequest, timeout: float) -> AlfredResponse:
        bus.pending.append(pending_action("owned"))
        return await Recorder()(request, timeout)

    task = asyncio.create_task(played(ctx(send, bus=bus), steps=[{"user": "Unlock it."}]))
    await asyncio.wait_for(cleaning.wait(), timeout=1)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and bus.pending
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert bus.pending == []


@pytest.mark.parametrize("original_failure", [False, True])
async def test_pending_cleanup_errors_are_visible_without_replacing_original_failure(
    original_failure: bool,
) -> None:
    class BrokenCleanup(FakeBus):
        async def delete_pending_actions(self, ids: list[str]) -> None:
            raise BusError("cleanup failed")

    bus = BrokenCleanup()

    async def send(request: UserRequest, timeout: float) -> AlfredResponse:
        bus.pending.append(pending_action("owned"))
        if original_failure:
            raise HarnessError("original failure")
        return await Recorder()(request, timeout)

    wanted: type[Exception] = HarnessError if original_failure else BusError
    with pytest.raises(wanted) as err:
        await played(ctx(send, bus=bus), steps=[{"user": "Unlock it."}])
    if original_failure:
        assert str(err.value) == "original failure"
        assert "cleanup failed" in " ".join(err.value.__notes__)


async def test_pending_snapshots_cover_non_user_steps_and_do_not_leak_between_samples() -> None:
    bus = FakeBus()

    async def send(request: UserRequest, timeout: float) -> AlfredResponse:
        bus.pending.append(pending_action("owned"))
        return await Recorder()(request, timeout)

    ev = await played(ctx(send, bus=bus), steps=[{"user": "Unlock it."}, {"wait": 0.001}])
    assert [s.step for s in ev.pending] == [0, 1]
    assert all(s.actions[0].request_id == "owned" for s in ev.pending)
    second = await played(ctx(Recorder(), bus=bus), steps=[{"user": "Hi."}])
    assert second.pending[0].actions == []


async def test_confirmation_wait_observes_the_requested_count_of_delayed_calls() -> None:
    bus, ha = FakeBus(), FakeHA(load_world("apartment"))
    effects: list[asyncio.Task[None]] = []
    sent = 0

    async def execute_twice() -> None:
        for _ in range(2):
            await asyncio.sleep(0.03)
            ha.record(HaCall(t=time.monotonic(), domain="lock", service="unlock"))

    async def send(request: UserRequest, timeout: float) -> AlfredResponse:
        nonlocal sent
        sent += 1
        if sent == 1:
            bus.pending.append(pending_action("real-id"))
        else:
            effects.append(asyncio.create_task(execute_twice()))
        return await Recorder()(request, timeout)

    ev = await played(
        ctx(send, ha=ha, bus=bus, ha_call_timeout_s=0.5),
        steps=[{"user": "Unlock it."}, CONFIRM],
        expect=[{"ha_called": {"domain": "lock", "service": "unlock", "at_step": 1, "count": 2}}],
    )
    await asyncio.gather(*effects)
    assert len(ev.ha_calls) == 2
