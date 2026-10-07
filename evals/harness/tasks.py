"""Inspect wiring: one Task per suite; solver = driver, scorer = checks + judge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from inspect_ai import Epochs, Task
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessageAssistant, ChatMessageUser, ModelOutput
from inspect_ai.scorer import CORRECT, INCORRECT, NOANSWER, Score, mean, scorer
from inspect_ai.solver import solver

from evals.harness.checks import run_check
from evals.harness.checks.judge_spec import JudgeSpec
from evals.harness.driver import HarnessError, play
from evals.harness.evidence import Evidence
from evals.harness.judge import judge_check
from evals.harness.report import SCORER_NAME
from evals.harness.scenario import UserStep
from evals.harness.stack import StackError

if TYPE_CHECKING:
    from inspect_ai.scorer import Scorer, Target
    from inspect_ai.solver import Generate, Solver, TaskState

    from evals.harness.checks.result import CheckResult
    from evals.harness.driver import PlayContext
    from evals.harness.judge import Judge
    from evals.harness.scenario import Scenario, ScenarioVariant


# Recovery restarts a suite gets (of a dead container, or of a stack a failed sample left
# dirty) before every later sample that needs one errors at once.
RECOVERIES_PER_SUITE = 1


def _first_line(exc: BaseException) -> str:
    lines = str(exc).strip().splitlines()
    return lines[0] if lines else type(exc).__name__


@dataclass
class RunContext:
    stack: Any  # evals.harness.stack.Stack; Any so tests can pass a fake
    judge: Judge
    trusted: set[str]
    variants: dict[str, ScenarioVariant]
    play_ctx: PlayContext
    restarts_left: int = RECOVERIES_PER_SUITE
    # Why the stack is dirty: a sample failed mid-play. A request that timed out is still
    # running inside Conscious, and one whose LLM call failed waits in its pending list to
    # be replayed; either would land in a later sample's evidence. A restart clears both.
    dirty: str | None = None
    # Why the stack is past saving: a restart failed. Every later sample errors at once.
    broken: str | None = None

    @property
    def recoveries(self) -> int:
        """Recovery restarts: of a dead container, or of a dirty stack. An isolated
        golden's restart is not one."""
        return RECOVERIES_PER_SUITE - self.restarts_left


def to_sample(variant: ScenarioVariant) -> Sample:
    s = variant.scenario
    first = next((st.user for st in variant.steps if isinstance(st, UserStep)), "(event-driven)")
    return Sample(
        id=variant.sample_id,
        input=first,
        metadata={
            "scenario_id": s.id,
            "variant": variant.variant,
            "suite": s.suite,
            "status": s.status,
            "prd": s.prd,
            "tags": s.tags,
            "isolated": s.isolated,
            "path": s.path,
        },
    )


def verdict(results: list[CheckResult]) -> str:
    counted = [r for r in results if r.counted]
    if any(r.status == "fail" for r in counted):
        return INCORRECT
    if not counted or any(r.status == "error" for r in counted):
        return NOANSWER
    return CORRECT


async def score_evidence(
    evidence: Evidence, scenario: Scenario, judge: Judge, trusted: set[str]
) -> tuple[str, list[CheckResult]]:
    results: list[CheckResult] = []
    for check in scenario.expect:
        if check.name == "judge":
            assert isinstance(check.params, JudgeSpec)
            results.append(await judge_check(judge, evidence, check.params, trusted))
        else:
            results.append(run_check(check.name, check.params, evidence))
    return verdict(results), results


async def _restart(ctx: RunContext) -> None:
    """Restart the stack, which leaves it clean. A failed restart leaves it broken."""
    try:
        await ctx.stack.restart()
    except StackError as exc:
        # The container may well be running but unready, so alive() would keep saying
        # True and every later sample would wait out its reply timeout. Stop here.
        ctx.broken = (
            f"eval container {ctx.stack.name} failed to restart ({_first_line(exc)}); "
            "every later sample in this suite errors"
        )
        raise HarnessError(ctx.broken) from exc
    ctx.dirty = None


@solver
def reset_or_recover(ctx: RunContext) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        if ctx.broken is not None:
            raise HarnessError(ctx.broken)
        variant = ctx.variants[str(state.sample_id)]
        if variant.scenario.isolated:
            await _restart(ctx)
            return state
        if ctx.dirty is not None:
            why = f"an earlier sample failed mid-play: {ctx.dirty}"
        elif not await ctx.stack.alive():
            why = "it is not running"
        else:
            return state
        if ctx.restarts_left <= 0:
            raise HarnessError(
                f"eval container {ctx.stack.name} needs a restart ({why}), but this suite's "
                f"recovery is spent (see `docker logs {ctx.stack.name}`)"
            )
        ctx.restarts_left -= 1
        await _restart(ctx)
        return state

    return solve


@solver
def play_scenario(ctx: RunContext) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        variant = ctx.variants[str(state.sample_id)]
        try:
            evidence = await play(ctx.play_ctx, variant, state.epoch)
        except (HarnessError, StackError) as exc:
            ctx.dirty = _first_line(exc)
            raise
        state.store.set("evidence", evidence.model_dump(mode="json"))
        state.messages = [
            ChatMessageAssistant(content=t.text)
            if t.role == "alfred"
            else ChatMessageUser(content=t.text if t.role == "user" else f"[home event] {t.text}")
            for t in evidence.transcript
        ]
        last = evidence.replies[-1].text if evidence.replies else ""
        state.output = ModelOutput.from_content(model="alfred", content=last)
        return state

    return solve


@scorer(metrics=[mean()], name=SCORER_NAME)
def scenario_scorer(ctx: RunContext) -> Scorer:
    async def score(state: TaskState, target: Target) -> Score:
        variant = ctx.variants[str(state.sample_id)]
        evidence = Evidence.model_validate(state.store.get("evidence"))
        value, results = await score_evidence(evidence, variant.scenario, ctx.judge, ctx.trusted)
        return Score(
            value=value,
            explanation="\n".join(
                f"{r.status.upper()}{'' if r.counted else '*'} {r.name}: {r.reason}"
                for r in results
            ),
            metadata={"checks": [r.model_dump() for r in results]},
        )

    return score


def build_task(suite: str, variants: list[ScenarioVariant], ctx: RunContext, epochs: int) -> Task:
    return Task(
        name=suite,
        dataset=[to_sample(v) for v in variants],
        setup=reset_or_recover(ctx),
        solver=play_scenario(ctx),
        scorer=scenario_scorer(ctx),
        epochs=Epochs(epochs, "mean"),
        metadata={"suite": suite},
    )
