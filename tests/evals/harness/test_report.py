# tests/evals/harness/test_report.py
from __future__ import annotations

from typing import TYPE_CHECKING

from inspect_ai import Task, eval_async
from inspect_ai.dataset import Sample
from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, mean, scorer
from inspect_ai.solver import Generate, TaskState, solver

from evals.harness.checks.result import CheckResult
from evals.harness.report import (
    RunMeta,
    SampleRun,
    percentile,
    render_markdown,
    runs_from_logs,
    summarize,
    write_report,
)
from tests.evals.harness.factories import evidence, llm

if TYPE_CHECKING:
    from pathlib import Path

META = RunMeta(
    run_dir="/tmp/run",
    model="m",
    alfred_commit="abc1234",
    home_service_commit="def5678",
    epochs=3,
    calibration={"tone": 0.9},
    trusted=["tone"],
    stacks=[],
)


def run(
    sid: str,
    value: str,
    *,
    epoch: int = 1,
    status: str = "shipped",
    prd: list[str] | None = None,
    reason: str = "x",
) -> SampleRun:
    checks = [] if value == "C" else [CheckResult(name="ha_called", status="fail", reason=reason)]
    variant = int(sid.split("~")[1]) if "~" in sid else 0
    return SampleRun(
        sample_id=sid,
        scenario_id=sid.split("~")[0],
        variant=variant,
        epoch=epoch,
        suite=sid.split(".")[0],
        status=status,
        prd=prd or ["4.4.lights-scenes"],
        value=value,
        checks=checks,
        reply_ms=[1000.0 * epoch],
    )


def test_percentile_is_nearest_rank() -> None:
    assert percentile([], 0.5) is None
    assert percentile([1.0, 2.0], 0.5) == 1.0
    assert percentile([1.0, 2.0, 3.0], 0.5) == 2.0
    assert percentile([float(i) for i in range(1, 21)], 0.95) == 19.0


def test_golden_summary_rates() -> None:
    runs = [
        run("home.a", "C", epoch=1),
        run("home.a", "I", epoch=2),
        run("home.a", "C", epoch=3),
        run("home.a~1", "E"),
        run("home.b", "C"),
        run("home.c", "N"),
    ]
    card = summarize(runs, META)
    a = next(g for g in card.goldens if g.scenario_id == "home.a")
    assert (a.runs, a.passes, a.errors, a.variants) == (4, 2, 1, 2)
    assert abs((a.pass_rate or 0) - 2 / 3) < 1e-9 and a.flaky and not a.pass_k
    b = next(g for g in card.goldens if g.scenario_id == "home.b")
    assert b.pass_k and not b.flaky
    c = next(g for g in card.goldens if g.scenario_id == "home.c")
    assert c.pass_rate is None and c.inconclusive == 1


def test_pending_goldens_stay_out_of_prd_rows() -> None:
    card = summarize(
        [run("home.a", "C"), run("home.p", "I", status="pending", prd=["4.4.device-discovery"])],
        META,
    )
    assert [r.prd_id for r in card.prd_rows] == ["4.4.lights-scenes"]
    md = render_markdown(card)
    assert "Not yet working" in md and "home.p" in md


def test_markdown_names_the_failing_check(tmp_path: Path) -> None:
    card = summarize([run("home.a", "I", reason="wanted light.turn_on")], META)
    md_path, json_path = write_report(card, tmp_path)
    assert "wanted light.turn_on" in md_path.read_text() and json_path.exists()


async def test_runs_from_logs_reads_inspect_logs(tmp_path: Path) -> None:
    ev = evidence(replies=["Done, sir."], llm_calls=[llm("system2")])

    @solver
    def fake():  # type: ignore[no-untyped-def]
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            state.store.set("evidence", ev.model_dump(mode="json"))
            return state

        return solve

    @scorer(metrics=[mean()], name="scenario_scorer")
    def fake_scorer():  # type: ignore[no-untyped-def]
        async def score(state: TaskState, target: Target) -> Score:
            ok = state.epoch == 1
            checks = [
                CheckResult(name="judge", status="pass" if ok else "fail", reason="r").model_dump()
            ]
            return Score(value=CORRECT if ok else INCORRECT, metadata={"checks": checks})

        return score

    task = Task(
        dataset=[
            Sample(
                id="home.a",
                input="x",
                metadata={
                    "scenario_id": "home.a",
                    "variant": 0,
                    "suite": "home",
                    "status": "shipped",
                    "prd": ["4.4.lights-scenes"],
                },
            )
        ],
        solver=fake(),
        scorer=fake_scorer(),
        epochs=2,
    )
    logs = await eval_async(task, model="mockllm/model", log_dir=str(tmp_path))
    runs = runs_from_logs(logs)
    assert sorted((r.epoch, r.value) for r in runs) == [(1, "C"), (2, "I")]
    assert runs[0].reply_ms == [1000.0] and runs[0].llm[0].role == "system2"
