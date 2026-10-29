"""A reply that answers the ask Reflex has open.

A yes or a no from the owner, on a channel the ask's notification reaches, answers the open ask
without a model run. The tool names and targets are test data (a generic ``home`` service);
the engine names no tool.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from bus.schemas.events import ActionRequest, ParkedCall, UserRequest
from core.conscious.agent import build_system2_agent, system2_toolsets
from core.conscious.approval_classifier import ApprovalClassifier
from core.conscious.approvals import approval_id_for
from core.conscious.engine import (
    ASK_CONFIRMED_REPLY,
    ASK_DECLINED_REPLY,
    ConsciousDeps,
    ConsciousEngine,
)
from core.conscious.session import SessionManager
from core.reflex.answers import OpenAsk, ReflexAnswer
from core.reflex.settings import load_settings
from core.reflex.tool_registry import REFLEX_AUDIENCE, ToolInfo
from core.routing.pending import decide, pending_key, read_pending, store_pending_action
from shared.spend import build_spend_limits, local_clock
from shared.streams import (
    ACTIONS_STREAM,
    REFLEX_ANSWERED_PREFIX,
    REFLEX_ANSWERS_STREAM,
    REFLEX_ASK_OPEN_KEY,
    SESSIONS_KEY_PREFIX,
)
from tests.core.conscious.helpers import identity
from tests.llm_fakes import Script, tool_call

if TYPE_CHECKING:
    from core.conscious.approvals import Decision

ASK_ID = "ask-1"
LAMP = "light.lamp"
SCRIPT = "movie_night"
_LOCK = {"entity_id": "lock.front", "reason": "The dog walker is here."}


@pytest.fixture(autouse=True)
def _no_integrations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.conscious.engine.fetch_integration_tools", AsyncMock(return_value=[]))


def _info(name: str, risk: str = "benign") -> ToolInfo:
    return ToolInfo(
        name=name,
        description=f"{name} does a thing",
        feature_name=name.split(".")[0],
        feature_description="Home control",
        target_service="home-service",
        audience=REFLEX_AUDIENCE,
        risk=risk,
        input_schema={
            "type": "object",
            "properties": {"entity_id": {"type": "string"}},
            "required": ["entity_id"],
        },
    )


_TOOLS = (_info("home.light_turn_on"), _info("home.lock_unlock", risk="critical"))


def _request(
    content: str = "yes please",
    *,
    who: str = "sir",
    session_id: str = "sess-1",
    channel: str = "web_pwa",
) -> UserRequest:
    return UserRequest(
        source="web-pwa",
        channel=channel,  # type: ignore[arg-type]
        session_id=session_id,
        identity_claim=who,
        authenticated=who == "sir",
        content_type="text",
        content=content,
    )


def _classifier(decision: Decision) -> AsyncMock:
    classifier = AsyncMock(spec=ApprovalClassifier)
    classifier.classify.return_value = decision
    return classifier


def _router() -> AsyncMock:
    router = AsyncMock()
    router.route.return_value = MagicMock(status="success", result={"ok": True}, error=None)
    return router


def _engine(
    redis: fakeredis.FakeAsyncRedis,
    script: Script,
    approval: AsyncMock | None,
    router: AsyncMock | None = None,
) -> ConsciousEngine:
    """A real engine, System 2 agent and session store over fakeredis."""
    identity_gate = MagicMock()
    identity_gate.resolve.side_effect = lambda **kw: identity(
        "sir" if kw["identity_claim"] == "sir" else "guest"
    )
    registry = AsyncMock()
    registry.get_tools.return_value = list(_TOOLS)
    spend = build_spend_limits(redis, cap_usd=5.0, clock=local_clock(lambda: "UTC"))
    return ConsciousEngine(
        deps=ConsciousDeps(
            redis=redis,
            identity_gate=identity_gate,
            session_mgr=SessionManager(redis),
            spend=spend,
            domain_router=router or _router(),
            tool_registry=registry,
            context_reader=AsyncMock(),
            agent=build_system2_agent(
                toolsets=system2_toolsets(None), max_tokens=256, timeout_s=5.0, spend=spend
            ),
            model=script.model,
            notifier=AsyncMock(),
            approval=approval,
        )
    )


def _ask_action(*, script: bool = False) -> ActionRequest:
    return ActionRequest(
        source="reflex-engine",
        request_id=ASK_ID,
        target_service="home-service",
        tool_name="home.script_run" if script else "home.light_turn_on",
        parameters={"target": SCRIPT if script else LAMP},
        reason="It is getting dark.",
    )


async def _open_ask(
    redis: fakeredis.FakeAsyncRedis,
    *,
    script: bool = False,
    deadline: datetime | None = None,
    asked_at: datetime | None = None,
    parked: bool = True,
) -> ActionRequest:
    """The ask Reflex has open and, unless *parked* is False, the action it parked.

    It went out a minute ago unless *asked_at* says otherwise."""
    action = _ask_action(script=script)
    now = datetime.now(UTC)
    ask = OpenAsk(
        ask_id=ASK_ID,
        tool_name=action.tool_name,
        targets=[SCRIPT if script else LAMP],
        origin="state_change",
        asked_at=asked_at or now - timedelta(minutes=1),
        deadline=deadline or now + timedelta(minutes=9),
        noticed="The lamp went off",
        script=script,
    )
    await redis.set(REFLEX_ASK_OPEN_KEY, ask.model_dump_json())
    if parked:
        await store_pending_action(redis, action, ttl_seconds=600)
    return action


async def _answers(redis: fakeredis.FakeAsyncRedis) -> list[ReflexAnswer]:
    return [
        ReflexAnswer.model_validate_json(fields[b"event"])
        for _id, fields in await redis.xrange(REFLEX_ANSWERS_STREAM)
    ]


async def _republished(redis: fakeredis.FakeAsyncRedis) -> list[ActionRequest]:
    return [
        ActionRequest.model_validate_json(fields[b"event"])
        for _id, fields in await redis.xrange(ACTIONS_STREAM)
    ]


async def _ask_is_untouched(redis: fakeredis.FakeAsyncRedis) -> bool:
    return (
        await redis.exists(REFLEX_ASK_OPEN_KEY) == 1
        and await redis.exists(pending_key(ASK_ID)) == 1
        and await _answers(redis) == []
        and await _republished(redis) == []
    )


@pytest.mark.asyncio
async def test_a_yes_from_the_owner_answers_the_ask_without_a_model_run() -> None:
    redis = fakeredis.FakeAsyncRedis()
    script = Script("never sent")
    action = await _open_ask(redis)
    approval = _classifier("approve")
    request = _request("yes please")

    response = await _engine(redis, script, approval).process_request(request)

    assert response.text == ASK_CONFIRMED_REPLY == "Okay."
    assert response.actions_taken == []
    assert response.in_reply_to == request.event_id
    assert (response.channel, response.session_id, response.followup) == (
        "web_pwa",
        "sess-1",
        False,
    )
    assert script.call_count == 0
    (answer,) = await _answers(redis)
    assert (answer.ask_id, answer.answer, answer.channel) == (ASK_ID, "yes", "web_pwa")
    (republished,) = await _republished(redis)
    assert republished.confirmed is True
    assert republished.model_copy(update={"confirmed": False}) == action
    assert await redis.exists(REFLEX_ASK_OPEN_KEY) == 0
    assert await redis.exists(pending_key(ASK_ID)) == 0


@pytest.mark.asyncio
async def test_the_classifier_reads_the_reply_against_the_parked_action() -> None:
    redis = fakeredis.FakeAsyncRedis()
    await _open_ask(redis)
    approval = _classifier("approve")

    await _engine(redis, Script("never sent"), approval).process_request(_request("go ahead"))

    approval.classify.assert_awaited_once_with(
        [
            ParkedCall(
                tool_call_id=ASK_ID,
                tool_name="home.light_turn_on",
                target_service="home-service",
                parameters={"target": LAMP},
                reason="It is getting dark.",
            )
        ],
        "go ahead",
    )


@pytest.mark.asyncio
async def test_a_call_with_no_reason_is_classified_with_an_empty_one() -> None:
    redis = fakeredis.FakeAsyncRedis()
    action = _ask_action().model_copy(update={"reason": None})
    await _open_ask(redis)
    await store_pending_action(redis, action, ttl_seconds=600)
    approval = _classifier("other")

    await _engine(redis, Script("Hello."), approval).process_request(_request("hm"))

    (calls, _reply) = approval.classify.await_args.args
    assert [call.reason for call in calls] == [""]


@pytest.mark.asyncio
async def test_a_no_from_the_owner_records_it_and_runs_nothing() -> None:
    redis = fakeredis.FakeAsyncRedis()
    script = Script("never sent")
    await _open_ask(redis)

    response = await _engine(redis, script, _classifier("deny")).process_request(
        _request("no, leave it", channel="ios")
    )

    assert response.text == ASK_DECLINED_REPLY == "Okay, I'll leave it."
    assert script.call_count == 0
    (answer,) = await _answers(redis)
    assert (answer.answer, answer.channel) == ("no", "ios")
    assert await _republished(redis) == []
    assert await redis.exists(REFLEX_ASK_OPEN_KEY) == 0
    assert await redis.exists(pending_key(ASK_ID)) == 0


@pytest.mark.asyncio
async def test_a_yes_on_signal_answers_too() -> None:
    redis = fakeredis.FakeAsyncRedis()
    await _open_ask(redis)

    response = await _engine(redis, Script("never sent"), _classifier("approve")).process_request(
        _request(channel="signal")
    )

    assert response.text == "Okay."
    assert [a.channel for a in await _answers(redis)] == ["signal"]


@pytest.mark.asyncio
async def test_a_guest_reply_is_a_normal_turn_and_records_nothing() -> None:
    redis = fakeredis.FakeAsyncRedis()
    await _open_ask(redis)
    approval = _classifier("approve")
    script = Script("Hello, guest.")

    response = await _engine(redis, script, approval).process_request(_request(who="guest"))

    assert response.text == "Hello, guest."
    assert script.call_count == 1
    approval.classify.assert_not_awaited()
    assert await _ask_is_untouched(redis)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["voice", "satellite"])
async def test_a_reply_by_voice_is_a_normal_turn(channel: str) -> None:
    redis = fakeredis.FakeAsyncRedis()
    await _open_ask(redis)
    approval = _classifier("approve")
    script = Script("Certainly, sir.")

    response = await _engine(redis, script, approval).process_request(
        _request("yes", channel=channel)
    )

    assert response.text == "Certainly, sir."
    assert script.call_count == 1
    approval.classify.assert_not_awaited()
    assert await _ask_is_untouched(redis)


@pytest.mark.asyncio
async def test_without_a_classifier_every_reply_is_a_normal_turn() -> None:
    redis = fakeredis.FakeAsyncRedis()
    await _open_ask(redis)
    script = Script("Hello.")

    response = await _engine(redis, script, None).process_request(_request())

    assert response.text == "Hello."
    assert await _ask_is_untouched(redis)


@pytest.mark.asyncio
async def test_a_reply_that_is_neither_yes_nor_no_is_a_normal_turn() -> None:
    redis = fakeredis.FakeAsyncRedis()
    await _open_ask(redis)
    approval = _classifier("other")
    script = Script("Sunny, sir.")

    response = await _engine(redis, script, approval).process_request(_request("weather?"))

    assert response.text == "Sunny, sir."
    assert script.call_count == 1
    approval.classify.assert_awaited_once()
    assert await _ask_is_untouched(redis)


@pytest.mark.asyncio
async def test_with_no_open_ask_a_reply_is_a_normal_turn() -> None:
    redis = fakeredis.FakeAsyncRedis()
    approval = _classifier("approve")
    script = Script("Hello.")

    response = await _engine(redis, script, approval).process_request(_request())

    assert response.text == "Hello."
    approval.classify.assert_not_awaited()
    assert await _answers(redis) == []


@pytest.mark.asyncio
async def test_an_ask_past_its_deadline_is_settled_not_answered() -> None:
    redis = fakeredis.FakeAsyncRedis()
    deadline = datetime.now(UTC) - timedelta(minutes=1)
    await _open_ask(redis, deadline=deadline)
    approval = _classifier("approve")
    script = Script("Hello.")

    response = await _engine(redis, script, approval).process_request(_request())

    assert response.text == "Hello."
    approval.classify.assert_not_awaited()
    (answer,) = await _answers(redis)
    assert (answer.answer, answer.time, answer.channel) == ("expired", deadline, None)
    assert await _republished(redis) == []
    assert await redis.exists(REFLEX_ASK_OPEN_KEY) == 0


@pytest.mark.asyncio
async def test_an_open_ask_whose_pending_record_is_gone_is_a_normal_turn() -> None:
    redis = fakeredis.FakeAsyncRedis()
    await _open_ask(redis, parked=False)
    approval = _classifier("approve")
    script = Script("Hello.")

    response = await _engine(redis, script, approval).process_request(_request())

    assert response.text == "Hello."
    approval.classify.assert_not_awaited()
    assert await _answers(redis) == []
    assert await redis.exists(REFLEX_ASK_OPEN_KEY) == 1


@pytest.mark.asyncio
async def test_an_open_ask_whose_pending_record_is_unreadable_is_a_normal_turn() -> None:
    redis = fakeredis.FakeAsyncRedis()
    await _open_ask(redis, parked=False)
    await redis.set(pending_key(ASK_ID), b"{not json", ex=60)
    approval = _classifier("approve")

    response = await _engine(redis, Script("Hello."), approval).process_request(_request())

    assert response.text == "Hello."
    approval.classify.assert_not_awaited()
    assert await _answers(redis) == []


@pytest.mark.asyncio
async def test_a_reply_is_read_without_claiming_the_record_first() -> None:
    """The classifier is a model call: the record must not be held while it thinks."""
    redis = fakeredis.FakeAsyncRedis()
    await _open_ask(redis)
    approval = _classifier("other")
    seen: list[bool] = []

    async def classify(calls: list[ParkedCall], reply: str) -> Decision:
        seen.append(await read_pending(redis, ASK_ID) is not None)
        return "other"

    approval.classify.side_effect = classify

    await _engine(redis, Script("Hello."), approval).process_request(_request())

    assert seen == [True]


@pytest.mark.asyncio
async def test_a_reply_that_lost_to_the_door_is_a_normal_turn() -> None:
    """The Door answered while the classifier was thinking: one answer, one action."""
    redis = fakeredis.FakeAsyncRedis()
    await _open_ask(redis)
    approval = _classifier("approve")

    async def door_first(calls: list[ParkedCall], reply: str) -> Decision:
        assert await decide(redis, ASK_ID, "approve", channel="door")
        return "approve"

    approval.classify.side_effect = door_first
    script = Script("Hello.")

    response = await _engine(redis, script, approval).process_request(_request())

    assert response.text == "Hello."
    assert script.call_count == 1
    (answer,) = await _answers(redis)
    assert (answer.answer, answer.channel) == ("yes", "door")
    assert len(await _republished(redis)) == 1


@pytest.mark.asyncio
async def test_a_yes_to_a_script_ask_allows_the_script() -> None:
    redis = fakeredis.FakeAsyncRedis()
    await _open_ask(redis, script=True)

    response = await _engine(redis, Script("never sent"), _classifier("approve")).process_request(
        _request()
    )

    assert response.text == "Okay."
    assert (await load_settings(redis)).allowed_scripts == [SCRIPT]


@pytest.mark.asyncio
async def test_a_no_to_a_script_ask_allows_nothing() -> None:
    redis = fakeredis.FakeAsyncRedis()
    await _open_ask(redis, script=True)

    await _engine(redis, Script("never sent"), _classifier("deny")).process_request(_request())

    assert (await load_settings(redis)).allowed_scripts == []
    assert [a.answer for a in await _answers(redis)] == ["no"]


# ── a parked run in the same conversation wins ───────────────────────────────


async def _park_a_run(engine: ConsciousEngine, session_id: str) -> str:
    """Park a run on the lock, as the owner's first turn in *session_id*."""
    request = _request("let the dog walker in", session_id=session_id)
    await engine.process_request(request)
    return approval_id_for(request.event_id)


