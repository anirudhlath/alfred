from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from inspect_ai import eval_async
from inspect_ai.model import ModelOutput, get_model
from inspect_ai.util import display_type
from inspect_ai.util._display import display_type_initialized

from bus.schemas.events import AlfredResponse, UserRequest
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

if TYPE_CHECKING:
    from pathlib import Path

    from evals.harness.driver import SendFn


class FakeStack:
    def __init__(self, alive: list[bool], restart_error: StackError | None = None) -> None:
        self._alive = alive
        self._restart_error = restart_error
        self.name = "alfred-eval-test"
        self.restarts = 0

    async def alive(self) -> bool:
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
    **scenario_fields: Any,
) -> RunContext:
    variants = expand_variants(scenario(**scenario_fields))
    outputs = [ModelOutput.from_content(model="mockllm/model", content=judge_says)] * 10
    judge = Judge(get_model("mockllm/model", custom_outputs=outputs, memoize=False))
    play_ctx = PlayContext(
        send=send,
        fake_ha=FakeHA(load_world("apartment")),
        proxy=LlmProxy("http://x"),
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
    assert all("not running" in (r.error or "") for r in runs if r.value == "E")


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


def test_the_test_package_pins_inspect_display_none() -> None:
    # eval_async pins "plain" unless a display was set first; the package sets "none".
    assert display_type_initialized() and display_type() == "none"
