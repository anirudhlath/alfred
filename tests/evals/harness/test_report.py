# tests/evals/harness/test_report.py
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from inspect_ai import Task, eval_async
from inspect_ai.dataset import Sample
from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, mean, scorer
from inspect_ai.solver import Generate, TaskState, solver

from evals.harness.checks.result import CheckResult
from evals.harness.report import (
    SCORER_NAME,
    RunMeta,
    SampleRun,
    log_problems,
    percentile,
    render_markdown,
    runs_from_logs,
    summarize,
    write_report,
)
from tests.evals.harness.factories import evidence, llm

if TYPE_CHECKING:
    from pathlib import Path

    from inspect_ai.log import EvalLog

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
EV = evidence(replies=["Done, sir."], llm_calls=[llm("system2")])


def run(
    sid: str,
    value: str,
    *,
    epoch: int = 1,
    status: str = "shipped",
    prd: list[str] | None = None,
    reason: str = "x",
    checks: list[CheckResult] | None = None,
    error: str | None = None,
) -> SampleRun:
    if checks is None:
        checks = (
            [] if value == "C" else [CheckResult(name="ha_called", status="fail", reason=reason)]
        )
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
        error=error,
        reply_ms=[1000.0 * epoch],
    )


def sample(sid: str, **metadata: Any) -> Sample:
    md = {
        "scenario_id": sid,
        "variant": 0,
        "suite": "home",
        "status": "shipped",
        "prd": ["4.4.lights-scenes"],
    }
    return Sample(id=sid, input="x", metadata=md | metadata)


@solver
def fake_stack():  # type: ignore[no-untyped-def]
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        if state.sample_id == "home.bad":
            raise RuntimeError("stack fell over")
        bogus = state.sample_id == "home.bad-evidence"
        state.store.set("evidence", {"bogus": 1} if bogus else EV.model_dump(mode="json"))
        return state

    return solve


@scorer(metrics=[mean()], name=SCORER_NAME)
def fake_scorer():  # type: ignore[no-untyped-def]
    async def score(state: TaskState, target: Target) -> Score:
        if state.sample_id == "home.partial":
            return Score(value="P")
        if state.sample_id == "home.bad-checks":
            return Score(value=CORRECT, metadata={"checks": [{"name": "judge"}]})
        ok = state.epoch == 1
        checks = [
            CheckResult(name="judge", status="pass" if ok else "fail", reason="r").model_dump()
        ]
        return Score(value=CORRECT if ok else INCORRECT, metadata={"checks": checks})

    return score


async def run_eval(tmp_path: Path, samples: list[Sample], epochs: int, **kw: Any) -> EvalLog:
    task = Task(dataset=samples, solver=fake_stack(), scorer=fake_scorer(), epochs=epochs)
    logs = await eval_async(task, model="mockllm/model", log_dir=str(tmp_path), **kw)
    return logs[0]


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


def test_failing_lists_failures_then_errors_then_judge_errors() -> None:
    judge_error = CheckResult(name="judge", status="error", reason="judge timed out")
    runs = [
        run("home.a", "N", epoch=1, checks=[judge_error]),
        run("home.a", "E", epoch=2, checks=[], error="boom"),
        run("home.a", "I", epoch=3, reason="wanted light.turn_on"),
        run("home.a", "I", epoch=4, reason="wanted light.turn_on"),
    ]
    (g,) = summarize(runs, META).goldens
    assert g.failing == ["ha_called: wanted light.turn_on", "error: boom", "judge: judge timed out"]


def test_failing_always_keeps_an_error_message() -> None:
    runs = [run("home.a", "I", epoch=i, reason=f"miss {i}") for i in range(1, 5)]
    runs.append(run("home.a", "E", epoch=5, checks=[], error="boom"))
    (g,) = summarize(runs, META).goldens
    assert g.failing == ["ha_called: miss 1", "ha_called: miss 2", "error: boom"]


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


def test_a_multiline_reason_keeps_its_table_row_on_one_line() -> None:
    md = render_markdown(summarize([run("home.a", "I", reason="a\n\n- b|c")], META))
    row = next(line for line in md.splitlines() if line.startswith("| home.a |"))
    assert row.endswith("| ha_called: a - b\\|c |")
    assert not any(line.startswith("- b") for line in md.splitlines())


def test_header_names_untrusted_and_uncalibrated_judge_categories() -> None:
    meta = META.model_copy(update={"calibration": {"tone": 0.9, "privacy": 0.7}})
    header = render_markdown(summarize([], meta)).splitlines()[2]
    assert "judge trusted: tone" in header
    assert "untrusted: privacy" in header
    assert "uncalibrated: answered, faithfulness, relevance" in header


def test_header_says_when_the_judge_has_no_calibration() -> None:
    meta = META.model_copy(update={"calibration": {}, "trusted": []})
    header = render_markdown(summarize([], meta)).splitlines()[2]
    assert "no judge calibration for m — every judge check is untrusted" in header
    assert "judge trusted" not in header