@pytest.mark.asyncio
async def test_a_reply_in_a_conversation_with_its_own_parked_run_resumes_that_run() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router = _router()
    script = Script(tool_call("home_lock_unlock", _LOCK), "The door is unlocked, sir.")
    engine = _engine(redis, script, _classifier("approve"), router)
    approval_id = await _park_a_run(engine, "sess-1")
    await _open_ask(redis)

    response = await engine.process_request(_request("yes please", session_id="sess-1"))

    assert response.text == "The door is unlocked, sir."
    assert [call.args[0].confirmed for call in router.route.await_args_list] == [True]
    assert await read_pending(redis, approval_id) is None
    assert await _ask_is_untouched(redis)


@pytest.mark.asyncio
async def test_a_reply_in_another_conversation_answers_the_ask_and_leaves_the_run_parked() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router = _router()
    script = Script(tool_call("home_lock_unlock", _LOCK))
    engine = _engine(redis, script, _classifier("approve"), router)
    approval_id = await _park_a_run(engine, "sess-1")
    await _open_ask(redis)
    calls_before = script.call_count

    response = await engine.process_request(_request("yes please", session_id="sess-2"))

    assert response.text == "Okay."
    assert response.session_id == "sess-2"
    assert script.call_count == calls_before
    router.route.assert_not_awaited()
    assert [a.answer for a in await _answers(redis)] == ["yes"]
    assert await read_pending(redis, approval_id) is not None
    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    assert session.pending_approval == approval_id


