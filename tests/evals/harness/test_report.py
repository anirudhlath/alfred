# tests/evals/harness/test_report.py
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import pytest
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
CheckStatus = Literal["pass", "fail", "error"]


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
            bad = {"name": "judge", "status": "meh", "reason": "r"}
            return Score(value=CORRECT, metadata={"checks": [bad]})
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
    assert abs((a.pass_rate or 0) - 2 / 3) < 1e-9 and a.flaky and a.pass_k is False
    base, other = a.samples
    assert (base.sample_id, base.runs, base.passes, base.flaky, base.pass_k) == (
        "home.a",
        3,
        2,
        True,
        False,
    )
    # An error is not a failure: the variant's pass^k is unknown, and it is not flaky.
    assert (other.sample_id, other.pass_rate, other.pass_k, other.flaky) == (
        "home.a~1",
        None,
        None,
        False,
    )
    b = next(g for g in card.goldens if g.scenario_id == "home.b")
    assert b.pass_k is True and not b.flaky and b.variants_pass_k == 1
    c = next(g for g in card.goldens if g.scenario_id == "home.c")
    assert c.pass_rate is None and c.inconclusive == 1 and c.pass_k is None


def test_flaky_and_pass_k_are_per_sample_so_a_phrasing_failure_is_not_noise() -> None:
    # Variant 0 passes every epoch and variant ~1 fails every epoch: a deterministic
    # phrasing failure. Neither sample is flaky, so the golden is not either.
    runs = [run("home.a", "C", epoch=e) for e in (1, 2, 3)]
    runs += [run("home.a~1", "I", epoch=e, reason="wanted light.turn_on") for e in (1, 2, 3)]
    (g,) = summarize(runs, META).goldens
    assert not g.flaky and [s.flaky for s in g.samples] == [False, False]
    assert [s.pass_k for s in g.samples] == [True, False]
    assert (g.variants_pass_k, g.variants, g.pass_k) == (1, 2, False)
    # Each failing entry says which sample failed.
    assert g.failing == ["home.a~1: ha_called: wanted light.turn_on"]


def test_one_flaky_sample_makes_its_golden_flaky() -> None:
    runs = [run("home.a", "C", epoch=e) for e in (1, 2)]
    runs += [run("home.a~1", "C", epoch=1), run("home.a~1", "I", epoch=2)]
    (g,) = summarize(runs, META).goldens
    assert g.flaky and [s.flaky for s in g.samples] == [False, True]


def test_pass_k_counts_scored_runs_and_is_unknown_when_a_run_errored() -> None:
    runs = [run("home.a", "C", epoch=1), run("home.a", "C", epoch=2)]
    runs.append(run("home.a", "E", epoch=3, checks=[], error="vLLM hiccup"))
    runs += [run("home.b", "C", epoch=e) for e in (1, 2, 3)]
    card = summarize(runs, META)
    a, b = card.goldens
    # A harness error never turns pass^k into a failure: it is unknown.
    assert (a.samples[0].pass_k, a.pass_k, a.variants_pass_k) == (None, None, 0)
    assert a.pass_rate == 1.0 and b.pass_k is True
    (row,) = card.prd_rows
    assert (row.pass_k, row.pass_k_unknown, row.errors, row.flaky) == (1, 1, 1, 0)
    md = render_markdown(card)
    assert "| 4.4.lights-scenes | 2 | 100% | 1/2 (1 —) | 0 | 1 |" in md.splitlines()
    golden_row = next(line for line in md.splitlines() if line.startswith("| home.a |"))
    assert "| 0/1 (1 —) |" in golden_row


def test_prd_rows_carry_flaky_and_error_counts() -> None:
    runs = [run("home.a", "C", epoch=1), run("home.a", "I", epoch=2)]
    runs += [run("home.b", "E", epoch=1, checks=[], error="boom"), run("home.b", "C", epoch=2)]
    runs += [run("home.b", "E", epoch=3, checks=[], error="boom")]
    card = summarize(runs, META)
    (row,) = card.prd_rows
    assert (row.flaky, row.errors, row.pass_k, row.pass_k_unknown) == (1, 2, 0, 1)
    md = render_markdown(card).splitlines()
    assert "| PRD row | goldens | pass rate | pass^k | flaky | errors |" in md
    assert any("| variants passing all k |" in line for line in md)


def test_failing_lists_failures_then_errors_then_judge_errors() -> None:
    judge_error = CheckResult(name="judge", status="error", reason="judge timed out")
    runs = [
        run("home.a", "N", epoch=1, checks=[judge_error]),
        run("home.a", "E", epoch=2, checks=[], error="boom"),
        run("home.a", "I", epoch=3, reason="wanted light.turn_on"),
        run("home.a", "I", epoch=4, reason="wanted light.turn_on"),
    ]
    (g,) = summarize(runs, META).goldens
    assert g.failing == [
        "home.a: ha_called: wanted light.turn_on",
        "home.a: error: boom",
        "home.a: judge: judge timed out",
    ]


