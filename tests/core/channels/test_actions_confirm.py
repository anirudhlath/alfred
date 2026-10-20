"""The auth-gated action endpoints: list, read, confirm and deny a pending approval."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from bus.schemas.events import ActionRequest, ApprovalDecision, ParkedAction, ParkedCall, ParkedRun
from core.channels.web_server import create_app
from shared.streams import ACTIONS_STREAM, APPROVALS_STREAM
from tests.helpers import aiter_values


def _pending_action() -> ActionRequest:
    return ActionRequest(
        source="conscious-engine",
        target_service="home-service",
        tool_name="home.unlock_door",
        parameters={"entity_id": "lock.front_door"},
    )


def _stored(action: ActionRequest) -> bytes:
    """The bytes the pending store holds for a router-intercepted action."""
    return ParkedAction(action=action).model_dump_json().encode()


def _parked_run() -> ParkedRun:
    return ParkedRun(
        approval_id="appr-1",
        session_id="sess-1",
        channel="web_pwa",
        identity={"user": "sir", "method": "passkey"},
        request_event_id="req-1",
        created_at=datetime(2026, 10, 19, 12, 0, tzinfo=UTC),
        content_type="text",
        tz_name="UTC",
        summary="Unlock the front door.",
        calls=[
            ParkedCall(
                tool_call_id="call-1",
                tool_name="home.unlock_door",
                target_service="home-service",
                parameters={"entity_id": "lock.front_door"},
            )
        ],
        messages="[]",
    )


def test_confirm_happy_path(web_client: TestClient) -> None:
    action = _pending_action()
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.getdel = AsyncMock(return_value=_stored(action))

    resp = web_client.post(f"/api/actions/{action.request_id}/confirm")

    assert resp.status_code == 200
    assert resp.json() == {"status": "confirmed"}
    stream, fields = redis.xadd.call_args[0]
    assert stream == ACTIONS_STREAM
    republished = ActionRequest.model_validate_json(fields["event"])
    assert republished.confirmed is True
    assert republished.request_id == action.request_id
    redis.getdel.assert_awaited_once_with(f"alfred:pending:{action.request_id}")


def test_confirm_missing_or_expired_returns_404(web_client: TestClient) -> None:
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.getdel = AsyncMock(return_value=None)

    resp = web_client.post("/api/actions/ghost-id/confirm")

    assert resp.status_code == 404
    redis.xadd.assert_not_called()


def test_confirm_requires_auth() -> None:
    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = AsyncMock()
    client = TestClient(app)  # no auth cookie

    resp = client.post("/api/actions/any-id/confirm")

    assert resp.status_code == 401


def test_confirm_a_parked_run_publishes_an_approval_decision(web_client: TestClient) -> None:
    run = _parked_run()
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.getdel = AsyncMock(return_value=run.model_dump_json().encode())

    resp = web_client.post(f"/api/actions/{run.approval_id}/confirm")

    assert resp.status_code == 200
    assert resp.json() == {"status": "confirmed"}
    stream, fields = redis.xadd.call_args[0]
    assert stream == APPROVALS_STREAM
    event = ApprovalDecision.model_validate_json(fields["event"])
    assert (event.approval_id, event.decision, event.run) == (run.approval_id, "approve", run)


def test_deny_returns_denied_and_claims(web_client: TestClient) -> None:
    action = _pending_action()
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.getdel = AsyncMock(return_value=_stored(action))

    resp = web_client.post(f"/api/actions/{action.request_id}/deny")

    assert resp.status_code == 200
    assert resp.json() == {"status": "denied"}
    redis.getdel.assert_awaited_once_with(f"alfred:pending:{action.request_id}")
    redis.xadd.assert_not_called()  # a denied router action goes nowhere


def test_deny_a_parked_run_publishes_a_deny_decision(web_client: TestClient) -> None:
    run = _parked_run()
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.getdel = AsyncMock(return_value=run.model_dump_json().encode())

    resp = web_client.post(f"/api/actions/{run.approval_id}/deny")

    assert resp.status_code == 200
    stream, fields = redis.xadd.call_args[0]
    assert stream == APPROVALS_STREAM
    assert ApprovalDecision.model_validate_json(fields["event"]).decision == "deny"


def test_deny_unknown_id_is_404(web_client: TestClient) -> None:
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.getdel = AsyncMock(return_value=None)

    resp = web_client.post("/api/actions/ghost-id/deny")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Pending action not found or expired"
    redis.xadd.assert_not_called()


def test_deny_requires_a_session() -> None:
    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = AsyncMock()
    client = TestClient(app)  # no auth cookie

    resp = client.post("/api/actions/any-id/deny")

    assert resp.status_code == 401
    app.state.redis.getdel.assert_not_called()


@pytest.mark.parametrize("verb", ["confirm", "deny"])
def test_a_decision_is_503_when_the_action_store_is_down(web_client: TestClient, verb: str) -> None:
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.getdel = AsyncMock(side_effect=ConnectionError("redis is down"))

    resp = web_client.post(f"/api/actions/some-id/{verb}")

    assert resp.status_code == 503
    assert resp.json()["detail"] == "Action store unavailable"


@pytest.mark.parametrize("verb", ["confirm", "deny"])
@pytest.mark.parametrize("request_id", ["a" * 129, "has space", "semi;colon", "dot.dot"])
def test_a_decision_refuses_a_request_id_outside_the_charset_before_redis(
    web_client: TestClient, verb: str, request_id: str
) -> None:
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.getdel = AsyncMock(return_value=None)

    resp = web_client.post(f"/api/actions/{request_id}/{verb}")

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid request id"
    redis.getdel.assert_not_awaited()


def test_get_pending_action_returns_payload(web_client: TestClient) -> None:
    action = _pending_action().model_copy(update={"reason": "You asked me to."})
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.get = AsyncMock(return_value=_stored(action))
    redis.ttl = AsyncMock(return_value=42)

    resp = web_client.get(f"/api/actions/{action.request_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"] == action.request_id
    assert body["tool_name"] == "home.unlock_door"
    assert body["reason"] == "You asked me to."
    assert body["ttl_seconds"] == 42
    assert "expires_at" in body
    redis.getdel.assert_not_called()


def test_get_pending_shows_a_parked_run_by_its_first_call(web_client: TestClient) -> None:
    run = _parked_run()
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.get = AsyncMock(return_value=run.model_dump_json().encode())
    redis.ttl = AsyncMock(return_value=42)

    resp = web_client.get(f"/api/actions/{run.approval_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"] == run.approval_id
    assert body["tool_name"] == "home.unlock_door"
    assert body["reason"] == "Unlock the front door."
    assert body["source"] == "conscious-engine"


def test_get_pending_action_missing_returns_404(web_client: TestClient) -> None:
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.get = AsyncMock(return_value=None)

    resp = web_client.get("/api/actions/ghost-id")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Pending action not found or expired"


def test_list_pending_actions(web_client: TestClient) -> None:
    action = _pending_action()
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.scan_iter = MagicMock(return_value=aiter_values([f"alfred:pending:{action.request_id}"]))
    redis.get = AsyncMock(return_value=_stored(action))
    redis.ttl = AsyncMock(return_value=250)

    resp = web_client.get("/api/actions/pending")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["actions"]) == 1
    assert body["actions"][0]["request_id"] == action.request_id
    assert body["actions"][0]["ttl_seconds"] == 250


def test_pending_reads_require_auth() -> None:
    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = AsyncMock()
    client = TestClient(app)  # no auth cookie

    assert client.get("/api/actions/pending").status_code == 401
    assert client.get("/api/actions/any-id").status_code == 401


def test_get_pending_action_corrupt_entry_returns_404(web_client: TestClient) -> None:
    """A value that no longer parses is a tombstone, not a crash: the single read
    answers the same 404 an expired entry does rather than a 500."""
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.get = AsyncMock(return_value=b"{not json")
    redis.ttl = AsyncMock(return_value=42)

    resp = web_client.get("/api/actions/corrupt-id")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Pending action not found or expired"


def test_list_pending_actions_skips_the_corrupt_entry_the_single_read_404s(
    web_client: TestClient,
) -> None:
    """The pair has to agree: the same unreadable value the single read tombstones
    is skipped by the list, which stays 200 with an empty set."""
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.scan_iter = MagicMock(return_value=aiter_values(["alfred:pending:corrupt-id"]))
    redis.get = AsyncMock(return_value=b"{not json")
    redis.ttl = AsyncMock(return_value=42)

    resp = web_client.get("/api/actions/pending")

    assert resp.status_code == 200
    assert resp.json() == {"actions": []}


def test_get_pending_action_is_503_when_the_action_store_is_down(
    web_client: TestClient,
) -> None:
    """An outage is 503 in the store vocabulary, never the 500 that reads as a bug."""
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.get = AsyncMock(side_effect=ConnectionError("redis is down"))

    resp = web_client.get("/api/actions/ghost-id")

    assert resp.status_code == 503
    assert resp.json()["detail"] == "Action store unavailable"


def test_list_pending_actions_is_503_when_the_action_store_is_down(
    web_client: TestClient,
) -> None:
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.scan_iter = MagicMock(side_effect=ConnectionError("redis is down"))

    resp = web_client.get("/api/actions/pending")

    assert resp.status_code == 503
    assert resp.json()["detail"] == "Action store unavailable"


def test_get_pending_action_accepts_a_request_id_at_the_length_cap(
    web_client: TestClient,
) -> None:
    """128 characters is the accepted edge — a uuid4 is 36, so real ids clear it."""
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.get = AsyncMock(return_value=None)

    resp = web_client.get(f"/api/actions/{'a' * 128}")

    assert resp.status_code == 404
    redis.get.assert_awaited_once_with(f"alfred:pending:{'a' * 128}")


def test_get_pending_action_refuses_a_request_id_one_over_the_cap(
    web_client: TestClient,
) -> None:
    """129 is refused before Redis — a 5000-character path segment goes nowhere."""
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.get = AsyncMock(return_value=None)

    resp = web_client.get(f"/api/actions/{'a' * 129}")

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid request id"
    redis.get.assert_not_awaited()


@pytest.mark.parametrize("request_id", ["has space", "semi;colon", "nul%00", "dot.dot"])
def test_get_pending_action_refuses_a_request_id_outside_the_charset(
    web_client: TestClient, request_id: str
) -> None:
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.get = AsyncMock(return_value=None)

    resp = web_client.get("/api/actions/" + request_id)

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid request id"
    redis.get.assert_not_awaited()
