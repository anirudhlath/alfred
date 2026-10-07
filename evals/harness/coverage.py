"""Every LLM-related PRD requirement maps to goldens, tests, or a stated not_llm reason.

docs/PRD.md says any PR that changes a capability row updates that row; this module
(and tests/evals/test_prd_coverage.py) make the same PR update the eval map too.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from evals.harness.scenario import Scenario

REPO_ROOT = Path(__file__).resolve().parents[2]
PRD_PATH = REPO_ROOT / "docs" / "PRD.md"
COVERAGE_PATH = REPO_ROOT / "evals" / "coverage.yaml"
PLANNED_SUITES = (
    "conversation",
    "home_control",
    "reflex",
    "triggers",
    "notifications",
    "attention",
    "memory",
    "integrations",
    "guest_boundary",
    "critical_actions",
    "privacy",
    "cross_domain",
    "voice",
)
_SECTION = re.compile(r"^## (\d+)\. ")
_SUBSECTION = re.compile(r"^### (\d+\.\d+) ")
_PRINCIPLE = re.compile(r"^\d+\. \*\*(.+?)\*\*")
_HEADER_CELLS = {"Capability", "Dimension"}


class PrdRow(BaseModel):
    section: str
    text: str


def _norm(text: str) -> str:
    return " ".join(text.split())


def parse_prd(text: str) -> list[PrdRow]:
    rows: list[PrdRow] = []
    section: str | None = None
    for line in text.splitlines():
        if m := _SECTION.match(line):
            section = m.group(1)
            continue
        if m := _SUBSECTION.match(line):
            section = m.group(1)
            continue
        if section == "3" and (m := _PRINCIPLE.match(line)):
            rows.append(PrdRow(section="3", text=m.group(1)))
        elif section and (section.startswith("4.") or section == "7") and line.startswith("| "):
            cell = line.split("|")[1].strip()
            if cell and cell not in _HEADER_CELLS:
                rows.append(PrdRow(section=section, text=_norm(cell)))
    return rows


class _Mapped(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    suites: list[str] = Field(default_factory=list)
    pending_suites: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    not_llm: str | None = None
    elsewhere: str | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        evaluated = bool(self.suites or self.pending_suites)
        if evaluated == (self.not_llm is not None):
            raise ValueError(
                f"{self.id}: give suites/pending_suites or not_llm, not both or neither"
            )
        if self.not_llm is not None and not (self.tests or self.elsewhere):
            raise ValueError(f"{self.id}: a not_llm row names its tests or where it is tested")
        return self


class CoverageEntry(_Mapped):
    section: str
    prd: str


class HeadingEntry(_Mapped):
    heading: str


class Coverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[CoverageEntry]
    headings: list[HeadingEntry] = Field(default_factory=list)


def load_coverage(path: Path = COVERAGE_PATH) -> Coverage:
    return Coverage.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def coverage_problems(
    prd_text: str, coverage: Coverage, suites: dict[str, list[Scenario]], repo_root: Path
) -> list[str]:
    problems: list[str] = []
    rows = parse_prd(prd_text)

    def matches(entry: CoverageEntry, row: PrdRow) -> bool:
        return entry.section == row.section and row.text.startswith(_norm(entry.prd))

    for row in rows:
        hits = [e.id for e in coverage.rows if matches(e, row)]
        if len(hits) != 1:
            problems.append(
                f"PRD §{row.section} row {row.text[:70]!r} matches {hits or 'no entry'}"
            )
    for entry in coverage.rows:
        if not any(matches(entry, row) for row in rows):
            problems.append(f"{entry.id} matches no PRD row (§{entry.section} {entry.prd!r})")

    entries: list[_Mapped] = [*coverage.rows, *coverage.headings]
    ids = [e.id for e in entries]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"duplicate coverage id {dup}")
    headings = {line[3:].strip() for line in prd_text.splitlines() if line.startswith("## ")}
    for h in coverage.headings:
        if h.heading not in headings:
            problems.append(f"{h.id}: PRD has no heading '## {h.heading}'")

    for e in entries:
        for suite in [*e.suites, *e.pending_suites]:
            if suite not in PLANNED_SUITES:
                problems.append(f"{e.id}: unknown suite {suite!r}")
        for suite in e.suites:
            if suite not in suites:
                if suite in PLANNED_SUITES:
                    problems.append(
                        f"{e.id}: suite {suite!r} is not built; list it under pending_suites"
                    )
            elif not any(e.id in s.prd for s in suites[suite]):
                problems.append(f"{e.id}: no golden in {suite!r} cites it")
        for test in e.tests:
            if not (repo_root / test).exists():
                problems.append(f"{e.id}: test path {test} does not exist")

    known = set(ids)
    for scenarios in suites.values():
        for s in scenarios:
            for prd_id in s.prd:
                if prd_id not in known:
                    problems.append(
                        f"{s.path}: cites {prd_id!r}, which coverage.yaml does not define"
                    )
    return problems
