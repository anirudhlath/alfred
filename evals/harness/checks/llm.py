"""Checks over the LLM calls the proxy recorded."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, field_validator

from core.conscious.tools.names import WireNameError, wire_name
from evals.harness.checks.matching import describe, validate_fields, value_matches
from evals.harness.checks.reply import Needles, hit, needle_text
from evals.harness.checks.result import CheckResult, failed, passed
from evals.harness.evidence import Role  # noqa: TC001 — Pydantic resolves ToolParams.role

if TYPE_CHECKING:
    from collections.abc import Iterator

    from evals.harness.evidence import Evidence, LlmCall, ToolCall


def same_tool(a: str, b: str) -> bool:
    """Whether *a* and *b* name one tool: a golden may give its identity
    (``home.light_turn_on``), the model calls its wire name (``home_light_turn_on``).

    The mapping is ``core/conscious/tools/names.wire_name``, the one Alfred offers tools
    by. A name with no wire name (empty, or too long) is only ever the same as itself.
    """
    try:
        return wire_name(a) == wire_name(b)
    except WireNameError:
        return a == b


class ToolParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    role: Role = "system2"
    at_step: int | None = None


class ToolOfferedParams(ToolParams):
    """A tool in the catalog sent to a role, optionally in one scenario step."""


class ToolArgsParams(ToolParams):
    args: dict[str, Any]

    @field_validator("args")
    @classmethod
    def _patterns_compile(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_fields(value)


class ToolArgAbsentParams(ToolParams):
    key: str


def _role_calls(evidence: Evidence, role: Role, at_step: int | None = None) -> list[LlmCall]:
    calls = [c for c in evidence.llm_calls if c.role == role]
    if at_step is not None:
        start, end = evidence.step_window(at_step)
        calls = [c for c in calls if start <= c.t < end]
    return calls


def _calls(evidence: Evidence, role: Role, tool: str, at_step: int | None = None) -> list[ToolCall]:
    return [
        tc
        for c in _role_calls(evidence, role, at_step)
        for tc in c.tool_calls
        if same_tool(tc.name, tool)
    ]


def _names(evidence: Evidence, role: Role, at_step: int | None = None) -> str:
    names = sorted({tc.name for c in _role_calls(evidence, role, at_step) for tc in c.tool_calls})
    return ", ".join(names) or "nothing"


def tool_called(evidence: Evidence, p: ToolParams) -> CheckResult:
    if _calls(evidence, p.role, p.tool, p.at_step):
        return passed("tool_called", f"{p.role} called {p.tool}")
    return failed(
        "tool_called",
        f"{p.role} never called {p.tool}; it called {_names(evidence, p.role, p.at_step)}",
    )


def tool_not_called(evidence: Evidence, p: ToolParams) -> CheckResult:
    if _calls(evidence, p.role, p.tool, p.at_step):
        return failed("tool_not_called", f"{p.role} called {p.tool}")
    return passed("tool_not_called", f"{p.role} did not call {p.tool}")


def llm_tool_args(evidence: Evidence, p: ToolArgsParams) -> CheckResult:
    calls = _calls(evidence, p.role, p.tool, p.at_step)
    for tc in calls:
        if all(k in tc.arguments and value_matches(v, tc.arguments[k]) for k, v in p.args.items()):
            return passed("llm_tool_args", f"{p.tool} called with {describe(tc.arguments)}")
    seen = "; ".join(describe(tc.arguments) for tc in calls) or "no calls"
    return failed("llm_tool_args", f"wanted {p.tool} with {describe(p.args)}; saw {seen}")


def llm_tool_args_absent(evidence: Evidence, p: ToolArgAbsentParams) -> CheckResult:
    bad = [tc for tc in _calls(evidence, p.role, p.tool, p.at_step) if p.key in tc.arguments]
    if bad:
        return failed(
            "llm_tool_args_absent",
            f"{p.tool} was given {p.key}={describe(bad[0].arguments[p.key])}",
        )
    return passed("llm_tool_args_absent", f"{p.tool} never given {p.key}")


class PromptParams(Needles):
    role: Role = "system2"


def tool_offered(evidence: Evidence, p: ToolOfferedParams) -> CheckResult:
    calls = _role_calls(evidence, p.role, p.at_step)
    if not calls:
        return failed("tool_offered", f"no {p.role} call was recorded in the selected scope")
    if any(same_tool(name, p.tool) for c in calls for name in c.tools_offered):
        return passed("tool_offered", f"{p.role} was offered {p.tool}")
    return failed("tool_offered", f"{p.role} was never offered {p.tool}")


def tool_not_offered(evidence: Evidence, p: ToolOfferedParams) -> CheckResult:
    calls = _role_calls(evidence, p.role, p.at_step)
    if not calls:
        return failed("tool_not_offered", f"no {p.role} call was recorded in the selected scope")
    if any(same_tool(name, p.tool) for c in calls for name in c.tools_offered):
        return failed("tool_not_offered", f"{p.role} was offered {p.tool}")
    return passed("tool_not_offered", f"{p.role} was not offered {p.tool}")


class RoleParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Role = "system2"


def tool_calls_offered(evidence: Evidence, p: RoleParams) -> CheckResult:
    """Each tool call names, exactly, a tool offered in that same request.

    Exact on purpose: a call by any other spelling (``home.light_turn_on`` for
    ``home_light_turn_on``) is refused and retried, never dispatched (#299).
    """
    stray = sorted(
        {
            tc.name
            for c in _role_calls(evidence, p.role)
            for tc in c.tool_calls
            if tc.name not in c.tools_offered
        }
    )
    if stray:
        return failed(
            "tool_calls_offered",
            f"{p.role} called {', '.join(stray)}, not offered in that request",
        )
    return passed("tool_calls_offered", f"every {p.role} tool call named an offered tool")


def message_text(message: dict[str, Any]) -> str:
    """A chat message's text: its string content, or its text parts joined."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def _strings(value: Any) -> Iterator[str]:
    """Every string leaf and mapping key, without introducing JSON escape sequences."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def prompt_contains(evidence: Evidence, p: PromptParams) -> CheckResult:
    calls = [c for c in evidence.llm_calls if c.role == p.role]
    if not calls:
        return failed("prompt_contains", f"no {p.role} call was recorded")
    for call in calls:
        for text in _strings(call.request_body or call.messages):
            if (found := hit(p, text)) is not None:
                return passed("prompt_contains", f"a {p.role} prompt contains {found!r}")
    return failed(
        "prompt_contains", f"{needle_text(p)} is in none of {len(calls)} {p.role} prompt(s)"
    )


def prompt_not_contains(evidence: Evidence, p: PromptParams) -> CheckResult:
    calls = [c for c in evidence.llm_calls if c.role == p.role]
    if not calls:
        # Vacuously clean is no evidence: the prompt the golden is about was never sent.
        return failed("prompt_not_contains", f"no {p.role} call was recorded")
    for call in calls:
        # Older Evidence kept only messages. New records retain the full validated request.
        for text in _strings(call.request_body or call.messages):
            if (found := hit(p, text)) is not None:
                return failed("prompt_not_contains", f"a {p.role} prompt contains {found!r}")
    return passed(
        "prompt_not_contains", f"{needle_text(p)} absent from {len(calls)} {p.role} prompt(s)"
    )
