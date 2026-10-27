"""Parse the Reflex model's reply into a ReflexProposal (#285).

The model answers ``{"decision": "none"}`` or ``{"decision": "act" | "ask", "reason",
"tool_name", "parameters"}``. The service that runs the tool comes from the registry, never
the model: it is not in the reply format, and a ``target_service`` the model adds anyway is
ignored. Anything this module cannot turn into one of those becomes an ``invalid`` proposal
that keeps the raw text and says what was wrong, so the shadow report shows model failures
instead of hiding them.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from bus.schemas.events import ActionRequest, ReflexProposal

if TYPE_CHECKING:
    from collections.abc import Sequence

    from core.reflex.tool_registry import ToolInfo


def _invalid(raw: str, problem: str) -> ReflexProposal:
    return ReflexProposal(decision="invalid", raw=raw, problem=problem)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def parse_decision(raw: str, tools: Sequence[ToolInfo]) -> ReflexProposal:
    """Turn the model's reply into a proposal, validated against Reflex's own tools.

    ``tools`` is the list the prompt showed. An act or ask naming any other tool is
    invalid, which keeps contract C9's audience rule in shadow mode too.
    """
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError:
        return _invalid(raw, "not JSON")
    if not isinstance(parsed, dict):
        return _invalid(raw, "not a JSON object")

    decision = parsed.get("decision")
    if decision is None and parsed.get("action") == "none":
        return ReflexProposal(decision="none")  # the pre-#285 reply shape
    if decision == "none":
        return ReflexProposal(decision="none", reason=_text(parsed.get("reason")))
    if decision not in ("act", "ask"):
        return _invalid(raw, f"unknown decision {decision!r}")

    tool_name = _text(parsed.get("tool_name"))
    if tool_name is None:
        return _invalid(raw, f"{decision} without a tool")
    tool = next((t for t in tools if t.name == tool_name), None)
    if tool is None:
        return _invalid(raw, f"tool {tool_name!r} is not one of Reflex's tools")
    parameters = parsed.get("parameters", {})
    if not isinstance(parameters, dict):
        return _invalid(raw, "parameters is not an object")

    reason = _text(parsed.get("reason"))
    return ReflexProposal(
        decision=decision,
        reason=reason,
        action=ActionRequest(
            source="reflex-engine",
            target_service=tool.target_service,
            tool_name=tool.name,
            parameters=parameters,
            reason=reason,
        ),
    )