def test_run_problems_render_above_the_tables() -> None:
    assert "## Run problems" not in render_markdown(summarize([], META))
    meta = META.model_copy(update={"problems": ["home: error — boom"]})
    md = render_markdown(summarize([run("home.a", "C")], meta))
    assert "## Run problems\n\n- home: error — boom" in md
    assert md.index("## Run problems") < md.index("## PRD rows")


async def test_runs_from_logs_reads_inspect_logs(tmp_path: Path) -> None:
    log = await run_eval(tmp_path, [sample("home.a"), sample("home.bad")], 2, fail_on_error=False)
    runs = runs_from_logs([log])
    good = sorted((r for r in runs if r.sample_id == "home.a"), key=lambda r: r.epoch)
    assert [(r.epoch, r.value) for r in good] == [(1, "C"), (2, "I")]
    assert good[0].reply_ms == [1000.0] and good[0].llm[0].role == "system2"
    assert good[0].checks == [CheckResult(name="judge", status="pass", reason="r")]
    assert good[1].checks == [CheckResult(name="judge", status="fail", reason="r")]
    bad = [r for r in runs if r.sample_id == "home.bad"]
    assert len(bad) == 2
    assert all(r.value == "E" and "stack fell over" in (r.error or "") for r in bad)
    assert log_problems([log], epochs=2) == []


async def test_an_unexpected_score_or_status_errors_only_that_run(tmp_path: Path) -> None:
    samples = [sample("home.a"), sample("home.partial"), sample("home.beta", status="beta")]
    runs = {r.sample_id: r for r in runs_from_logs([await run_eval(tmp_path, samples, 1)])}
    assert runs["home.a"].value == "C"
    assert (runs["home.partial"].value, runs["home.partial"].error) == (
        "E",
        "unexpected score value P",
    )
    beta = runs["home.beta"]
    assert (beta.value, beta.error, beta.status) == ("E", "unknown status beta", "shipped")


async def test_log_problems_names_failed_logs_and_missing_samples(tmp_path: Path) -> None:
    log = await run_eval(tmp_path, [sample("home.a"), sample("home.bad")], 1)
    assert log.status == "error"
    (line,) = log_problems([log], epochs=1)
    assert line.startswith(f"{log.eval.task}: error — ") and "stack fell over" in line
    assert log.error is not None
    multiline = log.error.model_copy(update={"message": "Traceback:\n  stack\n\tfell over"})
    (flat,) = log_problems([log.model_copy(update={"error": multiline})], epochs=1)
    assert flat == f"{log.eval.task}: error — Traceback: stack fell over"

    trimmed = log.model_copy(update={"samples": [s for s in log.samples or [] if s.epoch != 1]})
    problems = log_problems([trimmed], epochs=1)
    assert f"{log.eval.task}: sample home.a epoch 1 is missing from the log" in problems
    assert f"{log.eval.task}: sample home.bad epoch 1 is missing from the log" in problems

    unknown = log.eval.dataset.model_copy(update={"sample_ids": None})
    blind = trimmed.model_copy(update={"eval": log.eval.model_copy(update={"dataset": unknown})})
    assert log_problems([blind], epochs=1) == [line]

    header_only = log.model_copy(update={"samples": None})
    assert log_problems([header_only], epochs=1) == [
        line,
        f"{log.eval.task}: samples not loaded — cannot check for missing runs",
    ]


async def test_malformed_run_data_never_loses_the_report(tmp_path: Path) -> None:
    samples = [
        sample("home.a"),
        sample("home.bad-checks"),
        sample("home.bad-evidence"),
        sample("home.bad-variant", variant="two"),
        sample("home.bad-prd", prd=[1, 2]),
    ]
    runs = {r.sample_id: r for r in runs_from_logs([await run_eval(tmp_path, samples, 1)])}
    assert (runs["home.a"].value, runs["home.a"].error) == ("C", None)

    checks = runs["home.bad-checks"]
    assert (checks.value, checks.checks) == ("C", [])
    assert checks.error == "unreadable checks: 2 validation errors for CheckResult"

    ev = runs["home.bad-evidence"]
    assert ev.value == "E" and ev.reply_ms == []
    assert (ev.error or "").startswith("unreadable evidence: ")

    variant = runs["home.bad-variant"]
    assert variant.value == "E"
    assert variant.error == "unreadable sample: invalid literal for int() with base 10: 'two'"

    prd = runs["home.bad-prd"]
    assert (prd.value, prd.prd, prd.status) == ("E", [], "shipped")
    assert (prd.error or "").startswith("unreadable sample: ")

    card = summarize(list(runs.values()), META)
    golden = next(g for g in card.goldens if g.scenario_id == "home.bad-checks")
    assert golden.pass_k
    assert golden.failing == ["error: unreadable checks: 2 validation errors for CheckResult"]
    assert "unreadable checks" in render_markdown(card)
