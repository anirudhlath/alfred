from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import pytest
from inspect_ai import eval_async
from inspect_ai.model import ModelOutput, get_model
from inspect_ai.util import display_type
from inspect_ai.util._display import display_type_initialized
from redis.exceptions import ConnectionError as RedisConnectionError

from bus.schemas.events import AlfredResponse, UserRequest
from evals.harness.bus import BusError
from evals.harness.checks.result import CheckResult
from evals.harness.driver import PlayContext
from evals.harness.fake_ha import FakeHA
from evals.harness.judge import Judge
from evals.harness.proxy import LlmProxy
from evals.harness.report import runs_from_logs
from evals.harness.scenario import Scenario, expand_variants
from evals.harness.stack import StackError
from evals.harness.tasks import RunContext, build_task, verdict
from evals.harness.world import load_world
from tests.evals.harness.factories import FakeBus

if TYPE_CHECKING:
    from pathlib import Path

    from evals.harness.bus import Entry
    from evals.harness.driver import SendFn
    from evals.harness.report import SampleRun


class FakeStack:
    def __init__(self, alive: list[bool], restart_error: StackError | None = None) -> None:
        self._alive = alive
        self._restart_error = restart_error
        self.name = "alfred-eval-test"
        self.restarts = 0
        self.alive_calls = 0

    async def alive(self) -> bool:
        self.alive_calls += 1
        return self._alive.pop(0) if len(self._alive) > 1 else self._alive[0]

    async def restart(self) -> None:
        self.restarts += 1
        if self._restart_error is not None:
            raise self._restart_error


async def polite(request: UserRequest, timeout: float) -> AlfredResponse:
    return AlfredResponse(
        source="conscious-engine",
        channel=request.channel,
        session_id=request.session_id,
        text="Good evening, sir.",
    )


def scenario(**fields: Any) -> Scenario:
    return Scenario.model_validate(
        {
            **fields,
            "id": "conversation.persona.greeting",
            "prd": ["1.butler"],
            "status": "shipped",
            "suite": "conversation",
            "steps": [{"user": "Good evening, Alfred.", "variants": ["Hello Alfred."]}],
            "expect": [
                {"reply_contains": {"text": "sir"}},
                {
                    "judge": {
                        "category": "tone",
                        "rubric": "Does the reply keep a formal butler register?",
                    }
                },
            ],
        }
    )


def context(
    stack: FakeStack,
    judge_says: str = "Formal.\nVERDICT: yes",
    send: SendFn = polite,
    bus: FakeBus | None = None,
    **scenario_fields: Any,
) -> RunContext:
    variants = expand_variants(scenario(**scenario_fields))
    outputs = [ModelOutput.from_content(model="mockllm/model", content=judge_says)] * 10
    judge = Judge(get_model("mockllm/model", custom_outputs=outputs, memoize=False))
    play_ctx = PlayContext(
        send=send,
        fake_ha=FakeHA(load_world("apartment")),
        proxy=LlmProxy("http://x"),
        bus=bus or FakeBus(),
        settle_s=0,
        restore_settle_s=0,
    )
    return RunContext(
        stack=stack,
        judge=judge,
        trusted={"tone"},
        variants={v.sample_id: v for v in variants},
        play_ctx=play_ctx,
    )


def test_verdict_rules() -> None:
    ok = CheckResult(name="a", status="pass", reason="")
    bad = CheckResult(name="b", status="fail", reason="")
    err = CheckResult(name="c", status="error", reason="")
    untrusted_bad = CheckResult(name="d", status="fail", reason="", counted=False)
    assert verdict([ok]) == "C"
    assert verdict([ok, bad, err]) == "I"
    assert verdict([ok, err]) == "N"
    assert verdict([ok, untrusted_bad]) == "C"
    assert verdict([untrusted_bad]) == "N"


async def test_a_suite_runs_end_to_end_in_process(tmp_path: Path) -> None:
    ctx = context(FakeStack([True]))
    task = build_task("conversation", list(ctx.variants.values()), ctx, epochs=2)
    logs = await eval_async(task, model="mockllm/model", log_dir=str(tmp_path), max_samples=1)
    runs = runs_from_logs(logs)
    assert len(runs) == 4 and {r.value for r in runs} == {"C"}
    assert {r.sample_id for r in runs} == {
        "conversation.persona.greeting",
        "conversation.persona.greeting~1",
    }


