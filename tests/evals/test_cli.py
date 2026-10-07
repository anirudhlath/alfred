from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from alfred_cli.main import app
from evals.__main__ import build_parser

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from inspect_ai.model import Model

    from evals.harness.judge import JudgeVerdict

runner = CliRunner()


def _record_memory_main(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], str]]:
    """Replace ``evals.__main__.main`` with a fake that records each ``(argv, prog)`` call."""
    seen: list[tuple[list[str], str]] = []

    def fake_main(argv: Sequence[str], prog: str = "python -m evals") -> None:
        seen.append((list(argv), prog))

    monkeypatch.setattr("evals.__main__.main", fake_main)
    return seen


def test_alfred_has_an_evals_group() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "evals" in result.output


def test_evals_memory_passes_its_arguments_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _record_memory_main(monkeypatch)
    result = runner.invoke(app, ["evals", "memory", "runs", "--limit", "3"])
    assert result.exit_code == 0, result.output
    assert seen == [(["memory", "runs", "--limit", "3"], "alfred evals")]


def test_evals_memory_passes_help_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _record_memory_main(monkeypatch)
    result = runner.invoke(app, ["evals", "memory", "--help"])
    assert result.exit_code == 0, result.output
    assert seen == [(["memory", "--help"], "alfred evals")]


def test_parser_usage_names_the_given_prog() -> None:
    assert build_parser(prog="alfred evals").format_usage().startswith("usage: alfred evals")


def test_evals_calibrate_reports_each_category_and_saves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from evals.harness.judge import CalibrationReport, load_calibration_sets

    # The judge agrees with every label, except that it always says yes on faithfulness
    # and gives no verdict on tone-1.
    answers = {
        item.id: f"VERDICT: {'yes' if item.label or s.category == 'faithfulness' else 'no'}"
        for s in load_calibration_sets()
        for item in s.items
    }
    answers["tone-1"] = "Hard to say."
    built = _fake_judge_model(monkeypatch, list(answers.values()))
    saved: list[tuple[CalibrationReport, Path]] = []
    monkeypatch.setattr(
        "evals.harness.judge.save_report", lambda report, path: saved.append((report, path))
    )
    calibration_file = tmp_path / "calibration.json"
    monkeypatch.setattr("evals.harness.judge.CALIBRATION_FILE", calibration_file)

    result = runner.invoke(app, ["evals", "calibrate", *JUDGE_ARGS])

    assert result.exit_code == 0, result.output
    assert built == [("judge-m", "http://vllm.test/v1")]
    assert [(r.model, path) for r, path in saved] == [("judge-m", calibration_file)]
    lines = result.output.splitlines()
    assert lines[0].startswith("answered") and lines[0].endswith("100% of 5  trusted")
    assert lines[1].startswith("faithfulness") and "50% of 8  UNTRUSTED" in lines[1]
    assert lines[1].endswith(
        "disagreed: faithfulness-2, faithfulness-4, faithfulness-6, faithfulness-8"
    )
    assert lines[2].startswith("tone")
    assert lines[2].endswith("80% of 5  UNTRUSTED  unparseable: tone-1")
    assert lines[3] == f"saved {calibration_file}"


def test_evals_calibrate_names_the_vllm_url_when_the_judge_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refuse(*_: object) -> JudgeVerdict:
        raise ConnectionError("connection refused")

    _fake_judge_model(monkeypatch, [])
    monkeypatch.setattr("evals.harness.judge.Judge.ask", refuse)
    saved: list[object] = []
    monkeypatch.setattr("evals.harness.judge.save_report", lambda *a: saved.append(a))

    result = runner.invoke(app, ["evals", "calibrate", *JUDGE_ARGS])

    assert result.exit_code == 1
    assert result.output.strip().splitlines() == [
        "the judge at --vllm-url http://vllm.test/v1 did not answer: "
        "ConnectionError: connection refused"
    ]
    assert saved == []