@pytest.mark.asyncio
async def test_a_reply_after_its_own_run_lapsed_answers_the_ask() -> None:
    """The conversation's parked run is gone (the Door decided it): ``_resume_for`` gives None,
    so the reply is free to answer the ask."""
    redis = fakeredis.FakeAsyncRedis()
    engine = _engine(
        redis, Script(tool_call("home_lock_unlock", _LOCK)), _classifier("approve"), _router()
    )
    approval_id = await _park_a_run(engine, "sess-1")
    await redis.delete(pending_key(approval_id))
    await _open_ask(redis, asked_at=datetime.now(UTC))  # after the turn that parked the run

    response = await engine.process_request(_request("yes please", session_id="sess-1"))

    assert response.text == "Okay."
    assert [a.answer for a in await _answers(redis)] == ["yes"]


@pytest.mark.asyncio
async def test_the_ask_is_not_answered_twice() -> None:
    redis = fakeredis.FakeAsyncRedis()
    await _open_ask(redis)
    engine = _engine(redis, Script("Hello."), _classifier("approve"))

    first = await engine.process_request(_request("yes"))
    second = await engine.process_request(_request("yes"))

    assert first.text == "Okay."
    assert second.text == "Hello."  # nothing left to answer: a normal turn
    assert len(await _answers(redis)) == 1
    assert len(await _republished(redis)) == 1
    assert await redis.exists(f"{REFLEX_ANSWERED_PREFIX}{ASK_ID}") == 1


