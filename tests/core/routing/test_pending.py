"""The pending store: parked runs and router-held actions, claimed once, decided once."""

from __future__ import annotations

from datetime import UTC, datetime

import fakeredis
import pytest

from bus.schemas.events import (
    PENDING_RECORD,
    ActionRequest,
    ApprovalDecision,
    ParkedAction,
    ParkedCall,
    ParkedRun,
)
from core.routing.pending import (
    CONFIRMATION_TITLE,
    PENDING_TTL_SECONDS,
    claim_pending,
    confirmation_body,
    confirmation_metadata,
    decide,
    get_pending,
    list_pending,
    park_run,
    pending_key,
    pending_payload,
    read_pending,
    store_pending_action,
)
from shared.streams import ACTIONS_STREAM, APPROVALS_STREAM, PENDING_PREFIX

T0 = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


def _action(request_id: str = "act-1", at: datetime = T0) -> ActionRequest:
    return ActionRequest(
        source="conscious-engine",
        request_id=request_id,
        timestamp=at,
        target_service="home-service",
        tool_name="home.unlock_door",
        parameters={"entity_id": "lock.front_door"},
        reason="The dog walker is here.",
    )


def _run(approval_id: str = "appr-1", at: datetime = T0, calls: int = 2) -> ParkedRun:
    return ParkedRun(
        approval_id=approval_id,
        session_id="sess-1",
        channel="web_pwa",
        identity={"user": "sir", "method": "passkey"},
        request_event_id="req-1",
        created_at=at,
        content_type="text",
        tz_name="UTC",
        summary="Unlock the front door and the garage.",
        calls=[
            ParkedCall(
                tool_call_id=f"call-{n}",
                tool_name="home.unlock_door",
                target_service="home-service",
                parameters={"entity_id": f"lock.door_{n}"},
                reason=f"Reason {n}.",
            )
            for n in range(calls)
        ],
        messages="[]",
    )


async def _stream(redis: fakeredis.FakeAsyncRedis, name: str) -> list[dict[bytes, bytes]]:
    return [fields for _id, fields in await redis.xrange(name)]


def test_the_key_lives_under_the_pending_prefix() -> None:
    assert pending_key("abc") == f"{PENDING_PREFIX}abc"
    assert PENDING_TTL_SECONDS == 300


def test_the_confirmation_text_is_one_copy() -> None:
    assert CONFIRMATION_TITLE == "Confirmation required"
    assert (
        confirmation_body("home.unlock_door", "home-service")
        == "Alfred wants to run 'home.unlock_door' on home-service — confirm?"
    )
    assert confirmation_metadata("id-1", "home.unlock_door", {"a": 1}, "Because.") == {
        "pending_action_id": "id-1",
        "tool_name": "home.unlock_door",
        "parameters": {"a": 1},
        "reason": "Because.",
    }


