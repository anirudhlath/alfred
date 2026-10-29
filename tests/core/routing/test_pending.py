"""The pending store: parked runs and router-held actions, claimed once, decided once."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
from core.reflex.answers import OpenAsk, ReflexAnswer, record_answer
from core.reflex.settings import load_settings
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
from shared.streams import (
    ACTIONS_STREAM,
    APPROVALS_STREAM,
    PENDING_PREFIX,
    REFLEX_ANSWERED_PREFIX,
    REFLEX_ANSWERS_STREAM,
    REFLEX_ASK_OPEN_KEY,
)

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


async def test_store_pending_action_keeps_five_minutes_by_default(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    action = _action()
    await store_pending_action(redis, action)

    ttl = await redis.ttl(pending_key(action.request_id))
    assert PENDING_TTL_SECONDS == 300
    assert 295 <= ttl <= 300


async def test_store_pending_action_honours_a_longer_ttl(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    action = _action()
    await store_pending_action(redis, action, ttl_seconds=600)

    ttl = await redis.ttl(pending_key(action.request_id))
    assert 595 <= ttl <= 600


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


# --- an action Reflex proposed is an ask --------------------------------------------------

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def _ask_action(request_id: str = "ask-1", *, script: bool = False) -> ActionRequest:
    return ActionRequest(
        source="reflex-engine",
        request_id=request_id,
        timestamp=NOW - timedelta(minutes=5),
        target_service="home-service",
        tool_name="home.script_run" if script else "home.light_turn_on",
        parameters={"target": "movie_night" if script else "light.lamp"},
        reason="It is getting dark.",
    )


def _open_ask(action: ActionRequest, *, deadline: datetime, script: bool = False) -> OpenAsk:
    return OpenAsk(
        ask_id=action.request_id,
        tool_name=action.tool_name,
        targets=["movie_night"] if script else ["light.lamp"],
        origin="state_change",
        asked_at=deadline - timedelta(minutes=10),
        deadline=deadline,
        noticed="The lamp went off",
        script=script,
    )


async def _ask(
    redis: fakeredis.FakeAsyncRedis,
    *,
    deadline: datetime = NOW + timedelta(minutes=5),
    script: bool = False,
    parked: bool = True,
) -> ActionRequest:
    """An open ask and, unless *parked* is False, the pending action it parked."""
    action = _ask_action(script=script)
    ask = _open_ask(action, deadline=deadline, script=script)
    await redis.set(REFLEX_ASK_OPEN_KEY, ask.model_dump_json())
    if parked:
        await store_pending_action(redis, action, ttl_seconds=600)
    return action


async def _answers(redis: fakeredis.FakeAsyncRedis) -> list[ReflexAnswer]:
    return [
        ReflexAnswer.model_validate_json(fields[b"event"])
        for fields in await _stream(redis, REFLEX_ANSWERS_STREAM)
    ]


async def test_a_door_confirm_on_an_open_ask_is_a_yes_and_runs_the_action(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    action = await _ask(redis)

    assert await decide(redis, action.request_id, "approve", now=NOW) is True

    (answer,) = await _answers(redis)
    assert (answer.ask_id, answer.answer, answer.channel, answer.time) == (
        "ask-1",
        "yes",
        "door",
        NOW,
    )
    (fields,) = await _stream(redis, ACTIONS_STREAM)
    republished = ActionRequest.model_validate_json(fields[b"event"])
    assert republished.confirmed is True
    assert republished.model_copy(update={"confirmed": False}) == action
    assert await redis.exists(REFLEX_ASK_OPEN_KEY) == 0
    assert await redis.exists(pending_key(action.request_id)) == 0
    assert await _stream(redis, APPROVALS_STREAM) == []


async def test_a_door_deny_on_an_open_ask_is_a_no_and_runs_nothing(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    action = await _ask(redis)

    assert await decide(redis, action.request_id, "deny", now=NOW) is True

    (answer,) = await _answers(redis)
    assert (answer.answer, answer.channel) == ("no", "door")
    assert await _stream(redis, ACTIONS_STREAM) == []
    assert await redis.exists(REFLEX_ASK_OPEN_KEY) == 0
    assert await redis.exists(pending_key(action.request_id)) == 0


async def test_the_channel_a_decision_came_on_is_recorded(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    action = await _ask(redis)

    await decide(redis, action.request_id, "approve", channel="signal", now=NOW)

    (answer,) = await _answers(redis)
    assert answer.channel == "signal"


async def test_a_second_decision_on_an_answered_ask_finds_nothing(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    action = await _ask(redis)
    assert await decide(redis, action.request_id, "approve", now=NOW) is True

    assert await decide(redis, action.request_id, "deny", now=NOW) is False
    assert await decide(redis, action.request_id, "approve", now=NOW) is False

    assert len(await _answers(redis)) == 1
    assert len(await _stream(redis, ACTIONS_STREAM)) == 1


async def test_a_door_confirm_after_the_ask_lapsed_is_false_and_records_the_expiry_once(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    deadline = NOW - timedelta(minutes=2)
    action = await _ask(redis, deadline=deadline, parked=False)  # the pending record's TTL ran out

    assert await decide(redis, action.request_id, "approve", now=NOW) is False
    assert await decide(redis, action.request_id, "approve", now=NOW) is False

    (answer,) = await _answers(redis)
    assert (answer.answer, answer.time, answer.channel) == ("expired", deadline, None)
    assert await _stream(redis, ACTIONS_STREAM) == []
    assert await redis.exists(REFLEX_ASK_OPEN_KEY) == 0


async def test_a_decision_with_nothing_to_claim_leaves_an_ask_still_waiting(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await _ask(redis)

    assert await decide(redis, "ghost", "approve", now=NOW) is False

    assert await _answers(redis) == []
    assert await redis.exists(REFLEX_ASK_OPEN_KEY) == 1


async def test_an_unreadable_record_settles_a_lapsed_ask_too(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    action = await _ask(redis, deadline=NOW - timedelta(seconds=1), parked=False)
    await redis.set(pending_key(action.request_id), b"{not json", ex=60)

    assert await decide(redis, action.request_id, "approve", now=NOW) is False

    assert [a.answer for a in await _answers(redis)] == ["expired"]


@pytest.mark.parametrize("decision", ["approve", "deny"])
async def test_a_decision_after_the_ask_was_settled_elsewhere_runs_nothing(
    redis: fakeredis.FakeAsyncRedis, decision: str
) -> None:
    """The record is still there but no open ask names it: nothing may run."""
    action = _ask_action()
    await store_pending_action(redis, action, ttl_seconds=600)

    assert await decide(redis, action.request_id, decision, now=NOW) is False  # type: ignore[arg-type]

    assert await _stream(redis, ACTIONS_STREAM) == []
    assert await _answers(redis) == []
    assert await redis.exists(pending_key(action.request_id)) == 0  # claimed, and dropped


async def test_a_decision_on_an_ask_another_ask_replaced_runs_nothing(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    old = _ask_action("old")
    await store_pending_action(redis, old, ttl_seconds=600)
    newer = _open_ask(_ask_action("new"), deadline=NOW + timedelta(minutes=5))
    await redis.set(REFLEX_ASK_OPEN_KEY, newer.model_dump_json())

    assert await decide(redis, "old", "approve", now=NOW) is False

    assert await _stream(redis, ACTIONS_STREAM) == []
    assert await redis.exists(REFLEX_ASK_OPEN_KEY) == 1  # the newer ask is untouched


async def test_a_yes_that_lost_the_race_to_another_answer_runs_nothing(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    """The ask was answered a moment ago (its marker is set, its pending record not yet
    gone): the decision is not recorded, so it does not run and is not reported as made."""
    action = await _ask(redis)
    ask = _open_ask(action, deadline=NOW + timedelta(minutes=5))
    assert await record_answer(redis, ask, "expired", at=NOW, channel=None) is True
    await redis.set(REFLEX_ASK_OPEN_KEY, ask.model_dump_json())  # a straggler's view of it

    assert await decide(redis, action.request_id, "approve", now=NOW) is False

    assert [a.answer for a in await _answers(redis)] == ["expired"]
    assert await _stream(redis, ACTIONS_STREAM) == []


async def test_a_yes_to_a_script_ask_allows_the_script(redis: fakeredis.FakeAsyncRedis) -> None:
    action = await _ask(redis, script=True)

    await decide(redis, action.request_id, "approve", now=NOW)

    assert (await load_settings(redis)).allowed_scripts == ["movie_night"]


async def test_a_no_to_a_script_ask_allows_nothing(redis: fakeredis.FakeAsyncRedis) -> None:
    action = await _ask(redis, script=True)

    await decide(redis, action.request_id, "deny", now=NOW)

    assert (await load_settings(redis)).allowed_scripts == []


async def test_an_action_from_another_source_is_not_an_ask(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await _ask(redis)  # an ask is open, but this action is not it
    action = _action("other-1")
    await store_pending_action(redis, action)

    assert await decide(redis, action.request_id, "approve", now=NOW) is True

    assert len(await _stream(redis, ACTIONS_STREAM)) == 1
    assert await _answers(redis) == []
    assert await redis.exists(REFLEX_ASK_OPEN_KEY) == 1
    assert await redis.exists(f"{REFLEX_ANSWERED_PREFIX}ask-1") == 0


async def test_a_confirm_after_the_deadline_runs_nothing_though_the_record_has_not_lapsed(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    """The pending record's TTL is counted from a later write than the ask's deadline: it can
    outlive the deadline, and a yes in that gap is late."""
    deadline = NOW - timedelta(seconds=30)
    action = await _ask(redis, deadline=deadline)  # the record is still there, with its TTL

    assert await decide(redis, action.request_id, "approve", now=NOW) is False

    (answer,) = await _answers(redis)
    assert (answer.answer, answer.time, answer.channel) == ("expired", deadline, None)
    assert await _stream(redis, ACTIONS_STREAM) == []
    assert await redis.exists(REFLEX_ASK_OPEN_KEY) == 0
    assert await redis.exists(pending_key(action.request_id)) == 0  # claimed, and dropped


async def test_a_confirm_at_the_deadline_is_late_too(redis: fakeredis.FakeAsyncRedis) -> None:
    action = await _ask(redis, deadline=NOW)

    assert await decide(redis, action.request_id, "approve", now=NOW) is False

    assert [a.answer for a in await _answers(redis)] == ["expired"]
    assert await _stream(redis, ACTIONS_STREAM) == []


async def test_a_deny_after_the_deadline_is_recorded_as_silence(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    action = await _ask(redis, deadline=NOW - timedelta(seconds=1))

    assert await decide(redis, action.request_id, "deny", now=NOW) is False

    assert [a.answer for a in await _answers(redis)] == ["expired"]


async def test_a_yes_to_a_script_ask_still_runs_when_the_script_cannot_be_allowed(
    redis: fakeredis.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The allow is best effort: a settings failure must not cancel the republish."""
    action = await _ask(redis, script=True)

    async def refuse(*_args: object) -> None:
        raise ConnectionError("settings are down")

    monkeypatch.setattr("core.reflex.answers.add_allowed_scripts", refuse)

    assert await decide(redis, action.request_id, "approve", now=NOW) is True

    assert [a.answer for a in await _answers(redis)] == ["yes"]
    (fields,) = await _stream(redis, ACTIONS_STREAM)
    assert ActionRequest.model_validate_json(fields[b"event"]).confirmed is True