# ── a yes meant for Alfred is not an answer to the ask ─────────────────


async def _store_exchange(
    redis: fakeredis.FakeAsyncRedis, session_id: str, *, asked: str, answered_at: datetime
) -> None:
    """The owner's session already holds one exchange, Alfred's reply stamped *answered_at*."""
    await SessionManager(redis).store_run(
        session_id,
        [
            ModelRequest(parts=[UserPromptPart("Anything for the shopping list?")]),
            ModelResponse(parts=[TextPart(asked)], timestamp=answered_at),
        ],
        "sir",
    )


@pytest.mark.asyncio
async def test_a_yes_after_alfred_spoke_in_the_conversation_is_a_normal_turn() -> None:
    """System 2 asked "Shall I add milk to the list?" after Reflex's ask went out: the owner's
    yes answers System 2, and Reflex's action must not run."""
    redis = fakeredis.FakeAsyncRedis()
    now = datetime.now(UTC)
    await _open_ask(redis, asked_at=now - timedelta(minutes=5))
    await _store_exchange(
        redis,
        "sess-1",
        asked="Shall I add milk to the list?",
        answered_at=now - timedelta(minutes=1),
    )
    approval = _classifier("approve")
    script = Script("Added, sir.")

    response = await _engine(redis, script, approval).process_request(_request("yes please"))

    assert response.text == "Added, sir."
    assert script.call_count == 1
    approval.classify.assert_not_awaited()
    assert await _ask_is_untouched(redis)
    assert (await load_settings(redis)).allowed_scripts == []


