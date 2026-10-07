from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from alfred_cli.main import app
from evals.__main__ import build_parser

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import pytest
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
    assert lines[1].startswith("faithfulness") and "50% of 6  UNTRUSTED" in lines[1]
    assert lines[1].endswith("disagreed: faithfulness-2, faithfulness-4, faithfulness-6")
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
