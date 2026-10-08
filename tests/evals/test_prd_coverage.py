from __future__ import annotations

from typing import TYPE_CHECKING

from evals.harness.coverage import (
    COVERAGE_PATH,
    PRD_PATH,
    REPO_ROOT,
    Coverage,
    coverage_problems,
    load_coverage,
    parse_prd,
)
from evals.harness.scenario import Scenario, load_suites

if TYPE_CHECKING:
    from pathlib import Path

MINI_PRD = """# Alfred
## 1. What is Alfred
text
## 3. Product principles
1. **Proactive, not intrusive.** Alfred acts.
2. **Local-first and private.** Data stays.
## 4. Capability Catalog
### 4.1 Conversation & channels
| Capability | Status | Reference |
|---|---|---|
| Signal messaging (inbound requests + outbound notifications) | Shipped | x |
## 7. Success criteria
| Dimension | Bar |
|---|---|
| Reflex latency | < 500 ms |
"""


def test_parse_prd_finds_principles_rows_and_criteria() -> None:
    rows = parse_prd(MINI_PRD)
    assert [(r.section, r.text) for r in rows] == [
        ("3", "Proactive, not intrusive."),
        ("3", "Local-first and private."),
        ("4.1", "Signal messaging (inbound requests + outbound notifications)"),
        ("7", "Reflex latency"),
    ]


def test_problems_name_unmapped_rows_and_unknown_suites(tmp_path: Path) -> None:
    coverage = Coverage.model_validate(
        {
            "rows": [
                {
                    "id": "principle.1",
                    "section": "3",
                    "prd": "Proactive, not intrusive.",
                    "pending_suites": ["notifications"],
                },
                {
                    "id": "4.1.signal",
                    "section": "4.1",
                    "prd": "Signal messaging",
                    "suites": ["convo"],
                },
                {
                    "id": "7.reflex-latency",
                    "section": "7",
                    "prd": "Reflex latency",
                    "pending_suites": ["reflex"],
                },
                {
                    "id": "4.1.ghost",
                    "section": "4.1",
                    "prd": "Ghost row",
                    "not_llm": "x",
                    "tests": ["nope/"],
                },
            ],
            "headings": [
                {
                    "id": "1.butler",
                    "heading": "1. What is Alfred",
                    "pending_suites": ["conversation"],
                }
            ],
        }
    )
    problems = "\n".join(coverage_problems(MINI_PRD, coverage, {}, tmp_path))
    assert "Local-first and private." in problems  # unmapped principle
    assert "convo" in problems  # unknown suite
    assert "4.1.ghost matches no PRD row" in problems
    assert "nope/" in problems  # missing test path
    assert "is built" not in problems  # no suite is built yet

    # A built suite no golden of which cites the row is still pending for it.
    built = "\n".join(coverage_problems(MINI_PRD, coverage, {"notifications": []}, tmp_path))
    assert "principle.1" not in built


def _golden(
    scenario_id: str, prd: list[str], *, status: str = "shipped", suite: str = "conversation"
) -> Scenario:
    return Scenario.model_validate(
        {
            "id": scenario_id,
            "prd": prd,
            "status": status,
            "suite": suite,
            "path": f"{suite}/{scenario_id}.yaml",
            "steps": [{"user": "Good evening, Alfred."}],
            "expect": [{"ha_not_called": {}}],
        }
    )


def _one_row(**mapping: list[str]) -> Coverage:
    """A coverage map whose one row is MINI_PRD's §7 row, the rest not_llm."""
    return Coverage.model_validate(
        {
            "rows": [
                {
                    "id": "principle.1",
                    "section": "3",
                    "prd": "Proactive",
                    "not_llm": "x",
                    "elsewhere": "y",
                },
                {
                    "id": "principle.2",
                    "section": "3",
                    "prd": "Local-first",
                    "not_llm": "x",
                    "elsewhere": "y",
                },
                {
                    "id": "4.1.signal",
                    "section": "4.1",
                    "prd": "Signal",
                    "not_llm": "x",
                    "elsewhere": "y",
                },
                {"id": "7.reflex-latency", "section": "7", "prd": "Reflex latency", **mapping},
            ]
        }
    )


