"""The rubric judge (gemma through vLLM) and its calibration against hand-labelled items."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Self

import yaml
from inspect_ai.model import ChatMessageSystem, ChatMessageUser, GenerateConfig, Model, get_model
from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.harness.checks.judge_spec import JudgeCategory, JudgeSpec, Rubric
from evals.harness.checks.result import CheckResult
from evals.harness.evidence import TranscriptTurn  # noqa: TC001 — Pydantic field type

if TYPE_CHECKING:
    from collections.abc import Sequence

    from evals.harness.evidence import Evidence

CALIBRATION_DIR = Path(__file__).resolve().parent.parent / "judge_calibration"
STATE_DIR = Path.home() / ".local" / "share" / "alfred-evals"
CALIBRATION_FILE = STATE_DIR / "calibration.json"
TRUST_THRESHOLD = 0.85
JUDGE_API_KEY = "alfred-eval-not-a-key"

JUDGE_SYSTEM = (
    "You grade replies from Alfred, a formal, butler-style home assistant. Judge only the "
    "question asked, against the conversation shown. Be strict: if the reply does not "
    "clearly satisfy the question, the answer is no."
)
_LABEL = {"user": "User", "alfred": "Alfred", "event": "Home event"}
# Tolerates Markdown emphasis around the colon and the answer: "**VERDICT:** yes".
_VERDICT = re.compile(r"VERDICT[*_\s]*:[*_\s]*(yes|no)\b", re.IGNORECASE)


def render_prompt(transcript: Sequence[TranscriptTurn], spec: JudgeSpec) -> str:
    convo = "\n".join(f"{_LABEL[t.role]}: {t.text}" for t in transcript)
    parts = [f"<conversation>\n{convo}\n</conversation>"]
    if spec.reference:
        parts.append(
            "<reference>\nA good reply would be close in substance to:\n"
            f"{spec.reference}\n</reference>"
        )
    parts.append(f"Question about Alfred's last reply: {spec.rubric}")
    parts.append(
        "Explain in at most three sentences, then end with a final line that is exactly "
        '"VERDICT: yes" or "VERDICT: no".'
    )
    return "\n\n".join(parts)


def parse_verdict(text: str) -> bool | None:
    found = _VERDICT.findall(text)
    return None if not found else found[-1].lower() == "yes"


class JudgeVerdict(BaseModel):
    verdict: bool | None
    rationale: str


class Judge:
    def __init__(self, model: Model) -> None:
        self._model = model

    async def ask(self, transcript: Sequence[TranscriptTurn], spec: JudgeSpec) -> JudgeVerdict:
        output = await self._model.generate(
            [
                ChatMessageSystem(content=JUDGE_SYSTEM),
                ChatMessageUser(content=render_prompt(transcript, spec)),
            ]
        )
        text = output.completion
        return JudgeVerdict(verdict=parse_verdict(text), rationale=text.strip())


def make_judge_model(model: str, base_url: str) -> Model:
    # Inspect otherwise retries an unreachable server for up to 30 minutes, logging below
    # WARNING, so a down vLLM looks like a hang. Two retries inside 120 s, then fail.
    return get_model(
        f"openai-api/vllm/{model}",
        base_url=base_url,
        api_key=JUDGE_API_KEY,
        config=GenerateConfig(
            temperature=0.0, max_tokens=600, max_connections=2, max_retries=2, timeout=120
        ),
    )


async def judge_check(
    judge: Judge, evidence: Evidence, spec: JudgeSpec, trusted: set[str]
) -> CheckResult:
    counted = spec.category in trusted
    tag = spec.category if counted else f"{spec.category}, untrusted"
    if not any(turn.role == "alfred" for turn in evidence.transcript):
        return CheckResult(
            name="judge", status="error", reason=f"[{tag}] no reply to judge", counted=counted
        )
    try:
        verdict = await judge.ask(evidence.transcript, spec)
    except Exception as exc:  # a judge that cannot answer is inconclusive, not a harness failure
        return CheckResult(
            name="judge",
            status="error",
            counted=counted,
            reason=f"[{tag}] judge failed: {type(exc).__name__}: {exc}",
        )
    if verdict.verdict is None:
        return CheckResult(
            name="judge",
            status="error",
            counted=counted,
            reason=f"[{tag}] judge gave no VERDICT line: {verdict.rationale[:300]}",
        )
    answer = "yes" if verdict.verdict else "no"
    return CheckResult(
        name="judge",
        status="pass" if verdict.verdict else "fail",
        counted=counted,
        reason=f"[{tag}] {spec.rubric} → {answer}: {verdict.rationale[:500]}",
    )


class CalibrationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    conversation: list[TranscriptTurn] = Field(min_length=1)
    rubric: Rubric
    reference: str | None = None
    label: bool


class CalibrationSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: JudgeCategory
    items: list[CalibrationItem] = Field(min_length=1)

    @model_validator(mode="after")
    def _ids_are_unique(self) -> Self:
        ids = [item.id for item in self.items]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"{self.category}: duplicate item ids {', '.join(duplicates)}")
        return self


class CategoryResult(BaseModel):
    agreement: float
    n: int
    disagreements: list[str]
    unparseable: list[str]


class CalibrationReport(BaseModel):
    model: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    categories: dict[str, CategoryResult]

    def trusted(self, threshold: float = TRUST_THRESHOLD) -> set[str]:
        return {c for c, r in self.categories.items() if r.agreement >= threshold}


def load_calibration_sets(root: Path = CALIBRATION_DIR) -> list[CalibrationSet]:
    sets: list[CalibrationSet] = []
    origin: dict[str, Path] = {}
    for path in sorted(root.glob("*.yaml")):
        s = CalibrationSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if s.category in origin:
            raise ValueError(
                f"{origin[s.category].name} and {path.name} both calibrate {s.category!r}"
            )
        origin[s.category] = path
        sets.append(s)
    return sets


async def calibrate(judge: Judge, sets: Sequence[CalibrationSet], model: str) -> CalibrationReport:
    categories: dict[str, CategoryResult] = {}
    for s in sets:
        agree, disagreements, unparseable = 0, [], []
        for item in s.items:
            spec = JudgeSpec(category=s.category, rubric=item.rubric, reference=item.reference)
            verdict = await judge.ask(item.conversation, spec)
            if verdict.verdict is None:
                unparseable.append(item.id)
                disagreements.append(item.id)
            elif verdict.verdict == item.label:
                agree += 1
            else:
                disagreements.append(item.id)
        categories[s.category] = CategoryResult(
            agreement=agree / len(s.items),
            n=len(s.items),
            disagreements=disagreements,
            unparseable=unparseable,
        )
    return CalibrationReport(model=model, categories=categories)


def save_report(report: CalibrationReport, path: Path = CALIBRATION_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")


def load_report(path: Path = CALIBRATION_FILE) -> CalibrationReport | None:
    if not path.is_file():
        return None
    return CalibrationReport.model_validate(json.loads(path.read_text(encoding="utf-8")))
