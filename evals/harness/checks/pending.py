"""Checks on the pending critical actions observed after a settled step."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evals.harness.checks.llm import same_tool
from evals.harness.checks.matching import describe, validate_fields, value_matches
from evals.harness.checks.result import CheckResult, failed, passed

if TYPE_CHECKING:
    from bus.schemas.events import ActionRequest
    from evals.harness.evidence import Evidence


class PendingActionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at_step: int
    tool: str
    target_service: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    count: int = Field(default=1, ge=0)

    @field_validator("parameters")
    @classmethod
    def _patterns_compile(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_fields(value)


class PendingActionAbsentParams(PendingActionParams):
    count: int = Field(default=0, ge=0, le=0)


def matches(action: ActionRequest, p: PendingActionParams) -> bool:
    return (
        same_tool(action.tool_name, p.tool)
        and (p.target_service is None or value_matches(p.target_service, action.target_service))
        and value_matches(p.parameters, action.parameters)
    )


def _check(evidence: Evidence, p: PendingActionParams, name: str) -> CheckResult:
    snapshot = evidence.pending_at_step(p.at_step)
    if snapshot is None:
        return failed(name, f"no pending snapshot for step {p.at_step}")
    found = [a for a in snapshot.actions if matches(a, p)]
    wanted = f"{p.tool} {p.target_service or ''} {describe(p.parameters)}"
    reason = f"step {p.at_step}: wanted {p.count} pending {wanted}; saw {len(found)}"
    return passed(name, reason) if len(found) == p.count else failed(name, reason)


def pending_action(evidence: Evidence, p: PendingActionParams) -> CheckResult:
    return _check(evidence, p, "pending_action")


def pending_action_absent(evidence: Evidence, p: PendingActionAbsentParams) -> CheckResult:
    return _check(evidence, p, "pending_action_absent")
