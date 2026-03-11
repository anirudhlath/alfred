"""Data models for the evals runner."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, computed_field

from bus.schemas.events import StateChangedEvent  # noqa: TC001
from shared.tracing import TraceRecord  # noqa: TC001


class ExpectedAction(BaseModel):
    """What the SLM should produce for a scenario."""

    tool_name: str
    target_service: str | None = None
    parameters: dict[str, Any] | None = None


class Scenario(BaseModel):
    """A single eval scenario loaded from YAML."""

    name: str
    description: str | None = None
    tags: list[str] = []
    event: StateChangedEvent
    preferences_dir: str | None = None
    context: str | None = None
    expected: ExpectedAction | None


class Verdict(StrEnum):
    """Outcome of scoring a scenario."""

    PASS = "pass"
    PARTIAL = "partial"
    FAIL = "fail"


class ScenarioResult(BaseModel):
    """Result of running and scoring a single scenario."""

    scenario: Scenario
    verdict: Verdict
    reason: str
    trace: TraceRecord


class EvalRun(BaseModel):
    """A complete eval run with all scenario results."""

    run_id: str
    timestamp: datetime
    model: str
    results: list[ScenarioResult]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def scenario_count(self) -> int:
        """Number of scenarios in this run."""
        return len(self.results)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def summary(self) -> dict[Verdict, int]:
        """Verdict counts derived from results."""
        counts: dict[Verdict, int] = {v: 0 for v in Verdict}
        for r in self.results:
            counts[r.verdict] += 1
        return counts