@pytest.mark.asyncio
async def test_a_yes_after_alfred_spoke_does_not_allow_a_script_ask() -> None:
    redis = fakeredis.FakeAsyncRedis()
    now = datetime.now(UTC)
    await _open_ask(redis, script=True, asked_at=now - timedelta(minutes=5))
    await _store_exchange(
        redis,
        "sess-1",
        asked="Shall I add milk to the list?",
        answered_at=now - timedelta(minutes=1),
    )

    await _engine(redis, Script("Added."), _classifier("approve")).process_request(_request())

    assert (await load_settings(redis)).allowed_scripts == []
    assert await _ask_is_untouched(redis)


@pytest.mark.asyncio
async def test_a_yes_when_alfred_last_spoke_before_the_ask_answers_the_ask() -> None:
    redis = fakeredis.FakeAsyncRedis()
    now = datetime.now(UTC)
    await _open_ask(redis, asked_at=now - timedelta(minutes=1))
    await _store_exchange(
        redis, "sess-1", asked="Anything else, sir?", answered_at=now - timedelta(minutes=5)
    )
    script = Script("never sent")

    response = await _engine(redis, script, _classifier("approve")).process_request(
        _request("yes please")
    )

    assert response.text == "Okay."
    assert script.call_count == 0
    assert [a.answer for a in await _answers(redis)] == ["yes"]
    assert len(await _republished(redis)) == 1


