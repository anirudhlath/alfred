"""Check registry: name → (params model, function). The judge is scored separately."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from evals.harness.checks import home, latency, llm, notifications, reflex, reply, triggers
from evals.harness.checks.judge_spec import JudgeSpec
from evals.harness.checks.result import CheckResult
from evals.harness.evidence import Evidence, StepKind

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
    "reflex_decision": (reflex.ReflexDecisionParams, reflex.reflex_decision),
    "reflex_not_proposed": (reflex.ReflexNotProposedParams, reflex.reflex_not_proposed),
    "prompt_not_contains": (llm.PromptParams, llm.prompt_not_contains),
    "trigger_created": (triggers.TriggerCreatedParams, triggers.trigger_created),
    "trigger_not_created": (triggers.TriggerTypeParams, triggers.trigger_not_created),
    "trigger_fired": (triggers.TriggerFiredParams, triggers.trigger_fired),
    "notification": (notifications.NotificationParams, notifications.notification),
}

CHECK_PARAMS: dict[str, type[BaseModel]] = {n: p for n, (p, _) in DETERMINISTIC.items()} | {
    "judge": JudgeSpec
}

# Checks that read Alfred's reply, so a golden using one needs at least one user step.
_READS_REPLY: frozenset[str] = frozenset({"judge", "reply_contains", "reply_not_contains"})


def needs_reply(name: str, params: BaseModel) -> bool:
    if name == "latency":
        return getattr(params, "metric", None) == "reply_ms"
    return name in _READS_REPLY


def step_kind_needed(name: str, params: BaseModel) -> StepKind | None:
    """The kind of step the check reads (its ``at_step``, default the golden's last)."""
    if name in ("reflex_decision", "reflex_not_proposed"):
        return "ha_event"
    if name == "latency":
        return latency.STEP_KIND_FOR_METRIC.get(str(getattr(params, "metric", "")))
    return None


def watches_reflex(name: str, params: BaseModel) -> bool:
    """Whether the check reads System 1's calls, so the driver waits for them."""
    if name == "prompt_not_contains":
        return getattr(params, "role", None) == "system1"
    return step_kind_needed(name, params) == "ha_event"


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
    "CheckResult",
    "JudgeSpec",
    "needs_reply",
    "run_check",
    "step_kind_needed",
    "watches_reflex",
]
