from __future__ import annotations

from typing import TYPE_CHECKING

from inspect_ai.model import ModelOutput, get_model

from evals.harness.checks.judge_spec import JudgeSpec
from evals.harness.evidence import TranscriptTurn
from evals.harness.judge import (
    CalibrationReport,
    CategoryResult,
    Judge,
    calibrate,
    judge_check,
    load_calibration_sets,
    load_report,
    parse_verdict,
    render_prompt,
    save_report,
)
from tests.evals.harness.factories import evidence

if TYPE_CHECKING:
    from pathlib import Path

SPEC = JudgeSpec(category="tone", rubric="Does the reply keep a formal butler register?")


def judge_saying(*texts: str) -> Judge:
    outputs = [ModelOutput.from_content(model="mockllm/model", content=t) for t in texts]
    return Judge(get_model("mockllm/model", custom_outputs=outputs, memoize=False))


def test_parse_verdict_takes_the_last_verdict_line() -> None:
    assert parse_verdict("It is polite.\nVERDICT: yes") is True
    assert parse_verdict("verdict: No") is False
    assert parse_verdict("VERDICT: yes\nOn reflection...\nVERDICT: no") is False
    assert parse_verdict("I think so.") is None


def test_prompt_includes_conversation_reference_and_rubric() -> None:
    spec = SPEC.model_copy(update={"reference": "Good evening, sir."})
    prompt = render_prompt(
        [
            TranscriptTurn(role="user", text="hi"),
            TranscriptTurn(role="alfred", text="Good evening, sir."),
        ],
        spec,
    )
    assert "User: hi" in prompt and "Alfred: Good evening, sir." in prompt
    assert "<reference>" in prompt and SPEC.rubric in prompt and "VERDICT: yes" in prompt


async def test_trusted_yes_is_a_counted_pass() -> None:
    res = await judge_check(
        judge_saying("Formal.\nVERDICT: yes"),
        evidence(replies=["Good evening, sir."]),
        SPEC,
        trusted={"tone"},
    )
    assert res.status == "pass" and res.counted


async def test_untrusted_category_is_reported_but_not_counted() -> None:
    res = await judge_check(
        judge_saying("VERDICT: no"), evidence(replies=["yo"]), SPEC, trusted=set()
    )
    assert res.status == "fail" and not res.counted and "untrusted" in res.reason


async def test_unparseable_verdict_is_an_error_not_a_fail() -> None:
    res = await judge_check(
        judge_saying("Hard to say."), evidence(replies=["ok"]), SPEC, trusted={"tone"}
    )
    assert res.status == "error" and res.counted


async def test_calibration_agreement_and_trust(tmp_path: Path) -> None:
    sets = load_calibration_sets()
    tone = next(s for s in sets if s.category == "tone")
    answers = [f"VERDICT: {'yes' if item.label else 'no'}" for item in tone.items]
    answers[0] = "VERDICT: " + ("no" if tone.items[0].label else "yes")  # one disagreement
    report = await calibrate(judge_saying(*answers), [tone], model="m")
    result = report.categories["tone"]
    assert result.n == len(tone.items) and result.disagreements == [tone.items[0].id]
    assert abs(result.agreement - (len(tone.items) - 1) / len(tone.items)) < 1e-9
    save_report(report, tmp_path / "c.json")
    assert load_report(tmp_path / "c.json") == report


def test_trusted_threshold() -> None:
    report = CalibrationReport(
        model="m",
        categories={
            "tone": CategoryResult(agreement=0.9, n=10, disagreements=[], unparseable=[]),
            "answered": CategoryResult(agreement=0.8, n=10, disagreements=[], unparseable=[]),
        },
    )
    assert report.trusted() == {"tone"}


def test_every_shipped_calibration_set_mixes_labels() -> None:
    for s in load_calibration_sets():
        labels = {i.label for i in s.items}
        assert labels == {True, False}, f"{s.category} needs both yes and no items"