async def test_store_pending_action_writes_a_parked_action_with_a_ttl(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    action = _action()
    await store_pending_action(redis, action)

    raw = await redis.get(pending_key(action.request_id))
    assert raw is not None
    record = PENDING_RECORD.validate_json(raw)
    assert isinstance(record, ParkedAction)
    assert record.action == action
    assert 0 < await redis.ttl(pending_key(action.request_id)) <= PENDING_TTL_SECONDS


async def test_park_run_writes_once(redis: fakeredis.FakeAsyncRedis) -> None:
    run = _run()

    assert await park_run(redis, run) is True
    assert await park_run(redis, run.model_copy(update={"summary": "Different."})) is False

    stored = await read_pending(redis, run.approval_id)
    assert stored == run  # the second write did not replace it
    assert 0 < await redis.ttl(pending_key(run.approval_id)) <= PENDING_TTL_SECONDS


async def test_read_pending_does_not_consume_and_a_miss_is_none(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    run = _run()
    await park_run(redis, run)

    assert await read_pending(redis, run.approval_id) == run
    assert await read_pending(redis, run.approval_id) == run
    assert await read_pending(redis, "ghost") is None


async def test_claim_takes_the_record_once(redis: fakeredis.FakeAsyncRedis) -> None:
    run = _run()
    await park_run(redis, run)

    assert await claim_pending(redis, run.approval_id) == run
    assert await claim_pending(redis, run.approval_id) is None
    assert await redis.exists(pending_key(run.approval_id)) == 0


async def test_get_pending_returns_the_record_and_its_ttl(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    action = _action()
    await store_pending_action(redis, action)

    found = await get_pending(redis, action.request_id)

    assert found is not None
    record, ttl = found
    assert record == ParkedAction(action=action)
    assert 0 < ttl <= PENDING_TTL_SECONDS
    assert await get_pending(redis, "ghost") is None


async def test_list_pending_sorts_both_kinds_oldest_first(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    middle = datetime(2026, 10, 19, 12, 1, tzinfo=UTC)
    newest = datetime(2026, 10, 19, 12, 2, tzinfo=UTC)
    await park_run(redis, _run("run-newest", newest))
    await store_pending_action(redis, _action("act-middle", middle))
    await park_run(redis, _run("run-oldest", T0))

    items = await list_pending(redis)

    assert [record.record_id for record, _ in items] == ["run-oldest", "act-middle", "run-newest"]
    assert all(0 < ttl <= PENDING_TTL_SECONDS for _, ttl in items)


async def test_list_pending_skips_an_unreadable_entry_and_a_naive_timestamp_sorts(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await redis.set(pending_key("corrupt"), b"{not json", ex=60)
    await store_pending_action(redis, _action("aware", datetime(2026, 10, 19, 12, 5, tzinfo=UTC)))
    # A hand-written entry can carry a timestamp with no zone; it must not break the sort.
    await store_pending_action(redis, _action("naive", datetime(2026, 10, 19, 12, 0)))

    items = await list_pending(redis)

    assert [record.record_id for record, _ in items] == ["naive", "aware"]


async def test_a_parked_run_payload_shows_its_first_call_and_its_summary() -> None:
    run = _run()

    payload = pending_payload(run, 90)

    assert payload["request_id"] == "appr-1"
    assert payload["tool_name"] == "home.unlock_door"
    assert payload["target_service"] == "home-service"
    assert payload["parameters"] == {"entity_id": "lock.door_0"}
    assert payload["reason"] == "Unlock the front door and the garage."
    assert payload["source"] == "conscious-engine"
    assert payload["timestamp"] == T0.isoformat()
    assert payload["ttl_seconds"] == 90
    expires = datetime.fromisoformat(payload["expires_at"])
    assert 85 <= (expires - datetime.now(UTC)).total_seconds() <= 90


async def test_an_action_payload_keeps_todays_shape() -> None:
    action = _action()

    payload = pending_payload(ParkedAction(action=action), 90)

    assert payload["request_id"] == "act-1"
    assert payload["tool_name"] == "home.unlock_door"
    assert payload["parameters"] == {"entity_id": "lock.front_door"}
    assert payload["reason"] == "The dog walker is here."
    assert payload["source"] == "conscious-engine"
    assert payload["timestamp"] == T0.isoformat()
    assert set(payload) == {
        "request_id",
        "tool_name",
        "target_service",
        "parameters",
        "reason",
        "source",
        "timestamp",
        "ttl_seconds",
        "expires_at",
    }


async def test_decide_approve_on_an_action_republishes_it_confirmed(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    action = _action()
    await store_pending_action(redis, action)

    assert await decide(redis, action.request_id, "approve") is True

    (fields,) = await _stream(redis, ACTIONS_STREAM)
    republished = ActionRequest.model_validate_json(fields[b"event"])
    assert republished.confirmed is True
    assert republished.model_copy(update={"confirmed": False}) == action
    assert await redis.exists(pending_key(action.request_id)) == 0
    assert await _stream(redis, APPROVALS_STREAM) == []


async def test_decide_deny_on_an_action_drops_it(redis: fakeredis.FakeAsyncRedis) -> None:
    action = _action()
    await store_pending_action(redis, action)

    assert await decide(redis, action.request_id, "deny") is True

    assert await _stream(redis, ACTIONS_STREAM) == []
    assert await _stream(redis, APPROVALS_STREAM) == []
    assert await redis.exists(pending_key(action.request_id)) == 0


@pytest.mark.parametrize("decision", ["approve", "deny"])
async def test_decide_on_a_parked_run_publishes_the_decision_with_the_run(
    redis: fakeredis.FakeAsyncRedis, decision: str
) -> None:
    run = _run()
    await park_run(redis, run)

    assert await decide(redis, run.approval_id, decision) is True  # type: ignore[arg-type]

    (fields,) = await _stream(redis, APPROVALS_STREAM)
    event = ApprovalDecision.model_validate_json(fields[b"event"])
    assert event.source == "channels"
    assert event.approval_id == run.approval_id
    assert event.decision == decision
    assert event.run == run
    assert await _stream(redis, ACTIONS_STREAM) == []
    assert await redis.exists(pending_key(run.approval_id)) == 0


async def test_decide_on_nothing_is_false(redis: fakeredis.FakeAsyncRedis) -> None:
    assert await decide(redis, "ghost", "approve") is False
    assert await _stream(redis, ACTIONS_STREAM) == []
    assert await _stream(redis, APPROVALS_STREAM) == []


async def test_a_second_decision_finds_nothing(redis: fakeredis.FakeAsyncRedis) -> None:
    run = _run()
    await park_run(redis, run)

    assert await decide(redis, run.approval_id, "approve") is True
    assert await decide(redis, run.approval_id, "deny") is False
    assert len(await _stream(redis, APPROVALS_STREAM)) == 1


async def test_decide_on_an_unreadable_record_is_false_and_publishes_nothing(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await redis.set(pending_key("corrupt"), b"{not json", ex=60)

    assert await decide(redis, "corrupt", "approve") is False
    assert await _stream(redis, ACTIONS_STREAM) == []