def test_a_row_only_pending_goldens_cite_is_not_covered_by_its_suite(tmp_path: Path) -> None:
    suites = {
        "reflex": [_golden("reflex.fast", ["7.reflex-latency"], status="pending", suite="reflex")]
    }
    problems = coverage_problems(MINI_PRD, _one_row(suites=["reflex"]), suites, tmp_path)
    assert problems == [
        "7.reflex-latency: only pending goldens in 'reflex' cite it; list it under pending_suites"
    ]


def test_a_built_suite_whose_goldens_for_the_row_are_pending_is_a_pending_suite(
    tmp_path: Path,
) -> None:
    suites = {
        "reflex": [_golden("reflex.fast", ["7.reflex-latency"], status="pending", suite="reflex")]
    }
    coverage = _one_row(pending_suites=["reflex"])
    assert coverage_problems(MINI_PRD, coverage, suites, tmp_path) == []


def test_a_row_a_shipped_golden_cites_is_not_pending(tmp_path: Path) -> None:
    suites = {
        "reflex": [
            _golden("reflex.fast", ["7.reflex-latency"], suite="reflex"),
            _golden("reflex.later", ["7.reflex-latency"], status="pending", suite="reflex"),
        ]
    }
    problems = coverage_problems(MINI_PRD, _one_row(pending_suites=["reflex"]), suites, tmp_path)
    assert problems == [
        "7.reflex-latency: a shipped golden in 'reflex' cites it; move it to suites"
    ]


def test_problems_name_suite_heading_and_golden_faults(tmp_path: Path) -> None:
    coverage = Coverage.model_validate(
        {
            "rows": [
                # Cited by no golden in a built suite.
                {
                    "id": "principle.1",
                    "section": "3",
                    "prd": "Proactive, not intrusive.",
                    "suites": ["conversation"],
                },
                # Names a planned suite that is not built.
                {
                    "id": "principle.2",
                    "section": "3",
                    "prd": "Local-first and private.",
                    "suites": ["memory"],
                },
                # Two prefixes that both match one row.
                {
                    "id": "4.1.signal",
                    "section": "4.1",
                    "prd": "Signal messaging",
                    "suites": ["conversation"],
                },
                {
                    "id": "4.1.signal-short",
                    "section": "4.1",
                    "prd": "Signal",
                    "not_llm": "x",
                    "elsewhere": "y",
                },
                {
                    "id": "7.reflex-latency",
                    "section": "7",
                    "prd": "Reflex latency",
                    "pending_suites": ["reflex"],
                },
            ],
            "headings": [
                {"id": "1.butler", "heading": "1. What is Alfred", "pending_suites": ["voice"]},
                {"id": "1.butler", "heading": "1. What is Alfred", "pending_suites": ["voice"]},
                {"id": "8.roadmap", "heading": "8. Roadmap", "pending_suites": ["voice"]},
            ],
        }
    )
    suites = {
        "conversation": [
            _golden("conversation.signal.hello", ["4.1.signal"]),
            _golden("conversation.signal.ghost", ["4.1.ghost"]),
        ]
    }
    assert coverage_problems(MINI_PRD, coverage, suites, tmp_path) == [
        "PRD §4.1 row 'Signal messaging (inbound requests + outbound notifications)' "
        "matches ['4.1.signal', '4.1.signal-short']",
        "duplicate coverage id 1.butler",
        "8.roadmap: PRD has no heading '## 8. Roadmap'",
        "principle.1: no golden in 'conversation' cites it",
        "principle.2: suite 'memory' is not built; list it under pending_suites",
        "conversation/conversation.signal.ghost.yaml: cites '4.1.ghost', "
        "which coverage.yaml does not define",
    ]


def test_the_real_prd_is_fully_mapped() -> None:
    problems = coverage_problems(
        PRD_PATH.read_text(encoding="utf-8"), load_coverage(COVERAGE_PATH), load_suites(), REPO_ROOT
    )
    assert problems == [], "\n".join(problems)
