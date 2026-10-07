from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest
import yaml
from inspect_ai.model import GenerateConfig, ModelOutput, get_model
from pydantic import ValidationError

from evals.harness.checks.judge_spec import JudgeSpec
from evals.harness.evidence import TranscriptTurn
from evals.harness.judge import (
    CalibrationItem,
    CalibrationReport,
    CalibrationSet,
    CategoryResult,
    Judge,
    JudgeVerdict,
    calibrate,
    judge_check,
    load_calibration_sets,
    load_report,
    make_judge_model,
    parse_verdict,
    render_prompt,
    save_report,
)
from evals.harness.vllm_model import PROVIDER as VLLM_PROVIDER
from tests.evals.harness.factories import evidence

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from inspect_ai.model import ChatMessage

    from evals.harness.evidence import Evidence

SPEC = JudgeSpec(category="tone", rubric="Does the reply keep a formal butler register?")


def judge_saying(*texts: str) -> Judge:
    outputs = [ModelOutput.from_content(model="mockllm/model", content=t) for t in texts]
    return Judge(get_model("mockllm/model", custom_outputs=outputs, memoize=False))


def recording_judge(answer: str) -> tuple[Judge, list[list[ChatMessage]]]:
    """A judge that always gives ``answer`` and keeps every message list it was sent."""
    sent: list[list[ChatMessage]] = []

    def reply(messages: list[ChatMessage], *_: Any) -> ModelOutput:
        sent.append(messages)
        return ModelOutput.from_content(model="mockllm/model", content=answer)

    return Judge(get_model("mockllm/model", custom_outputs=reply, memoize=False)), sent


class BrokenJudge(Judge):
    async def ask(self, transcript: Sequence[TranscriptTurn], spec: JudgeSpec) -> JudgeVerdict:
        raise ConnectionError("connection refused")


def conversation(reply: str) -> Evidence:
    return evidence(
        replies=[reply],
        transcript=[
            TranscriptTurn(role="user", text="Good evening, Alfred."),
            TranscriptTurn(role="alfred", text=reply),
        ],
    )


def test_parse_verdict_takes_the_last_verdict_line() -> None:
    assert parse_verdict("It is polite.\nVERDICT: yes") is True
    assert parse_verdict("verdict: No") is False
    assert parse_verdict("VERDICT: yes\nOn reflection...\nVERDICT: no") is False
    assert parse_verdict("I think so.") is None


def test_parse_verdict_reads_markdown_bold() -> None:
    assert parse_verdict("Formal throughout.\n**VERDICT:** yes") is True
    assert parse_verdict("Too casual.\nVerdict: **no**") is False
    assert parse_verdict("**VERDICT**: no") is False
    assert parse_verdict("**VERDICT:** no\nOn reflection...\nVerdict: **yes**") is True


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
    judge, sent = recording_judge("Formal.\nVERDICT: yes")
    res = await judge_check(judge, conversation("Good evening, sir."), SPEC, trusted={"tone"})
    assert res.status == "pass" and res.counted
    assert len(sent) == 1 and "Alfred: Good evening, sir." in sent[0][-1].text


async def test_untrusted_category_is_reported_but_not_counted() -> None:
    res = await judge_check(judge_saying("VERDICT: no"), conversation("yo"), SPEC, trusted=set())
    assert res.status == "fail" and not res.counted and "untrusted" in res.reason


async def test_unparseable_verdict_is_an_error_not_a_fail() -> None:
    res = await judge_check(
        judge_saying("Hard to say."), conversation("ok"), SPEC, trusted={"tone"}
    )
    assert res.status == "error" and res.counted


async def test_a_transcript_without_an_alfred_turn_is_not_sent_to_the_judge() -> None:
    judge, sent = recording_judge("VERDICT: yes")
    res = await judge_check(judge, evidence(replies=["ok"]), SPEC, trusted={"tone"})
    assert res.status == "error" and res.counted and "no reply to judge" in res.reason
    assert sent == []


async def test_a_judge_that_raises_is_an_error_not_a_crash() -> None:
    broken = BrokenJudge(get_model("mockllm/model", memoize=False))
    res = await judge_check(broken, conversation("ok"), SPEC, trusted={"tone"})
    assert res.status == "error" and res.counted
    assert "ConnectionError: connection refused" in res.reason


async def test_an_unreachable_vllm_is_reported_by_its_own_error() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    judge = Judge(
        get_model(
            f"{VLLM_PROVIDER}/m",
            base_url="http://vllm.test/v1",
            config=GenerateConfig(max_retries=0),
            memoize=False,
            transport=httpx.MockTransport(refuse),
        )
    )
    res = await judge_check(judge, conversation("ok"), SPEC, trusted={"tone"})
    assert res.status == "error" and res.counted
    assert res.reason == "[tone] judge failed: ConnectError: connection refused"


def test_the_real_judge_model_builds_and_gives_up_instead_of_retrying_forever() -> None:
    # Builds the real provider: mocks hid that Inspect's own openai-api one cannot load
    # here. Never generates, so nothing touches the network.
    model = make_judge_model("m", "http://127.0.0.1:9/v1")
    assert str(model) == "alfred-vllm/m"
    assert model.api.base_url == "http://127.0.0.1:9/v1"
    assert model.api.api_key == "alfred-eval-not-a-key"
    config = model.config
    assert config.max_retries == 2 and config.timeout == 120
    assert config.max_connections == 2 and config.temperature == 0.0 and config.max_tokens == 600


async def test_calibration_counts_disagreements_and_round_trips(tmp_path: Path) -> None:
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


async def test_a_judge_that_agrees_everywhere_is_trusted_everywhere() -> None:
    sets = load_calibration_sets()
    answers = [f"VERDICT: {'yes' if i.label else 'no'}" for s in sets for i in s.items]
    report = await calibrate(judge_saying(*answers), sets, model="m")
    assert report.trusted() == {s.category for s in sets}


async def test_an_unparseable_calibration_answer_is_a_disagreement() -> None:
    tone = next(s for s in load_calibration_sets() if s.category == "tone")
    answers = [f"VERDICT: {'yes' if item.label else 'no'}" for item in tone.items]
    answers[0] = "Hard to say."
    result = (await calibrate(judge_saying(*answers), [tone], model="m")).categories["tone"]
    assert result.unparseable == [tone.items[0].id] == result.disagreements


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


def _item(item_id: str, rubric: str = "Does the reply greet the user formally?") -> dict[str, Any]:
    return {
        "id": item_id,
        "conversation": [{"role": "alfred", "text": "Good evening, sir."}],
        "rubric": rubric,
        "label": True,
    }


def test_a_calibration_rubric_obeys_the_judge_spec_rule() -> None:
    with pytest.raises(ValidationError, match="rubric"):
        CalibrationItem.model_validate(_item("tone-1", rubric="Formal?"))


def test_calibration_item_ids_are_unique_within_a_set() -> None:
    with pytest.raises(ValidationError, match="duplicate item ids tone-1"):
        CalibrationSet.model_validate({"category": "tone", "items": [_item("tone-1")] * 2})


def test_two_files_cannot_calibrate_the_same_category(tmp_path: Path) -> None:
    for name, item_id in (("a.yaml", "tone-1"), ("b.yaml", "tone-2")):
        body = yaml.safe_dump({"category": "tone", "items": [_item(item_id)]})
        (tmp_path / name).write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match=r"a\.yaml and b\.yaml both calibrate 'tone'"):
        load_calibration_sets(tmp_path)