def test_failing_always_keeps_an_error_message() -> None:
    runs = [run("home.a", "I", epoch=i, reason=f"miss {i}") for i in range(1, 5)]
    runs.append(run("home.a~1", "E", epoch=1, checks=[], error="boom"))
    (g,) = summarize(runs, META).goldens
    assert g.failing == [
        "home.a: ha_called: miss 1",
        "home.a: ha_called: miss 2",
        "home.a~1: error: boom",
    ]


def test_every_checks_result_is_tallied_per_sample(tmp_path: Path) -> None:
    def checks(ha_called: CheckStatus, judge: CheckStatus) -> list[CheckResult]:
        return [
            CheckResult(name="ha_called", status=ha_called, reason="r"),
            CheckResult(name="reply_matches", status="pass", reason="r"),
            # An untrusted judge category: kept and tallied, but it does not count.
            CheckResult(name="judge", status=judge, reason="r", counted=False),
        ]

    runs = [
        run("home.a", "C", epoch=1, checks=checks("pass", "fail")),
        run("home.a", "I", epoch=2, checks=checks("fail", "pass")),
        run("home.a", "N", epoch=3, checks=checks("error", "fail")),
        run("home.a~1", "E", epoch=1, checks=[], error="boom"),
    ]
    card = summarize(runs, META)
    (g,) = card.goldens
    base, other = g.samples
    tallies = [(t.index, t.name, t.passed, t.failed, t.errored, t.counted) for t in base.checks]
    assert tallies == [
        (0, "ha_called", 1, 1, 1, True),
        (1, "reply_matches", 3, 0, 0, True),
        (2, "judge", 1, 2, 0, False),
    ]
    assert other.checks == []
    # The compact column counts counted results only: 4 of the 6 counted ones passed.
    assert (g.checks_passed, g.checks_counted) == (4, 6)
    golden_row = next(line for line in render_markdown(card).splitlines() if "| home.a |" in line)
    assert "| 4/6 |" in golden_row
    _, json_path = write_report(card, tmp_path)
    assert '"checks": [' in json_path.read_text()


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
    assert row.endswith("| home.a: ha_called: a - b\\|c |")
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


@pytest.mark.parametrize(
    "suffix", ["", "+dirty", " (image not rebuilt)", "+dirty (image not rebuilt)"]
)
def test_header_shortens_commits_but_keeps_what_follows_the_sha(suffix: str) -> None:
    sha = "abc1234" + "0" * 33
    meta = META.model_copy(update={"alfred_commit": f"{sha}{suffix}", "home_service_commit": sha})
    header = render_markdown(summarize([], meta)).splitlines()[2]
    assert f"Alfred `abc1234{suffix}`" in header and "home-service `abc1234`" in header


def test_stack_lines_count_recoveries() -> None:
    stack = {"suite": "home", "boot_seconds": 61.0, "first_reply_ms": 2500.0, "recoveries": 1}
    md = render_markdown(summarize([], META.model_copy(update={"stacks": [stack]})))
    assert "- stack `home`: boot 61 s, first reply 2.5 s, recoveries 1" in md.splitlines()


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

    # A validation error names its first failing field, and how many more there are.
    unreadable_checks = (
        "unreadable checks: checks.0.status: Input should be 'pass', 'fail' or 'error'"
    )
    checks = runs["home.bad-checks"]
    assert (checks.value, checks.checks, checks.error) == ("C", [], unreadable_checks)

    ev = runs["home.bad-evidence"]
    assert (ev.value, ev.reply_ms) == ("E", [])
    assert ev.error == "unreadable evidence: evidence.scenario_id: Field required (+5 more)"

    # Any other exception keeps its own first line.
    variant = runs["home.bad-variant"]
    assert variant.value == "E"
    assert variant.error == "unreadable sample: invalid literal for int() with base 10: 'two'"

    prd = runs["home.bad-prd"]
    assert (prd.value, prd.prd, prd.status) == ("E", [], "shipped")
    assert prd.error == "unreadable sample: prd.0: Input should be a valid string (+1 more)"

    card = summarize(list(runs.values()), META)
    golden = next(g for g in card.goldens if g.scenario_id == "home.bad-checks")
    assert golden.pass_k
    assert golden.failing == [f"home.bad-checks: error: {unreadable_checks}"]
    assert "unreadable checks: checks.0.status" in render_markdown(card)