async def test_dead_container_restarts_once_then_errors_fast(tmp_path: Path) -> None:
    stack = FakeStack([False])
    ctx = context(stack)
    task = build_task("conversation", list(ctx.variants.values()), ctx, epochs=1)
    logs = await eval_async(
        task,
        model="mockllm/model",
        log_dir=str(tmp_path),
        max_samples=1,
        retry_on_error=1,
        fail_on_error=False,
    )
    runs = runs_from_logs(logs)
    assert stack.restarts == 1 and ctx.recoveries == 1
    assert [r.value for r in runs].count("E") >= 1
    assert all("is not running" in (r.error or "") for r in runs if r.value == "E")


async def test_restarts_for_isolated_goldens_are_not_recoveries(tmp_path: Path) -> None:
    stack = FakeStack([True])
    ctx = context(stack, isolated=True)
    task = build_task("conversation", list(ctx.variants.values()), ctx, epochs=1)
    logs = await eval_async(task, model="mockllm/model", log_dir=str(tmp_path), max_samples=1)
    assert {r.value for r in runs_from_logs(logs)} == {"C"}
    assert stack.restarts == 2 and ctx.recoveries == 0


async def lost_redis(request: UserRequest, timeout: float) -> AlfredResponse:
    raise StackError("alfred-eval-test: lost redis sending a request")


@pytest.mark.parametrize(
    ("stack", "send", "isolated"),
    [
        (FakeStack([True]), lost_redis, False),
        (
            FakeStack([True], StackError("alfred-eval-test: lost redis sending a request")),
            polite,
            True,
        ),
    ],
    ids=["send-raises", "restart-raises"],
)
async def test_a_stack_error_mid_eval_scores_the_sample_e(
    tmp_path: Path, stack: FakeStack, send: SendFn, isolated: bool
) -> None:
    ctx = context(stack, send=send, isolated=isolated)
    task = build_task("conversation", list(ctx.variants.values()), ctx, epochs=1)
    logs = await eval_async(
        task,
        model="mockllm/model",
        log_dir=str(tmp_path),
        max_samples=1,
        retry_on_error=1,
        fail_on_error=False,
    )
    runs = runs_from_logs(logs)
    assert len(runs) == 2 and {r.value for r in runs} == {"E"}
    assert all("lost redis sending a request" in (r.error or "") for r in runs)


def answering(sources: list[str]) -> SendFn:
    """A send that answers with each of *sources* in turn, then from System 2. Any other
    source makes play() raise HarnessError, as a reply timeout does."""
    queue = list(sources)

    async def send(request: UserRequest, timeout: float) -> AlfredResponse:
        source = queue.pop(0) if queue else "conscious-engine"
        return AlfredResponse(
            source=source,
            channel=request.channel,
            session_id=request.session_id,
            text="Good evening, sir.",
        )

    return send


async def evaluate(ctx: RunContext, tmp_path: Path) -> list[SampleRun]:
    task = build_task("conversation", list(ctx.variants.values()), ctx, epochs=1)
    logs = await eval_async(
        task,
        model="mockllm/model",
        log_dir=str(tmp_path),
        max_samples=1,
        retry_on_error=1,
        fail_on_error=False,
    )
    return runs_from_logs(logs)


async def test_a_failed_play_leaves_the_stack_dirty_and_the_retry_runs_on_a_restart(
    tmp_path: Path,
) -> None:
    # The first request times out: Conscious is still working on it, so the stack is
    # dirty and the retry must not share it. The restart is the suite's recovery.
    stack = FakeStack([True])
    ctx = context(stack, send=answering(["channels"]))
    runs = await evaluate(ctx, tmp_path)
    assert {r.value for r in runs} == {"C"} and len(runs) == 2
    assert stack.restarts == 1 and ctx.recoveries == 1 and ctx.dirty is None


async def test_a_dirty_stack_with_no_recovery_left_errors_at_once(tmp_path: Path) -> None:
    stack = FakeStack([True])
    ctx = context(stack, send=answering(["channels"]))
    ctx.restarts_left = 0
    runs = await evaluate(ctx, tmp_path)
    assert [r.value for r in runs] == ["E", "E"] and stack.restarts == 0
    # The later sample says why the stack needed the restart it could not have.
    later = next(r for r in runs if r.sample_id.endswith("~1"))
    assert "needs a restart" in (later.error or "")
    assert "no reply from System 2" in (later.error or "")
    assert "recovery is spent" in (later.error or "")


async def test_a_failed_restart_breaks_the_stack_and_later_samples_error_at_once(
    tmp_path: Path,
) -> None:
    # The restart's readiness fails, but the container keeps running: alive() would say
    # True for ever, and every later sample would wait out its reply timeout.
    broken = StackError(
        "alfred-eval-test: System 2 never answered the readiness request\n"
        "--- docker logs ---\nTraceback: conscious crashed"
    )
    stack = FakeStack([True], restart_error=broken)
    ctx = context(stack, send=answering(["channels"]))
    runs = await evaluate(ctx, tmp_path)
    assert [r.value for r in runs] == ["E", "E"]
    # One restart was tried; after it failed nothing probed or restarted the stack again.
    assert stack.restarts == 1 and stack.alive_calls == 1
    for r in runs:
        assert "failed to restart" in (r.error or "")
        assert "System 2 never answered the readiness request" in (r.error or "")
        assert "docker logs" not in (r.error or "")  # the first line only; the rest is logged


