"""Check registry: name → (params model, function). The judge is scored separately."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from evals.harness.checks import home, latency, llm, reply
from evals.harness.checks.judge_spec import JudgeSpec
from evals.harness.checks.result import CheckResult
from evals.harness.evidence import Evidence

if TYPE_CHECKING:
    from pydantic import BaseModel

DeterministicCheck = Callable[[Evidence, Any], CheckResult]

DETERMINISTIC: dict[str, tuple[type[BaseModel], DeterministicCheck]] = {
    "ha_called": (home.HaCalledParams, home.ha_called),
    "ha_not_called": (home.HaNotCalledParams, home.ha_not_called),
    "ha_state": (home.HaStateParams, home.ha_state),
    "tool_called": (llm.ToolParams, llm.tool_called),
    "tool_not_called": (llm.ToolParams, llm.tool_not_called),
    "llm_tool_args": (llm.ToolArgsParams, llm.llm_tool_args),
    "llm_tool_args_absent": (llm.ToolArgAbsentParams, llm.llm_tool_args_absent),
    "reply_contains": (reply.ReplyTextParams, reply.reply_contains),
    "reply_not_contains": (reply.ReplyTextParams, reply.reply_not_contains),
    "latency": (latency.LatencyParams, latency.latency),
}

CHECK_PARAMS: dict[str, type[BaseModel]] = {n: p for n, (p, _) in DETERMINISTIC.items()} | {
    "judge": JudgeSpec
}

# Checks that read Alfred's reply, so a golden using one needs at least one user step.
NEEDS_REPLY: frozenset[str] = frozenset(
    {"judge", "reply_contains", "reply_not_contains", "latency"}
)


def run_check(name: str, params: BaseModel, evidence: Evidence) -> CheckResult:
    """Run one deterministic check. A bug in a check scores ``error``, never ``fail``."""
    _, fn = DETERMINISTIC[name]
    try:
        return fn(evidence, params)
    except Exception as exc:  # a check bug must not read as Alfred failing
        return CheckResult(
            name=name, status="error", reason=f"check raised {type(exc).__name__}: {exc}"
        )


__all__ = [
    "CHECK_PARAMS",
    "DETERMINISTIC",
    "NEEDS_REPLY",
    "CheckResult",
    "JudgeSpec",
    "run_check",
]