JUDGE_ARGS = ["--model", "judge-m", "--vllm-url", "http://vllm.test/v1"]


@pytest.mark.parametrize(
    ("unstarted", "exit_code"), [([], 0), (["home_control", "memory"], 1)], ids=["ok", "unstarted"]
)
def test_evals_run_exits_1_after_the_scorecard_when_a_suite_never_started(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, unstarted: list[str], exit_code: int
) -> None:
    from evals.harness.orchestrate import RunOptions, RunOutcome

    seen: list[RunOptions] = []

    async def fake_run_suites(opts: RunOptions) -> RunOutcome:
        seen.append(opts)
        print("# Alfred eval scorecard")
        return RunOutcome(run_dir=tmp_path, unstarted=unstarted)

    monkeypatch.setattr("evals.harness.orchestrate.run_suites", fake_run_suites)

    result = runner.invoke(app, ["evals", "run", "--no-build", "--home-service", str(tmp_path)])

    assert result.exit_code == exit_code, result.output
    assert [(o.build, o.home_service, o.display) for o in seen] == [(False, tmp_path, "rich")]
    assert result.stdout.startswith("# Alfred eval scorecard\n")
    assert f"logs and report: {tmp_path}" in result.stdout
    if unstarted:
        assert result.stderr.strip().splitlines() == [
            "alfred evals: the stack for home_control, memory failed to start; "
            "see Run problems in the scorecard"
        ]
    else:
        assert result.stderr == ""


def _fake_judge_model(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> list[tuple[str, str]]:
    """Point ``make_judge_model`` at mockllm giving ``answers``; return the (model, url) it got."""
    from inspect_ai.model import ModelOutput, get_model

    built: list[tuple[str, str]] = []

    def fake_model(model: str, base_url: str) -> Model:
        built.append((model, base_url))
        outputs = [ModelOutput.from_content(model="mockllm/model", content=a) for a in answers]
        return get_model("mockllm/model", custom_outputs=outputs, memoize=False)

    monkeypatch.setattr("evals.harness.judge.make_judge_model", fake_model)
    return built


def test_evals_list_shows_scenarios() -> None:
    result = runner.invoke(app, ["evals", "list", "--include-pending"])
    assert result.exit_code == 0, result.output
    assert "home_control.lights.turn_on_named_lamp" in result.output


def test_evals_run_rejects_an_unknown_suite() -> None:
    result = runner.invoke(app, ["evals", "run", "nope"])
    assert result.exit_code == 1 and "unknown suite" in result.output


def test_evals_run_offers_only_displays_that_work_under_eval_async() -> None:
    # Inspect's "full" (Textual) display crashes inside eval_async.
    result = runner.invoke(app, ["evals", "run", "--display", "full"])
    assert result.exit_code == 2
    assert "'full' is not one of 'rich', 'plain', 'none'" in result.output


def test_evals_list_shows_each_golden_with_its_variant_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import functools
    import json

    from evals.harness.scenario import load_suites

    (tmp_path / "demo").mkdir()
    golden = {
        "id": "demo.lights.on",
        "prd": ["4.4.lights-scenes", "1.butler"],
        "status": "shipped",
        "steps": [{"user": "Lights on.", "variants": ["Light, please.", "Lamp on."]}],
        "expect": [{"ha_not_called": {}}],
    }
    (tmp_path / "demo" / "on.yaml").write_text(json.dumps(golden), encoding="utf-8")
    monkeypatch.setattr(
        "evals.harness.scenario.load_suites", functools.partial(load_suites, root=tmp_path)
    )

    result = runner.invoke(app, ["evals", "list"])

    assert result.exit_code == 0, result.output
    (line,) = result.output.splitlines()
    assert line.split() == [
        "demo.lights.on",
        "shipped",
        "\N{MULTIPLICATION SIGN}3",
        "4.4.lights-scenes,",
        "1.butler",
    ]
