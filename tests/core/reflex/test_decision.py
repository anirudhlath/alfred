"""parse_decision — the model's reply as a ReflexProposal (#285)."""

from __future__ import annotations

import json

import pytest

from core.reflex.decision import parse_decision
from core.reflex.tool_registry import ToolInfo

TOOLS = [
    ToolInfo(
        name="home.light_turn_on",
        description="light.turn_on",
        parameters={"target": {"type": "str"}, "brightness_pct": {"type": "float"}},
        feature_name="home",
        feature_description="",
        target_service="home-service",
        audience="reflex",
    )
]


def _act(**overrides: object) -> str:
    reply: dict[str, object] = {
        "decision": "act",
        "reason": "Film at night",
        "tool_name": "home.light_turn_on",
        "target_service": "home-service",
        "parameters": {"target": "Living Room", "brightness_pct": 30},
    }
    reply.update(overrides)
    return json.dumps(reply)


def test_none_is_none() -> None:
    proposal = parse_decision('{"decision": "none"}', TOOLS)

    assert proposal.decision == "none"
    assert proposal.action is None
    assert proposal.raw is None


def test_the_pre_285_none_shape_still_reads_as_none() -> None:
    assert parse_decision('{"action": "none"}', TOOLS).decision == "none"


@pytest.mark.parametrize("decision", ["act", "ask"])
def test_act_and_ask_carry_the_action_and_its_reason(decision: str) -> None:
    proposal = parse_decision(_act(decision=decision), TOOLS)

    assert proposal.decision == decision
    assert proposal.reason == "Film at night"
    assert proposal.action is not None
    assert proposal.action.source == "reflex-engine"
    assert proposal.action.tool_name == "home.light_turn_on"
    assert proposal.action.target_service == "home-service"
    assert proposal.action.parameters == {"target": "Living Room", "brightness_pct": 30}
    assert proposal.action.reason == "Film at night"


def test_a_missing_target_service_is_taken_from_the_tool() -> None:
    reply = json.loads(_act())
    del reply["target_service"]

    proposal = parse_decision(json.dumps(reply), TOOLS)

    assert proposal.decision == "act"
    assert proposal.action is not None
    assert proposal.action.target_service == "home-service"


def test_a_missing_reason_is_allowed() -> None:
    reply = json.loads(_act())
    del reply["reason"]

    proposal = parse_decision(json.dumps(reply), TOOLS)

    assert proposal.decision == "act"
    assert proposal.reason is None


@pytest.mark.parametrize(
    ("raw", "problem"),
    [
        ("not json", "not JSON"),
        ("", "not JSON"),
        ('["act"]', "not a JSON object"),
        ('{"decision": "maybe"}', "unknown decision 'maybe'"),
        ('{"decision": "act", "reason": "x"}', "act without a tool"),
        (
            _act(tool_name="home.lock_unlock"),
            "tool 'home.lock_unlock' is not one of Reflex's tools",
        ),
        (
            _act(target_service="other-service"),
            "target_service 'other-service' does not serve home.light_turn_on",
        ),
        (_act(parameters=["Living Room"]), "parameters is not an object"),
    ],
)
def test_anything_else_is_invalid_and_keeps_the_raw_text(raw: str, problem: str) -> None:
    proposal = parse_decision(raw, TOOLS)

    assert proposal.decision == "invalid"
    assert proposal.problem == problem
    assert proposal.raw == raw
    assert proposal.action is None


# Review Focus 1
@pytest.mark.parametrize(
    "raw",
    [
        "```json\n" + _act() + "\n```",
        _act() + "\nThat should help.",
    ],
)
def test_a_reply_wrapped_in_extra_text_is_invalid_not_a_crash(raw: str) -> None:
    proposal = parse_decision(raw, TOOLS)

    assert proposal.decision == "invalid"
    assert proposal.problem == "not JSON"
    assert proposal.raw == raw


# Review Focus 2
def test_the_pre_285_action_shape_is_invalid_not_executed() -> None:
    legacy = json.dumps(
        {"tool_name": "home.light_turn_on", "target_service": "home-service", "parameters": {}}
    )

    proposal = parse_decision(legacy, TOOLS)

    assert proposal.decision == "invalid"
    assert proposal.problem == "unknown decision None"
