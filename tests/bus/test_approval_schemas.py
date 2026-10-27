"""Tests for the pending-record, approval-decision and follow-up schemas."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from bus.schemas.events import (
    PENDING_RECORD,
    ActionRequest,
    AlfredResponse,
    ApprovalDecision,
    ParkedAction,
    ParkedCall,
    ParkedRun,
)

CREATED_AT = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
# Opaque JSON text with an escaped newline and a non-ASCII character, to prove it survives.
MESSAGES = '[{"kind": "request", "parts": [{"content": "caf\\u00e9\\n"}]}]'


def _call(n: int = 1) -> ParkedCall:
    return ParkedCall(
        tool_call_id=f"call-{n}",
        tool_name="smart_home.unlock_door",
        target_service="home",
        parameters={"entity_id": f"lock.front_{n}"},
        reason="You asked to let the guests in.",
    )


def _run(calls: list[ParkedCall] | None = None) -> ParkedRun:
    return ParkedRun(
        approval_id="appr-1",
        session_id="sess-1",
        channel="web_pwa",
        identity={"user": "sir", "method": "passkey"},
        request_event_id="req-1",
        created_at=CREATED_AT,
        content_type="text",
        tz_name="America/Denver",
        summary="Unlock the front door.",
        calls=calls if calls is not None else [_call()],
        messages=MESSAGES,
    )


def test_a_pending_record_parses_by_kind() -> None:
    run = _run()
    parsed_run = PENDING_RECORD.validate_json(run.model_dump_json())
    assert isinstance(parsed_run, ParkedRun)
    assert parsed_run == run

    action = ParkedAction(
        action=ActionRequest(
            source="router",
            request_id="act-1",
            target_service="home",
            tool_name="smart_home.unlock_door",
        )
    )
    parsed_action = PENDING_RECORD.validate_json(action.model_dump_json())
    assert isinstance(parsed_action, ParkedAction)
    assert parsed_action.record_id == "act-1"
    assert parsed_action.action_requests() == [parsed_action.action]


def test_a_parked_run_needs_a_call() -> None:
    with pytest.raises(ValidationError):
        _run(calls=[])


def test_a_parked_run_projects_one_action_request_per_call() -> None:
    run = _run([_call(1), _call(2)])
    assert run.record_id == run.approval_id

    requests = run.action_requests()

    assert len(requests) == 2
    for request, call in zip(requests, run.calls, strict=True):
        assert request.request_id == run.approval_id
        assert request.source == "conscious-engine"
        assert request.confirmed is False
        assert request.timestamp == run.created_at
        assert request.target_service == call.target_service
        assert request.tool_name == call.tool_name
        assert request.parameters == call.parameters
        assert request.reason == call.reason


def test_an_approval_decision_round_trips() -> None:
    decision = ApprovalDecision(source="api", approval_id="appr-1", decision="approve", run=_run())

    parsed = ApprovalDecision.model_validate_json(decision.model_dump_json())

    assert parsed.event_type == "approval_decision"
    assert parsed.decision == "approve"
    assert parsed.run.messages == MESSAGES
    assert parsed == decision


def test_a_response_is_not_a_followup_by_default() -> None:
    response = AlfredResponse(
        source="conscious-engine", channel="web_pwa", session_id="sess-1", text="Done, sir."
    )
    assert response.followup is False