@pytest.mark.asyncio
async def test_a_yes_to_what_system_2_just_asked_is_not_read_against_the_ask() -> None:
    """The whole road, with the session as the engine stores it: a turn that the classifier
    reads as neither yes nor no, System 2's question, and then the owner's yes."""
    redis = fakeredis.FakeAsyncRedis()
    await _open_ask(redis)  # went out a minute ago
    approval = _classifier("other")
    script = Script("Shall I add milk to the list?", "Added, sir.")
    engine = _engine(redis, script, approval)

    first = await engine.process_request(_request("anything we need?"))
    approval.classify.return_value = "approve"  # a mistaken read of the next reply would answer
    second = await engine.process_request(_request("yes please"))

    assert first.text == "Shall I add milk to the list?"
    assert second.text == "Added, sir."
    assert script.call_count == 2
    approval.classify.assert_awaited_once()  # only the first reply was read against the ask
    assert await _ask_is_untouched(redis)


@pytest.mark.asyncio
async def test_an_acknowledgement_leaves_the_session_history_untouched() -> None:
    """Answering the ask is not a turn: nothing is stored, and no model ran."""
    redis = fakeredis.FakeAsyncRedis()
    now = datetime.now(UTC)
    await _open_ask(redis, asked_at=now - timedelta(minutes=1))
    await _store_exchange(
        redis, "sess-1", asked="Anything else, sir?", answered_at=now - timedelta(minutes=5)
    )
    key = f"{SESSIONS_KEY_PREFIX}sess-1"
    before = await redis.hgetall(key)

    response = await _engine(redis, Script("never sent"), _classifier("approve")).process_request(
        _request("yes please")
    )

    assert response.text == "Okay."
    assert await redis.hgetall(key) == before


@pytest.mark.asyncio
async def test_an_acknowledgement_in_a_new_conversation_stores_no_history() -> None:
    redis = fakeredis.FakeAsyncRedis()
    await _open_ask(redis)

    await _engine(redis, Script("never sent"), _classifier("approve")).process_request(
        _request("yes please", session_id="fresh")
    )

    assert await redis.hget(f"{SESSIONS_KEY_PREFIX}fresh", "messages") is None


@pytest.mark.asyncio
async def test_an_acknowledgement_builds_no_turn_context() -> None:
    """The ack is sent before involuntary recall and the prompts: it pays for neither."""
    redis = fakeredis.FakeAsyncRedis()
    await _open_ask(redis)
    engine = _engine(redis, Script("never sent"), _classifier("approve"))
    engine._recall = AsyncMock(side_effect=AssertionError("recall ran"))  # type: ignore[method-assign]

    response = await engine.process_request(_request("yes please"))

    assert response.text == "Okay."
    engine._recall.assert_not_awaited()


# ── only a Reflex ask is answered in words ───────────────────────────────────


@pytest.mark.asyncio
async def test_an_action_the_router_held_is_not_approved_by_a_reply() -> None:
    """The record under the open ask's id is a critical action another source parked: a
    reply must not approve it, whatever the classifier says."""
    redis = fakeredis.FakeAsyncRedis()
    await _open_ask(redis, parked=False)
    held = _ask_action().model_copy(update={"source": "conscious-engine"})
    await store_pending_action(redis, held)
    approval = _classifier("approve")
    script = Script("Hello.")

    response = await _engine(redis, script, approval).process_request(_request())

    assert response.text == "Hello."
    approval.classify.assert_not_awaited()
    assert await _answers(redis) == []
    assert await _republished(redis) == []
    assert await read_pending(redis, ASK_ID) is not None  # still waiting for the Door
