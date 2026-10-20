"""Parking primitives: the approval id, the calls a gate deferred, the record that holds them."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import fakeredis
import pytest
from pydantic_ai import DeferredToolRequests, ToolApproved, ToolDenied
from pydantic_ai.messages import ToolCallPart

from bus.schemas.events import ParkedCall, ParkedRun
from core.conscious.approvals import (
    APPROVAL_NAMESPACE,
    Decision,
    approval_id_for,
    claimable_run,
    confirmation_reply,
    park,
    parked_calls,
    results_for,
    summary_of,
)
from core.conscious.effects import EffectLedger
from core.notifications.schema import Urgency
from core.routing.pending import CONFIRMATION_TITLE, pending_key, read_pending

T0 = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


class FakeNotifier:
    """Records what would have been published."""

    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def publish(
        self,
        *,
        title: str,
        body: str,
        source: str,
        urgency: Urgency,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.published.append(
            {
                "title": title,
                "body": body,
                "source": source,
                "urgency": urgency,
                "metadata": metadata,
            }
        )


def _call(
    n: int = 0, tool: str = "home.unlock_door", reason: str | None = "Unlock the front door."
) -> ParkedCall:
    return ParkedCall(
        tool_call_id=f"call-{n}",
        tool_name=tool,
        target_service="home-service",
        parameters={"entity_id": f"lock.door_{n}"},
        reason=reason,
    )


def _run(
    calls: list[ParkedCall] | None = None,
    identity: dict[str, Any] | None = None,
    approval_id: str = "appr-1",
) -> ParkedRun:
    return ParkedRun(
        approval_id=approval_id,
        session_id="sess-1",
        channel="web_pwa",
        identity=identity if identity is not None else {"identity": "sir", "method": "passkey"},
        request_event_id="req-1",
        created_at=T0,
        content_type="text",
        tz_name="UTC",
        summary="Unlock the front door; garage door open",
        calls=calls if calls is not None else [_call(0), _call(1, "home.garage_door_open", None)],
        messages="[]",
    )


def test_the_approval_id_is_deterministic_and_differs_per_key() -> None:
    assert approval_id_for("req-1") == approval_id_for("req-1")
    assert approval_id_for("req-1") != approval_id_for("req-2")
    # A UUID5 under the module's own namespace, so no other id space can collide with it.
    assert len(approval_id_for("req-1")) == 36
    assert APPROVAL_NAMESPACE.version == 5


def test_parked_calls_read_the_gate_metadata() -> None:
    requests = DeferredToolRequests(
        approvals=[
            ToolCallPart("home_unlock_door", {"entity_id": "lock.front"}, "c1"),
            ToolCallPart("home_garage_door_open", {}, "c2"),
        ],
        metadata={
            "c1": {
                "tool_name": "home.unlock_door",
                "target_service": "home-service",
                "parameters": {"entity_id": "lock.front"},
                "reason": "x" * 600,
            },
            "c2": {
                "tool_name": "home.garage_door_open",
                "target_service": "home-service",
                "parameters": {},
                "reason": None,
            },
        },
    )

    first, second = parked_calls(requests)

    assert first == ParkedCall(
        tool_call_id="c1",
        tool_name="home.unlock_door",
        target_service="home-service",
        parameters={"entity_id": "lock.front"},
        reason="x" * 500,
    )
    assert (second.tool_call_id, second.tool_name, second.parameters, second.reason) == (
        "c2",
        "home.garage_door_open",
        {},
        None,
    )


def test_the_reply_names_every_call() -> None:
    calls = [_call(0, reason="Unlock the front door."), _call(1, "home.garage_door_open", None)]

    summary = summary_of(calls)

    assert summary == "Unlock the front door; garage door open"
    assert confirmation_reply(summary) == (
        "Before I do that, I need your confirmation: Unlock the front door; garage door open. "
        "Shall I, sir?"
    )


@pytest.mark.parametrize(
    ("decision", "denial"),
    [
        ("approve", None),
        ("deny", "The user declined."),
        ("other", "Not confirmed; the user moved on."),
    ],
)
def test_results_cover_every_call_with_the_verdict(decision: Decision, denial: str | None) -> None:
    run = _run()

    results = results_for(run, decision)

    assert set(results.approvals) == {"call-0", "call-1"}
    for verdict in results.approvals.values():
        if denial is None:
            assert isinstance(verdict, ToolApproved)
        else:
            assert isinstance(verdict, ToolDenied)
            assert verdict.message == denial


async def test_park_notifies_once_and_ledgers_each_call(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    notifier = FakeNotifier()
    run = _run()

    await park(redis, notifier, run, ledger_key="req-1")
    await park(redis, notifier, run, ledger_key="req-1")

    assert await redis.exists(pending_key("appr-1")) == 1
    assert len(notifier.published) == 1
    note = notifier.published[0]
    assert note["title"] == CONFIRMATION_TITLE
    assert note["body"] == "Alfred wants to run 'home.unlock_door' on home-service — confirm?"
    assert note["source"] == "conscious-engine"
    assert note["urgency"] is Urgency.URGENT
    assert note["metadata"] == {
        "pending_action_id": "appr-1",
        "tool_name": "home.unlock_door",
        "parameters": {"entity_id": "lock.door_0"},
        "reason": "Unlock the front door; garage door open",
    }
    effects = await EffectLedger(redis).read("req-1")
    # A redelivered park leaves the ledger as it was: one parked entry per call.
    assert [(e.tool, e.args, e.status) for e in effects] == [
        ("home.unlock_door", {"entity_id": "lock.door_0"}, "parked"),
        ("home.garage_door_open", {"entity_id": "lock.door_1"}, "parked"),
    ]


async def test_a_guest_record_is_not_claimable(redis: fakeredis.FakeAsyncRedis) -> None:
    notifier = FakeNotifier()
    guest = _run(identity={"identity": "guest", "method": "none"})
    await park(redis, notifier, guest, ledger_key="r")
    await park(redis, notifier, _run(approval_id="appr-2"), ledger_key="r2")

    assert await read_pending(redis, "appr-1") is not None
    assert await claimable_run(redis, "appr-1") is None
    claimable = await claimable_run(redis, "appr-2")
    assert claimable is not None and claimable.approval_id == "appr-2"
    assert await claimable_run(redis, "missing") is None