class BrokenBus(FakeBus):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def user_timezone(self) -> str | None:
        raise self.error


# A bus failure, and how the dirty mark and the scorecard name it. redis-py's repr drops
# the message, so a redis error is named as the harness losing redis.
BUS_FAILURES = pytest.mark.parametrize(
    ("error", "named"),
    [
        (
            RedisConnectionError("Connection closed by server."),
            "lost redis: ConnectionError: Connection closed by server.",
        ),
        (BusError("POST /api/admin/dnd answered 401"), "POST /api/admin/dnd answered 401"),
        # One the harness does not name, such as a bus entry that is not JSON.
        (ValueError("Expecting value: line 1 column 1"), "Expecting value: line 1 column 1"),
    ],
    ids=["redis", "bus", "unnamed"],
)


@BUS_FAILURES
async def test_a_bus_failure_errors_the_sample_and_dirties_the_stack(
    tmp_path: Path, error: Exception, named: str
) -> None:
    stack = FakeStack([True])
    ctx = context(stack, bus=BrokenBus(error))
    ctx.restarts_left = 0
    runs = await evaluate(ctx, tmp_path)
    assert [r.value for r in runs] == ["E", "E"] and stack.restarts == 0
    assert ctx.dirty == named
    # Only a dirty stack makes the next sample ask for the restart it cannot have.
    later = next(r for r in runs if r.sample_id.endswith("~1"))
    assert f"needs a restart (an earlier sample failed mid-play: {named})" in (later.error or "")


@BUS_FAILURES
async def test_a_bus_failure_that_ends_a_sample_is_its_reported_error(
    tmp_path: Path, error: Exception, named: str
) -> None:
    # One sample. Its first try dirties the stack; the retry runs on the restart and fails
    # the same way, so the bus failure itself is the error the scorecard shows.
    stack = FakeStack([True])
    ctx = context(stack, bus=BrokenBus(error))
    ctx.variants = {k: v for k, v in ctx.variants.items() if not k.endswith("~1")}
    (run,) = await evaluate(ctx, tmp_path)
    assert run.value == "E" and stack.restarts == 1
    assert named in (run.error or "") and "needs a restart" not in (run.error or "")


Stage = Literal["collection", "cleanup"]


class BusLostAt(FakeBus):
    """Loses redis late in play(): as it collects the bus's evidence, or as it cleans up,
    once it has deleted the sample's triggers."""

    def __init__(self, stage: Stage) -> None:
        super().__init__()
        self.stage = stage

    async def notifications(self, since_wall: float) -> list[Entry]:
        if self.stage == "collection":
            raise RedisConnectionError("Connection closed by server.")
        return await super().notifications(since_wall)

    async def delete_triggers(self, trigger_ids: list[str]) -> None:
        await super().delete_triggers(trigger_ids)
        if self.stage == "cleanup":
            raise RedisConnectionError("Connection closed by server.")


def creating_a_trigger(bus: FakeBus) -> SendFn:
    """A polite send whose turn also creates a trigger, which cleanup then deletes."""

    async def send(request: UserRequest, timeout: float) -> AlfredResponse:
        bus.created()
        return await polite(request, timeout)

    return send


@pytest.mark.parametrize("stage", ["collection", "cleanup"])
async def test_a_bus_failure_late_in_play_dirties_the_stack(tmp_path: Path, stage: Stage) -> None:
    stack = FakeStack([True])
    bus = BusLostAt(stage)
    ctx = context(stack, send=creating_a_trigger(bus), bus=bus)
    ctx.restarts_left = 0
    runs = await evaluate(ctx, tmp_path)
    assert [r.value for r in runs] == ["E", "E"] and stack.restarts == 0
    # Cleanup ran only where collection got through: a failed collection skips it.
    assert bus.deleted == (["t1"] if stage == "cleanup" else [])
    assert ctx.dirty == "lost redis: ConnectionError: Connection closed by server."
    later = next(r for r in runs if r.sample_id.endswith("~1"))
    assert "needs a restart" in (later.error or "")


def test_the_test_package_pins_inspect_display_none() -> None:
    # eval_async pins "plain" unless a display was set first; the package sets "none".
    assert display_type_initialized() and display_type() == "none"
