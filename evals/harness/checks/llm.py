"""Checks over the LLM calls the proxy recorded."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, field_validator

from evals.harness.checks.matching import describe, validate_fields, value_matches
from evals.harness.checks.reply import Needles, hit, needle_text
from evals.harness.checks.result import CheckResult, failed, passed
from evals.harness.evidence import Role  # noqa: TC001 — Pydantic resolves ToolParams.role

if TYPE_CHECKING:
    from evals.harness.evidence import Evidence, ToolCall


def normalize_tool(name: str) -> str:
    """System 2 sends ``home_light_turn_on`` for ``home.light_turn_on`` (engine.py)."""
    return name.replace(".", "_")


class ToolParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    role: Role = "system2"


class ToolArgsParams(ToolParams):
    args: dict[str, Any]

    @field_validator("args")
    @classmethod
    def _patterns_compile(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_fields(value)


class ToolArgAbsentParams(ToolParams):
    key: str


def _calls(evidence: Evidence, role: Role, tool: str) -> list[ToolCall]:
    want = normalize_tool(tool)
    return [
        tc
        for c in evidence.llm_calls
        if c.role == role
        for tc in c.tool_calls
        if normalize_tool(tc.name) == want
    ]


def _names(evidence: Evidence, role: Role) -> str:
    names = sorted({tc.name for c in evidence.llm_calls if c.role == role for tc in c.tool_calls})
    return ", ".join(names) or "nothing"


def tool_called(evidence: Evidence, p: ToolParams) -> CheckResult:
    if _calls(evidence, p.role, p.tool):
        return passed("tool_called", f"{p.role} called {p.tool}")
    return failed(
        "tool_called", f"{p.role} never called {p.tool}; it called {_names(evidence, p.role)}"
    )


def tool_not_called(evidence: Evidence, p: ToolParams) -> CheckResult:
    if _calls(evidence, p.role, p.tool):
        return failed("tool_not_called", f"{p.role} called {p.tool}")
    return passed("tool_not_called", f"{p.role} did not call {p.tool}")


def llm_tool_args(evidence: Evidence, p: ToolArgsParams) -> CheckResult:
    calls = _calls(evidence, p.role, p.tool)
    for tc in calls:
        if all(k in tc.arguments and value_matches(v, tc.arguments[k]) for k, v in p.args.items()):
            return passed("llm_tool_args", f"{p.tool} called with {describe(tc.arguments)}")
    seen = "; ".join(describe(tc.arguments) for tc in calls) or "no calls"
    return failed("llm_tool_args", f"wanted {p.tool} with {describe(p.args)}; saw {seen}")


def llm_tool_args_absent(evidence: Evidence, p: ToolArgAbsentParams) -> CheckResult:
    bad = [tc for tc in _calls(evidence, p.role, p.tool) if p.key in tc.arguments]
    if bad:
        return failed(
            "llm_tool_args_absent",
            f"{p.tool} was given {p.key}={describe(bad[0].arguments[p.key])}",
        )
    return passed("llm_tool_args_absent", f"{p.tool} never given {p.key}")


class PromptParams(Needles):
    role: Role = "system2"


def message_text(message: dict[str, Any]) -> str:
    """A chat message's text: its string content, or its text parts joined."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def prompt_not_contains(evidence: Evidence, p: PromptParams) -> CheckResult:
    calls = [c for c in evidence.llm_calls if c.role == p.role]
    if not calls:
        # Vacuously clean is no evidence: the prompt the golden is about was never sent.
        return failed("prompt_not_contains", f"no {p.role} call was recorded")
    for call in calls:
        for message in call.messages:
            if (found := hit(p, message_text(message))) is not None:
                return failed("prompt_not_contains", f"a {p.role} prompt contains {found!r}")
    return passed(
        "prompt_not_contains", f"{needle_text(p)} absent from {len(calls)} {p.role} prompt(s)"
    )
