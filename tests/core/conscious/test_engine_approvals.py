"""A turn whose critical call waits for the owner: the run is parked, the owner is asked.

System 2 can no longer confirm its own critical action (#317 PR 5): the approval gate
defers the call, the engine parks the run in the pending store, notifies the owner and
replies with a fixed template. A redelivery answers from the side-effect ledger. The
owner's next turn in the conversation approves, declines or moves on from the parked run;
the Door's decision resumes it as a turn of its own, answered as a follow-up.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai_harness.spend import SpendEntry

from bus.schemas.events import (
    AlfredResponse,
    ApprovalDecision,
    ParkedCall,
    ParkedRun,
    UserRequest,
)
from core.conscious.agent import build_system2_agent, system2_toolsets
from core.conscious.approval_classifier import ApprovalClassifier
from core.conscious.approvals import Decision, approval_id_for, confirmation_reply
from core.conscious.effects import Effect, EffectLedger, ledger_reply
from core.conscious.engine import (
    APPROVED_MARKER,
    BUDGET_AFTER_ACTING,
    BUDGET_REPLY,
    DECLINED_MARKER,
    DELIBERATING_REPLY,
    ConsciousDeps,
    ConsciousEngine,
)
from core.conscious.failures import FAILED_REPLY
from core.conscious.runner import process_approval_entry, process_request_entry
from core.conscious.session import SessionManager, text_turn
from core.notifications.schema import Urgency
from core.reflex.tool_registry import REFLEX_AUDIENCE, ToolInfo
from core.routing.pending import claim_pending, decide, park_run, pending_key, read_pending
from shared.redis_streams import forward_range
from shared.spend import build_spend_limits, local_clock
from shared.streams import (
    ANSWERED_KEY,
    APPROVALS_STREAM,
    DEAD_LETTER_STREAM,
    HOME_ACTION_RESULTS_STREAM,
    USER_REQUESTS_STREAM,
    USER_RESPONSES_STREAM,
)
from tests.core.conscious.helpers import identity
from tests.llm_fakes import Script, tool_call, tool_calls

if TYPE_CHECKING:
    from collections.abc import Sequence

_GROUP = "conscious-engine"
_CONSUMER = "worker-1"
_LOCK = {"entity_id": "lock.front", "reason": "The dog walker is here."}


@pytest.fixture(autouse=True)
def _no_integrations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.conscious.engine.fetch_integration_tools", AsyncMock(return_value=[]))


def _info(name: str, risk: str = "benign", audience: str = REFLEX_AUDIENCE) -> ToolInfo:
    return ToolInfo(
        name=name,
        description=f"{name} does a thing",
        feature_name=name.split(".")[0],
        feature_description="Home control",
        target_service="home-service",
        audience=audience,
        risk=risk,
        input_schema={
            "type": "object",
            "properties": {"entity_id": {"type": "string"}},
            "required": ["entity_id"],
        },
    )


_TOOLS = (_info("home.light_turn_on"), _info("home.lock_unlock", risk="critical"))


def _router() -> AsyncMock:
    router = AsyncMock()
    router.route.return_value = MagicMock(status="success", result={"ok": True}, error=None)
    return router


def _request(
    content: str = "let the dog walker in", *, who: str = "sir", session_id: str = "sess-1"
) -> UserRequest:
    return UserRequest(
        source="web-pwa",
        channel="web_pwa",
        session_id=session_id,
        identity_claim=who,
        authenticated=who == "sir",
        content_type="text",
        content=content,
    )


def _classifier(decision: Decision) -> AsyncMock:
    """An approval classifier that reads every reply as *decision*."""
    classifier = AsyncMock(spec=ApprovalClassifier)
    classifier.classify.return_value = decision
    return classifier


def _engine(
    redis: fakeredis.FakeAsyncRedis,
    script: Script,
    router: AsyncMock,
    notifier: AsyncMock,
    *,
    approval: AsyncMock | None = None,
    tools: Sequence[ToolInfo] = _TOOLS,
) -> ConsciousEngine:
    """A real engine, System 2 agent and session store over fakeredis. The identity gate
    resolves a request by its claim: ``sir`` or a guest."""
    identity_gate = MagicMock()
    identity_gate.resolve.side_effect = lambda **kw: identity(
        "sir" if kw["identity_claim"] == "sir" else "guest"
    )
    registry = AsyncMock()
    registry.get_tools.return_value = list(tools)
    spend = build_spend_limits(redis, cap_usd=5.0, clock=local_clock(lambda: "UTC"))
    return ConsciousEngine(
        deps=ConsciousDeps(
            redis=redis,
            identity_gate=identity_gate,
            session_mgr=SessionManager(redis),
            spend=spend,
            domain_router=router,
            tool_registry=registry,
            context_reader=AsyncMock(),
            agent=build_system2_agent(
                toolsets=system2_toolsets(None),
                max_tokens=256,
                timeout_s=5.0,
                spend=spend,
            ),
            model=script.model,
            notifier=notifier,
            approval=approval,
        )
    )


@pytest.mark.asyncio
async def test_a_critical_call_parks_the_run() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(tool_call("home_lock_unlock", _LOCK))
    request = _request()

    response = await _engine(redis, script, router, notifier).process_request(request)

    approval_id = approval_id_for(request.event_id)
    summary = 'lock unlock (entity_id=lock.front) ("The dog walker is here.")'
    assert response.text == confirmation_reply(summary)
    assert response.actions_taken == []
    assert response.in_reply_to == request.event_id
    router.route.assert_not_awaited()
    assert script.call_count == 1

    record = await read_pending(redis, approval_id)
    assert isinstance(record, ParkedRun)
    assert (record.session_id, record.channel, record.request_event_id) == (
        "sess-1",
        "web_pwa",
        request.event_id,
    )
    assert record.identity["identity"] == "sir"
    assert record.summary == summary
    assert (record.content_type, record.area, record.tz_name) == ("text", None, "UTC")
    assert record.calls == [
        ParkedCall(
            tool_call_id=record.calls[0].tool_call_id,
            tool_name="home.lock_unlock",
            target_service="home-service",
            parameters={"entity_id": "lock.front"},
            reason="The dog walker is here.",
        )
    ]
    parked_history = ModelMessagesTypeAdapter.validate_json(record.messages)
    [open_call] = [p for p in parked_history[-1].parts if isinstance(p, ToolCallPart)]
    assert open_call.tool_call_id == record.calls[0].tool_call_id
    assert 0 < await redis.ttl(pending_key(approval_id)) <= 300

    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    assert session.pending_approval == approval_id
    assert isinstance(session.messages[-1], ModelResponse)
    assert session.messages[-1].parts[-1].tool_call_id == open_call.tool_call_id  # type: ignore[union-attr]

    notifier.publish.assert_awaited_once()
    note = notifier.publish.await_args.kwargs
    assert (note["source"], note["urgency"]) == ("conscious-engine", Urgency.URGENT)
    assert note["title"] == "Confirmation required"
    assert note["metadata"]["pending_action_id"] == approval_id


@pytest.mark.asyncio
async def test_the_next_turn_closes_a_parked_call() -> None:
    """A run parked on a critical call alone ends in a response with an open call, and
    Pydantic AI refuses a new prompt after one. The next turn that does not resume (here
    the approval lapsed) closes it as interrupted, and nothing runs."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(tool_call("home_lock_unlock", _LOCK), "Sunny, sir.")
    engine = _engine(redis, script, router, notifier)
    request = _request()
    await engine.process_request(request)
    await redis.delete(pending_key(approval_id_for(request.event_id)))

    response = await engine.process_request(_request("what's the weather?"))

    assert response.text == "Sunny, sir."
    assert response.actions_taken == []
    router.route.assert_not_awaited()
    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    assert session.pending_approval is None
    [call] = [
        p
        for m in session.messages
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, ToolCallPart)
    ]
    [closed] = [
        p
        for m in session.messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart) and p.tool_call_id == call.tool_call_id
    ]
    assert (closed.metadata or {}).get("pydantic_ai_synthesized_tool_return") is True


@pytest.mark.asyncio
async def test_the_next_turn_keeps_an_ordinary_history() -> None:
    """Closing open calls changes nothing in a history that has none."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    light = {"entity_id": "light.hall"}
    script = Script(tool_call("home_light_turn_on", light), "On, sir.", "Sunny, sir.")
    engine = _engine(redis, script, router, notifier)
    sessions = SessionManager(redis)
    await engine.process_request(_request("hall light on"))
    first = (await sessions.load("sess-1", "web_pwa", "sir")).messages

    await engine.process_request(_request("what's the weather?"))

    second = (await sessions.load("sess-1", "web_pwa", "sir")).messages
    assert second[: len(first)] == first
    assert len(second) == len(first) + 2


@pytest.mark.asyncio
async def test_a_benign_sibling_runs_before_the_park() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(
        tool_calls(
            ("home_light_turn_on", {"entity_id": "light.porch"}),
            ("home_lock_unlock", {"entity_id": "lock.front"}),
        )
    )
    request = _request()

    response = await _engine(redis, script, router, notifier).process_request(request)

    router.route.assert_awaited_once()
    assert router.route.await_args.args[0].tool_name == "home.light_turn_on"
    record = await read_pending(redis, approval_id_for(request.event_id))
    assert isinstance(record, ParkedRun)
    assert [call.tool_name for call in record.calls] == ["home.lock_unlock"]
    assert response.text == confirmation_reply("lock unlock (entity_id=lock.front)")
    # What ran is reported; the parked call never reached the catalog.
    assert response.actions_taken == ["home.light_turn_on"]
    assert await EffectLedger(redis).read(request.event_id) == [
        Effect("home.light_turn_on", {"entity_id": "light.porch"}, "ok"),
        Effect("home.lock_unlock", {"entity_id": "lock.front"}, "parked"),
    ]


@pytest.mark.asyncio
async def test_a_parked_turn_ledgers_parked_effects() -> None:
    redis = fakeredis.FakeAsyncRedis()
    script = Script(tool_call("home_lock_unlock", _LOCK))
    request = _request()

    await _engine(redis, script, _router(), AsyncMock()).process_request(request)

    effects = await EffectLedger(redis).read(request.event_id)
    assert effects == [Effect("home.lock_unlock", {"entity_id": "lock.front"}, "parked")]
    assert "I've asked for your confirmation before: lock unlock." in ledger_reply(effects)


@pytest.mark.asyncio
async def test_a_rerun_replies_from_the_run_already_pending() -> None:
    """A delivery that wrote the record and died before its ledger is run again, and the
    model need not defer the same calls twice: the owner is looking at the stored record,
    so the reply, the session and its history describe that one (ruling P5)."""
    redis = fakeredis.FakeAsyncRedis()
    notifier = AsyncMock()
    request = _request()
    approval_id = approval_id_for(request.event_id)
    stored_history = [*text_turn("open the garage", "One moment, sir.")]
    stored = ParkedRun(
        approval_id=approval_id,
        session_id="sess-1",
        channel="web_pwa",
        identity=identity("sir").model_dump(mode="json"),
        request_event_id=request.event_id,
        created_at=datetime(2026, 10, 19, 12, 0, tzinfo=UTC),
        content_type="text",
        tz_name="UTC",
        summary="garage door open (entity_id=cover.garage)",
        calls=[
            ParkedCall(
                tool_call_id="call-garage",
                tool_name="home.garage_door_open",
                target_service="home-service",
                parameters={"entity_id": "cover.garage"},
            )
        ],
        messages=ModelMessagesTypeAdapter.dump_json(stored_history).decode(),
    )
    assert await park_run(redis, stored)
    script = Script(tool_call("home_lock_unlock", _LOCK))

    response = await _engine(redis, script, _router(), notifier).process_request(request)

    assert response.text == confirmation_reply("garage door open (entity_id=cover.garage)")
    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    assert session.pending_approval == approval_id
    assert [type(m) for m in session.messages] == [ModelRequest, ModelResponse]
    assert session.messages[0].parts[0].content == "open the garage"  # type: ignore[union-attr]
    assert notifier.publish.await_args.kwargs["metadata"]["tool_name"] == "home.garage_door_open"
    assert await read_pending(redis, approval_id) == stored


# ── a redelivered turn (runner level, ruling P1) ─────────────────────────────


async def _pending(redis: fakeredis.FakeAsyncRedis, request: UserRequest) -> bytes:
    await redis.xgroup_create(USER_REQUESTS_STREAM, _GROUP, id="0", mkstream=True)
    entry_id: bytes = await redis.xadd(USER_REQUESTS_STREAM, {"event": request.model_dump_json()})
    await redis.xreadgroup(_GROUP, _CONSUMER, {USER_REQUESTS_STREAM: ">"})
    return entry_id


async def _deliver(
    redis: fakeredis.FakeAsyncRedis, entry_id: bytes, engine: Any, delivery: int
) -> None:
    eid = entry_id.decode()
    [(_id, fields)] = await forward_range(redis, USER_REQUESTS_STREAM, min_id=eid, max_id=eid)
    await process_request_entry(
        entry_id,
        fields,
        engine=engine,
        redis=redis,
        stream=USER_REQUESTS_STREAM,
        group=_GROUP,
        consumer=_CONSUMER,
        delivery=delivery,
    )


def _first_publish_is_lost(redis: fakeredis.FakeAsyncRedis) -> None:
    """The runner's first answer never reaches Redis, as on a dropped connection: neither
    the response nor its answered marker lands, so the entry is delivered again."""
    pipeline = redis.pipeline
    lost: list[bool] = []

    def flaky_pipeline(*args: Any, **kwargs: Any) -> Any:
        pipe = pipeline(*args, **kwargs)
        execute = pipe.execute

        async def flaky_execute(*a: Any, **k: Any) -> Any:
            publishing = any(
                tuple(command[:2]) == ("XADD", USER_RESPONSES_STREAM)
                for command, _options in pipe.command_stack
            )
            if publishing and not lost:
                lost.append(True)
                raise ConnectionError("connection lost")
            return await execute(*a, **k)

        pipe.execute = flaky_execute
        return pipe

    redis.pipeline = flaky_pipeline  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_a_redelivered_turn_parks_and_notifies_once() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(tool_call("home_lock_unlock", _LOCK))
    request = _request()
    entry_id = await _pending(redis, request)
    engine = _engine(redis, script, router, notifier)
    runs = AsyncMock(wraps=engine.process_request)
    engine.process_request = runs  # type: ignore[method-assign]
    _first_publish_is_lost(redis)

    await _deliver(redis, entry_id, engine, delivery=1)
    assert await forward_range(redis, USER_RESPONSES_STREAM) == []
    await _deliver(redis, entry_id, engine, delivery=2)

    runs.assert_awaited_once()
    assert script.call_count == 1
    router.route.assert_not_awaited()
    notifier.publish.assert_awaited_once()
    assert notifier.publish.await_args.kwargs["urgency"] is Urgency.URGENT
    [(_id, fields)] = await forward_range(redis, USER_RESPONSES_STREAM)
    text = json.loads(fields[b"event"])["text"]
    assert "I've asked for your confirmation before: lock unlock." in text
    assert int((await redis.xpending(USER_REQUESTS_STREAM, _GROUP))["pending"]) == 0
    approval_id = approval_id_for(request.event_id)
    assert isinstance(await read_pending(redis, approval_id), ParkedRun)
    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    assert session.pending_approval == approval_id


# ── the owner's reply in the conversation ────────────────────────────────────

_YES = "Yes, go ahead"


async def _park(engine: ConsciousEngine, request: UserRequest | None = None) -> str:
    """Park a run on the lock, as the owner's first turn; returns its approval id."""
    request = request or _request()
    await engine.process_request(request)
    return approval_id_for(request.event_id)


def _returns_for(messages: list[Any], tool_call_id: str) -> list[ToolReturnPart]:
    """The results *messages* hold for one call."""
    return [
        p
        for m in messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart) and p.tool_call_id == tool_call_id
    ]


def _synthesized(part: ToolReturnPart) -> bool:
    """Whether *part* is a return Pydantic AI made up to close an interrupted call."""
    return (part.metadata or {}).get("pydantic_ai_synthesized_tool_return") is True


@pytest.mark.asyncio
async def test_a_yes_runs_the_parked_call_in_the_same_turn() -> None:
    """The approved call really runs. A resume passes the parked history as it is, so its
    open call is the one answered; closing it first as interrupted (as a turn that does not
    resume does) would leave the approval nothing to run."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier, approval = _router(), AsyncMock(), _classifier("approve")
    script = Script(tool_call("home_lock_unlock", _LOCK), "The door is unlocked, sir.")
    engine = _engine(redis, script, router, notifier, approval=approval)
    approval_id = await _park(engine)
    record = await read_pending(redis, approval_id)
    assert isinstance(record, ParkedRun)
    reply = _request(_YES)

    response = await engine.process_request(reply)

    approval.classify.assert_awaited_once_with(record.calls, _YES)
    router.route.assert_awaited_once()
    action = router.route.await_args.args[0]
    assert (action.tool_name, action.parameters, action.confirmed) == (
        "home.lock_unlock",
        {"entity_id": "lock.front"},
        True,
    )
    assert response.text == "The door is unlocked, sir."
    assert response.actions_taken == ["home.lock_unlock"]
    assert response.in_reply_to == reply.event_id
    # One run takes the call's result and the reply's words together.
    assert script.call_count == 2
    assert script.outcomes(1) == ["success"]
    assert script.prompt(1) == _YES
    assert await read_pending(redis, approval_id) is None
    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    assert session.pending_approval is None
    [returned] = _returns_for(session.messages, record.calls[0].tool_call_id)
    assert returned.outcome == "success"
    assert not _synthesized(returned)
    # Under this turn's request id, and published under the approval for the Door.
    effects = await EffectLedger(redis).read(reply.event_id)
    assert [(effect.tool, effect.status) for effect in effects] == [("home.lock_unlock", "ok")]
    [(_id, fields)] = await forward_range(redis, HOME_ACTION_RESULTS_STREAM)
    assert json.loads(fields[b"event"])["request_id"] == approval_id
    notifier.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_no_denies_and_the_model_acknowledges() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(tool_call("home_lock_unlock", _LOCK), "Very well, sir. It stays locked.")
    engine = _engine(redis, script, router, notifier, approval=_classifier("deny"))
    approval_id = await _park(engine)

    response = await engine.process_request(_request("No, leave it locked"))

    assert script.returns(1) == ["The user declined."]
    router.route.assert_not_awaited()
    assert response.text == "Very well, sir. It stays locked."
    assert response.actions_taken == []
    assert await read_pending(redis, approval_id) is None
    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    assert session.pending_approval is None


@pytest.mark.asyncio
async def test_an_unrelated_reply_moves_on_and_is_answered() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(tool_call("home_lock_unlock", _LOCK), "Sunny, sir.")
    engine = _engine(redis, script, router, notifier, approval=_classifier("other"))
    approval_id = await _park(engine)

    response = await engine.process_request(_request("what's the weather?"))

    assert script.call_count == 2
    assert script.returns(1) == ["Not confirmed; the user moved on."]
    assert script.prompt(1) == "what's the weather?"
    router.route.assert_not_awaited()
    assert response.text == "Sunny, sir."
    assert await read_pending(redis, approval_id) is None
    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    assert session.pending_approval is None


@pytest.mark.asyncio
async def test_without_a_classifier_a_reply_moves_on() -> None:
    """Ruling P3: with no classifier configured, every reply reads as "other". The record
    is still claimed, and the run hears that the owner moved on: nothing runs."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(tool_call("home_lock_unlock", _LOCK), "Of course, sir.")
    engine = _engine(redis, script, router, notifier)
    approval_id = await _park(engine)

    await engine.process_request(_request(_YES))

    assert script.returns(1) == ["Not confirmed; the user moved on."]
    assert script.prompt(1) == _YES
    router.route.assert_not_awaited()
    assert await read_pending(redis, approval_id) is None


@pytest.mark.asyncio
async def test_a_guest_reply_never_classifies() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router, notifier, approval = _router(), AsyncMock(), _classifier("approve")
    script = Script(tool_call("home_lock_unlock", _LOCK), "Good evening.")
    engine = _engine(redis, script, router, notifier, approval=approval)
    approval_id = await _park(engine)
    record = await read_pending(redis, approval_id)

    response = await engine.process_request(_request(_YES, who="guest"))

    approval.classify.assert_not_awaited()
    assert await read_pending(redis, approval_id) == record
    router.route.assert_not_awaited()
    assert response.text == "Good evening."


@pytest.mark.asyncio
async def test_a_guest_never_classifies_even_an_approval_it_can_read() -> None:
    """The engine's own guard, not only the session's: a guest turn that reads a pending
    approval (a session stored under the guest's name with the field set) still never
    reaches the classifier."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier, approval = _router(), AsyncMock(), _classifier("approve")
    script = Script(tool_call("home_lock_unlock", _LOCK), "Good evening.")
    engine = _engine(redis, script, router, notifier, approval=approval)
    approval_id = await _park(engine)
    record = await read_pending(redis, approval_id)
    sessions = SessionManager(redis)
    await sessions.store_run("sess-1", [], "guest", pending_approval=approval_id)
    assert (await sessions.load("sess-1", "web_pwa", "guest")).pending_approval == approval_id

    await engine.process_request(_request(_YES, who="guest"))

    approval.classify.assert_not_awaited()
    assert await read_pending(redis, approval_id) == record
    router.route.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_run_parked_on_another_session_is_not_resumed() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router, notifier, approval = _router(), AsyncMock(), _classifier("approve")
    script = Script(tool_call("home_lock_unlock", _LOCK), "Of course, sir.")
    engine = _engine(redis, script, router, notifier, approval=approval)
    approval_id = await _park(engine, _request(session_id="sess-2"))
    record = await read_pending(redis, approval_id)
    await SessionManager(redis).store_run("sess-1", [], "sir", pending_approval=approval_id)

    await engine.process_request(_request(_YES))

    approval.classify.assert_not_awaited()
    assert await read_pending(redis, approval_id) == record
    router.route.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_door_decision_first_leaves_the_chat_yes_a_normal_turn() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router, notifier, approval = _router(), AsyncMock(), _classifier("approve")
    script = Script(tool_call("home_lock_unlock", _LOCK), "Of course, sir.")
    engine = _engine(redis, script, router, notifier, approval=approval)
    approval_id = await _park(engine)
    record = await claim_pending(redis, approval_id)  # the Door's tap
    assert isinstance(record, ParkedRun)

    response = await engine.process_request(_request(_YES))

    approval.classify.assert_not_awaited()
    router.route.assert_not_awaited()
    assert response.text == "Of course, sir."
    # No results: the open call was closed as interrupted, not answered.
    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    [returned] = _returns_for(session.messages, record.calls[0].tool_call_id)
    assert _synthesized(returned)
    assert session.pending_approval is None


@pytest.mark.asyncio
async def test_a_door_decision_during_classification_leaves_a_normal_turn() -> None:
    """The reply is classified before the record is claimed, so the Door can take it in
    between; the claim then finds nothing, and the "yes" runs nothing."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(tool_call("home_lock_unlock", _LOCK), "Of course, sir.")
    approval = _classifier("approve")
    engine = _engine(redis, script, router, notifier, approval=approval)
    approval_id = await _park(engine)

    async def door_taps_first(calls: list[ParkedCall], reply: str) -> Decision:
        assert await claim_pending(redis, approval_id) is not None
        return "approve"

    approval.classify.side_effect = door_taps_first

    response = await engine.process_request(_request(_YES))

    approval.classify.assert_awaited_once()
    router.route.assert_not_awaited()
    assert response.text == "Of course, sir."
    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    [call] = [
        p
        for m in session.messages
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, ToolCallPart)
    ]
    [returned] = _returns_for(session.messages, call.tool_call_id)
    assert _synthesized(returned)


@pytest.mark.asyncio
async def test_a_yes_after_expiry_runs_nothing_unapproved() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router, notifier, approval = _router(), AsyncMock(), _classifier("approve")
    script = Script(tool_call("home_lock_unlock", _LOCK), "That request has lapsed, sir.")
    engine = _engine(redis, script, router, notifier, approval=approval)
    approval_id = await _park(engine)
    await redis.delete(pending_key(approval_id))  # the TTL lapsed

    response = await engine.process_request(_request(_YES))

    approval.classify.assert_not_awaited()
    router.route.assert_not_awaited()
    assert response.text == "That request has lapsed, sir."
    # A normal turn over the parked history.
    assert script.prompt(1) == _YES
    first = script.requests[1][0][0]
    assert isinstance(first, ModelRequest)
    assert first.parts[0].content == "let the dog walker in"  # type: ignore[union-attr]
    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    assert session.pending_approval is None


@pytest.mark.asyncio
async def test_a_searched_critical_tool_resumes() -> None:
    """The parked run found the critical tool through ``search_tools``; the resumed run
    re-derives found tools from its history, so the approved call is still callable."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    tools = [_info("home.lock_unlock", risk="critical", audience="conscious")]
    script = Script(
        tool_call("search_tools", {"queries": ["lock unlock"]}),
        tool_call("home_lock_unlock", _LOCK),
        "The door is unlocked, sir.",
    )
    engine = _engine(redis, script, router, notifier, approval=_classifier("approve"), tools=tools)
    approval_id = await _park(engine)
    assert "home_lock_unlock" not in script.offered(0)
    assert isinstance(await read_pending(redis, approval_id), ParkedRun)

    response = await engine.process_request(_request(_YES))

    router.route.assert_awaited_once()
    action = router.route.await_args.args[0]
    assert (action.tool_name, action.confirmed) == ("home.lock_unlock", True)
    assert response.text == "The door is unlocked, sir."
    assert script.call_count == 3  # no second search


@pytest.mark.asyncio
async def test_a_resumed_run_that_parks_again_gets_a_new_approval() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    tools = [*_TOOLS, _info("home.garage_door_open", risk="critical")]
    script = Script(
        tool_call("home_lock_unlock", _LOCK),
        tool_call("home_garage_door_open", {"entity_id": "cover.garage"}),
    )
    engine = _engine(redis, script, router, notifier, approval=_classifier("approve"), tools=tools)
    first_id = await _park(engine)
    reply = _request(_YES)

    response = await engine.process_request(reply)

    router.route.assert_awaited_once()
    assert router.route.await_args.args[0].tool_name == "home.lock_unlock"
    second_id = approval_id_for(reply.event_id)
    assert second_id != first_id
    assert await read_pending(redis, first_id) is None
    record = await read_pending(redis, second_id)
    assert isinstance(record, ParkedRun)
    assert [call.tool_name for call in record.calls] == ["home.garage_door_open"]
    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    assert session.pending_approval == second_id
    assert response.text == confirmation_reply("garage door open (entity_id=cover.garage)")
    assert response.actions_taken == ["home.lock_unlock"]
    assert notifier.publish.await_count == 2
    assert notifier.publish.await_args.kwargs["metadata"]["pending_action_id"] == second_id


@pytest.mark.asyncio
async def test_a_yes_resumes_from_the_parked_record_after_a_ledger_turn() -> None:
    """Ruling P12: a redelivered parked turn answered from its ledger appends a text turn
    after the open call and keeps the approval. The resume continues the claimed record's
    history, which ends in the open call, so the approved call still runs."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(tool_call("home_lock_unlock", _LOCK), "The door is unlocked, sir.")
    engine = _engine(redis, script, router, notifier, approval=_classifier("approve"))
    request = _request()
    approval_id = await _park(engine, request)
    sessions = SessionManager(redis)
    await sessions.append_text_turn(
        "sess-1", "sir", request.content, "I've asked for your confirmation before: lock unlock."
    )
    session = await sessions.load("sess-1", "web_pwa", "sir")
    assert session.pending_approval == approval_id
    assert not any(isinstance(p, ToolCallPart) for p in session.messages[-1].parts)

    response = await engine.process_request(_request(_YES))

    router.route.assert_awaited_once()
    assert router.route.await_args.args[0].confirmed is True
    assert response.text == "The door is unlocked, sir."
    assert await read_pending(redis, approval_id) is None


# ── what goes wrong around a reply ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unreadable_approval_leaves_a_normal_turn() -> None:
    """Ruling P15: a record that no longer parses is not claimable, so the owner's turn runs
    as a normal one instead of failing on every delivery until the record expires."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier, approval = _router(), AsyncMock(), _classifier("approve")
    script = Script(tool_call("home_lock_unlock", _LOCK), "Of course, sir.")
    engine = _engine(redis, script, router, notifier, approval=approval)
    approval_id = await _park(engine)
    await redis.set(pending_key(approval_id), b"not a pending record", ex=300)

    response = await engine.process_request(_request(_YES))

    assert response.text == "Of course, sir."
    approval.classify.assert_not_awaited()
    router.route.assert_not_awaited()
    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    assert session.pending_approval is None


@pytest.mark.asyncio
async def test_an_approval_whose_history_no_longer_reads_stays_unclaimed() -> None:
    """The record's history is read before the claim, so one that no longer decodes leaves
    the approval where it was and the turn normal, rather than consuming it and failing."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier, approval = _router(), AsyncMock(), _classifier("approve")
    script = Script(tool_call("home_lock_unlock", _LOCK), "Of course, sir.")
    engine = _engine(redis, script, router, notifier, approval=approval)
    approval_id = await _park(engine)
    record = await read_pending(redis, approval_id)
    assert isinstance(record, ParkedRun)
    broken = record.model_copy(update={"messages": '[{"kind": "no such message"}]'})
    await redis.set(pending_key(approval_id), broken.model_dump_json(), ex=300)

    response = await engine.process_request(_request(_YES))

    assert response.text == "Of course, sir."
    approval.classify.assert_not_awaited()
    router.route.assert_not_awaited()
    assert await read_pending(redis, approval_id) == broken
    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    assert session.pending_approval is None


@pytest.mark.asyncio
async def test_a_record_changed_under_the_reply_runs_nothing() -> None:
    """Defence in depth: the claim takes a record other than the one the reply was read
    against, so nothing runs for it; it is consumed, which fails closed."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(tool_call("home_lock_unlock", _LOCK), "Of course, sir.")
    approval = _classifier("approve")
    engine = _engine(redis, script, router, notifier, approval=approval)
    approval_id = await _park(engine)

    async def swapped(calls: list[ParkedCall], reply: str) -> Decision:
        record = await claim_pending(redis, approval_id)
        assert isinstance(record, ParkedRun)
        assert await park_run(redis, record.model_copy(update={"summary": "something else"}))
        return "approve"

    approval.classify.side_effect = swapped

    response = await engine.process_request(_request(_YES))

    approval.classify.assert_awaited_once()
    router.route.assert_not_awaited()
    assert response.text == "Of course, sir."
    assert await read_pending(redis, approval_id) is None


@pytest.mark.asyncio
async def test_a_classifier_outage_leaves_the_approval_for_the_retry() -> None:
    """A provider failure in the classifier raises before the claim: the turn is retried
    and the owner's yes is still there to be read."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(tool_call("home_lock_unlock", _LOCK))
    approval = _classifier("approve")
    approval.classify.side_effect = ModelAPIError("m", "connection reset")
    engine = _engine(redis, script, router, notifier, approval=approval)
    approval_id = await _park(engine)
    record = await read_pending(redis, approval_id)

    with pytest.raises(ModelAPIError):
        await engine.process_request(_request(_YES))

    assert await read_pending(redis, approval_id) == record
    router.route.assert_not_awaited()
    assert script.call_count == 1
    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    assert session.pending_approval == approval_id


@pytest.mark.asyncio
async def test_a_budget_spent_mid_resume_says_what_was_done() -> None:
    """The classifier's call (or another process) spends the day after the turn's budget
    check: the approved call runs, the resumed run's model request is refused, and the
    budget reply says what was done. The approval is spent; nothing ran unapproved."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(tool_call("home_lock_unlock", _LOCK), "never sent")
    approval = _classifier("approve")

    async def spends_the_day(calls: list[ParkedCall], reply: str) -> Decision:
        spend = build_spend_limits(redis, cap_usd=5.0, clock=local_clock(lambda: "UTC"))
        (status,) = await spend.status()
        await spend.store.add_many(  # type: ignore[union-attr]
            [SpendEntry(key=status.key, usd=Decimal(6), requests=1)]
        )
        return "approve"

    approval.classify.side_effect = spends_the_day
    engine = _engine(redis, script, router, notifier, approval=approval)
    approval_id = await _park(engine)
    reply = _request(_YES)

    response = await engine.process_request(reply)

    assert script.call_count == 1
    assert [call.args[0].confirmed for call in router.route.await_args_list] == [True]
    effects = await EffectLedger(redis).read(reply.event_id)
    assert response.text == f"{ledger_reply(effects)} {BUDGET_AFTER_ACTING}"
    assert response.mood == "concerned"
    assert await read_pending(redis, approval_id) is None
    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    assert session.pending_approval is None


# ── the owner's decision from the Door ───────────────────────────────────────


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _door(
    redis: fakeredis.FakeAsyncRedis, approval_id: str, decision: Literal["approve", "deny"]
) -> ApprovalDecision:
    """The Door's tap: ``decide`` claims the record and publishes the decision, which is
    returned as the conscious process reads it."""
    assert await decide(redis, approval_id, decision)
    [(_id, fields)] = await forward_range(redis, APPROVALS_STREAM)
    return ApprovalDecision.model_validate_json(fields[b"event"])


def _tail(messages: list[Any]) -> list[tuple[type, str]]:
    """The last two messages as (type, text): a text turn's user words and reply."""
    return [(type(m), m.parts[0].content) for m in messages[-2:]]


@pytest.mark.asyncio
async def test_a_door_approval_runs_under_the_approval_id() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(tool_call("home_lock_unlock", _LOCK), "The door is unlocked, sir.")
    engine = _engine(redis, script, router, notifier)
    request = _request()
    approval_id = await _park(engine, request)
    decision = await _door(redis, approval_id, "approve")

    response = await engine.resume_approval(decision, received_ms=_now_ms())

    router.route.assert_awaited_once()
    action = router.route.await_args.args[0]
    assert (action.tool_name, action.parameters, action.confirmed) == (
        "home.lock_unlock",
        {"entity_id": "lock.front"},
        True,
    )
    assert script.call_count == 2
    assert script.outcomes(1) == ["success"]
    # The Door says nothing: the resumed run carries the call's result and no new prompt.
    resumed = script.requests[1][0][-1]
    assert isinstance(resumed, ModelRequest)
    assert not any(isinstance(p, UserPromptPart) for p in resumed.parts)
    assert response.text == "The door is unlocked, sir."
    assert response.actions_taken == ["home.lock_unlock"]
    assert (response.followup, response.in_reply_to) == (True, request.event_id)
    assert (response.source, response.channel, response.session_id) == (
        "conscious-engine",
        "web_pwa",
        "sess-1",
    )
    effects = await EffectLedger(redis).read(approval_id)
    assert [(effect.tool, effect.status) for effect in effects] == [("home.lock_unlock", "ok")]
    [(_id, fields)] = await forward_range(redis, HOME_ACTION_RESULTS_STREAM)
    assert json.loads(fields[b"event"])["request_id"] == approval_id
    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    assert session.pending_approval is None
    [returned] = _returns_for(session.messages, decision.run.calls[0].tool_call_id)
    assert returned.outcome == "success"
    assert not _synthesized(returned)
    assert session.messages[-1].parts == [TextPart("The door is unlocked, sir.")]
    notifier.publish.assert_awaited_once()  # the park's; nothing new to ask


@pytest.mark.asyncio
async def test_a_door_decision_past_the_window_is_refused() -> None:
    """The replay guard measures from the owner's tap: a decision read five minutes
    later never unlocks the door, and the model is told why."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(tool_call("home_lock_unlock", _LOCK), "It was too late to act, sir.")
    engine = _engine(redis, script, router, notifier)
    approval_id = await _park(engine)
    decision = await _door(redis, approval_id, "approve")

    response = await engine.resume_approval(decision, received_ms=_now_ms() - 301_000)

    router.route.assert_not_awaited()
    assert script.outcomes(1) == ["failed"]
    [refusal] = script.returns(1)
    assert "home.lock_unlock was not run" in refusal
    assert response.text == "It was too late to act, sir."
    assert response.followup is True
    assert await EffectLedger(redis).read(approval_id) == []


@pytest.mark.asyncio
async def test_a_door_denial_is_acknowledged() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(tool_call("home_lock_unlock", _LOCK), "Very well, sir. It stays locked.")
    engine = _engine(redis, script, router, notifier)
    request = _request()
    approval_id = await _park(engine, request)
    decision = await _door(redis, approval_id, "deny")

    response = await engine.resume_approval(decision, received_ms=_now_ms())

    assert script.returns(1) == ["The user declined."]
    router.route.assert_not_awaited()
    assert response.text == "Very well, sir. It stays locked."
    assert response.actions_taken == []
    assert (response.followup, response.in_reply_to) == (True, request.event_id)
    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    assert session.pending_approval is None


@pytest.mark.asyncio
async def test_a_door_approval_that_parks_again_asks_again() -> None:
    """Approval is per call: a critical call the resumed run makes defers again, under a
    new approval the session now waits on."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    tools = [*_TOOLS, _info("home.garage_door_open", risk="critical")]
    script = Script(
        tool_call("home_lock_unlock", _LOCK),
        tool_call("home_garage_door_open", {"entity_id": "cover.garage"}),
    )
    engine = _engine(redis, script, router, notifier, tools=tools)
    request = _request()
    first_id = await _park(engine, request)
    decision = await _door(redis, first_id, "approve")

    response = await engine.resume_approval(decision, received_ms=_now_ms())

    router.route.assert_awaited_once()
    assert router.route.await_args.args[0].tool_name == "home.lock_unlock"
    second_id = approval_id_for(first_id)
    record = await read_pending(redis, second_id)
    assert isinstance(record, ParkedRun)
    assert [call.tool_name for call in record.calls] == ["home.garage_door_open"]
    assert record.request_event_id == request.event_id
    assert response.text == confirmation_reply("garage door open (entity_id=cover.garage)")
    assert response.actions_taken == ["home.lock_unlock"]
    assert (response.followup, response.in_reply_to) == (True, request.event_id)
    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    assert session.pending_approval == second_id
    # The session holds the re-parked run, ending in the call the new approval decides.
    assert len(session.messages) == len(ModelMessagesTypeAdapter.validate_json(record.messages))
    [open_call] = [p for p in session.messages[-1].parts if isinstance(p, ToolCallPart)]
    assert open_call.tool_call_id == record.calls[0].tool_call_id
    assert notifier.publish.await_count == 2
    assert notifier.publish.await_args.kwargs["metadata"]["pending_action_id"] == second_id


@pytest.mark.asyncio
async def test_a_door_decision_after_another_turn_keeps_that_turn() -> None:
    """Ruling P13: a turn between the park and the decision closed the open call, stored
    its own history and cleared the approval. The resume adds its answer to that history
    as text rather than replacing it."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(
        tool_call("home_lock_unlock", _LOCK), "Sunny, sir.", "The door is unlocked, sir."
    )
    engine = _engine(redis, script, router, notifier)
    sessions = SessionManager(redis)
    approval_id = await _park(engine)
    decision = await _door(redis, approval_id, "approve")
    await engine.process_request(_request("what's the weather?"))
    between = (await sessions.load("sess-1", "web_pwa", "sir")).messages

    response = await engine.resume_approval(decision, received_ms=_now_ms())

    router.route.assert_awaited_once()
    assert response.text == "The door is unlocked, sir."
    session = await sessions.load("sess-1", "web_pwa", "sir")
    assert session.messages[: len(between)] == between
    assert len(session.messages) == len(between) + 2
    assert _tail(session.messages) == [
        (ModelRequest, APPROVED_MARKER),
        (ModelResponse, "The door is unlocked, sir."),
    ]
    assert session.pending_approval is None


@pytest.mark.asyncio
async def test_a_door_decision_after_another_turn_parks_again_without_the_session() -> None:
    """Ruling P13: after an intervening turn a re-park leaves the session's approval unset;
    the new record is still answerable from the Door."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    tools = [*_TOOLS, _info("home.garage_door_open", risk="critical")]
    script = Script(
        tool_call("home_lock_unlock", _LOCK),
        "Sunny, sir.",
        tool_call("home_garage_door_open", {"entity_id": "cover.garage"}),
    )
    engine = _engine(redis, script, router, notifier, tools=tools)
    sessions = SessionManager(redis)
    approval_id = await _park(engine)
    decision = await _door(redis, approval_id, "approve")
    await engine.process_request(_request("what's the weather?"))
    between = (await sessions.load("sess-1", "web_pwa", "sir")).messages

    response = await engine.resume_approval(decision, received_ms=_now_ms())

    assert response.text == confirmation_reply("garage door open (entity_id=cover.garage)")
    assert isinstance(await read_pending(redis, approval_id_for(approval_id)), ParkedRun)
    session = await sessions.load("sess-1", "web_pwa", "sir")
    assert session.pending_approval is None
    assert session.messages[: len(between)] == between
    assert _tail(session.messages) == [
        (ModelRequest, APPROVED_MARKER),
        (ModelResponse, confirmation_reply("garage door open (entity_id=cover.garage)")),
    ]


@pytest.mark.asyncio
async def test_a_door_decision_after_a_turn_that_parked_keeps_that_approval() -> None:
    """Ruling P13: a turn between the Door's claim and the resume parked an approval of
    its own. The session goes on waiting on that one, with its history; the resume only
    adds its answer as text."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    tools = [*_TOOLS, _info("home.garage_door_open", risk="critical")]
    script = Script(
        tool_call("home_lock_unlock", _LOCK),
        tool_call("home_garage_door_open", {"entity_id": "cover.garage"}),
        "The door is unlocked, sir.",
    )
    engine = _engine(redis, script, router, notifier, tools=tools)
    sessions = SessionManager(redis)
    first_id = await _park(engine)
    decision = await _door(redis, first_id, "approve")
    garage = _request("open the garage")
    await engine.process_request(garage)
    second_id = approval_id_for(garage.event_id)
    between = await sessions.load("sess-1", "web_pwa", "sir")
    assert between.pending_approval == second_id

    response = await engine.resume_approval(decision, received_ms=_now_ms())

    router.route.assert_awaited_once()
    assert router.route.await_args.args[0].tool_name == "home.lock_unlock"
    assert response.text == "The door is unlocked, sir."
    session = await sessions.load("sess-1", "web_pwa", "sir")
    assert session.pending_approval == second_id
    assert session.messages[: len(between.messages)] == between.messages
    assert len(session.messages) == len(between.messages) + 2
    assert _tail(session.messages) == [
        (ModelRequest, APPROVED_MARKER),
        (ModelResponse, "The door is unlocked, sir."),
    ]
    assert isinstance(await read_pending(redis, second_id), ParkedRun)


@pytest.mark.asyncio
async def test_a_door_resume_without_its_own_answer_stores_the_marker() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(tool_call("home_lock_unlock", _LOCK), "", "", "")
    engine = _engine(redis, script, router, notifier)
    approval_id = await _park(engine)
    decision = await _door(redis, approval_id, "deny")
    parked = ModelMessagesTypeAdapter.validate_json(decision.run.messages)

    response = await engine.resume_approval(decision, received_ms=_now_ms())

    assert response.text == DELIBERATING_REPLY
    assert response.followup is True
    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    assert len(session.messages) == len(parked) + 2
    assert _tail(session.messages) == [
        (ModelRequest, DECLINED_MARKER),
        (ModelResponse, DELIBERATING_REPLY),
    ]
    assert session.pending_approval is None


@pytest.mark.asyncio
async def test_a_door_decision_on_a_spent_budget_gets_the_budget_reply() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(tool_call("home_lock_unlock", _LOCK), "never sent")
    engine = _engine(redis, script, router, notifier)
    request = _request()
    approval_id = await _park(engine, request)
    decision = await _door(redis, approval_id, "approve")
    spend = build_spend_limits(redis, cap_usd=5.0, clock=local_clock(lambda: "UTC"))
    (status,) = await spend.status()
    await spend.store.add_many(  # type: ignore[union-attr]
        [SpendEntry(key=status.key, usd=Decimal(6), requests=1)]
    )

    response = await engine.resume_approval(decision, received_ms=_now_ms())

    assert response.text == BUDGET_REPLY
    assert (response.followup, response.in_reply_to, response.mood) == (
        True,
        request.event_id,
        "concerned",
    )
    assert script.call_count == 1
    router.route.assert_not_awaited()


async def _pending_decision(redis: fakeredis.FakeAsyncRedis, decision: ApprovalDecision) -> bytes:
    await redis.xgroup_create(APPROVALS_STREAM, _GROUP, id="0", mkstream=True)
    entry_id: bytes = await redis.xadd(APPROVALS_STREAM, {"event": decision.model_dump_json()})
    await redis.xreadgroup(_GROUP, _CONSUMER, {APPROVALS_STREAM: ">"})
    return entry_id


async def _deliver_decision(
    redis: fakeredis.FakeAsyncRedis, entry_id: bytes, engine: Any, delivery: int
) -> None:
    eid = entry_id.decode()
    [(_id, fields)] = await forward_range(redis, APPROVALS_STREAM, min_id=eid, max_id=eid)
    await process_approval_entry(
        entry_id,
        fields,
        engine=engine,
        redis=redis,
        stream=APPROVALS_STREAM,
        group=_GROUP,
        consumer=_CONSUMER,
        delivery=delivery,
    )


async def _approval_responses(redis: fakeredis.FakeAsyncRedis) -> list[AlfredResponse]:
    return [
        AlfredResponse.model_validate_json(fields[b"event"])
        for _id, fields in await forward_range(redis, USER_RESPONSES_STREAM)
    ]


@pytest.mark.asyncio
async def test_a_door_decision_whose_history_no_longer_reads_fails_once() -> None:
    """Rulings P17/P18: the Door already claimed the record, so a retry cannot help. The
    decision is answered once, leaves the stream and is dead-lettered; nothing runs."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(tool_call("home_lock_unlock", _LOCK))
    engine = _engine(redis, script, router, notifier)
    request = _request()
    approval_id = await _park(engine, request)
    record = await claim_pending(redis, approval_id)
    assert isinstance(record, ParkedRun)
    broken = record.model_copy(update={"messages": '[{"kind": "no such message"}]'})
    decision = ApprovalDecision(
        source="channels", approval_id=approval_id, decision="approve", run=broken
    )
    entry_id = await _pending_decision(redis, decision)

    await _deliver_decision(redis, entry_id, engine, delivery=1)

    [reply] = await _approval_responses(redis)
    assert reply.text == FAILED_REPLY
    assert (reply.followup, reply.in_reply_to, reply.mood) == (
        True,
        request.event_id,
        "concerned",
    )
    assert int((await redis.xpending(APPROVALS_STREAM, _GROUP))["pending"]) == 0
    [(_id, letter)] = await forward_range(redis, DEAD_LETTER_STREAM)
    assert (letter[b"reason"], letter[b"error_type"]) == (b"permanent", b"UserError")
    assert ApprovalDecision.model_validate_json(letter[b"event"]) == decision
    assert script.call_count == 1
    router.route.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_door_approval_through_the_loop_never_runs_twice() -> None:
    """The approved call ran and the answer was lost: the redelivered decision answers
    from the approval's ledger, and the door is unlocked once."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(tool_call("home_lock_unlock", _LOCK), "The door is unlocked, sir.")
    engine = _engine(redis, script, router, notifier)
    request = _request()
    approval_id = await _park(engine, request)
    await redis.xgroup_create(APPROVALS_STREAM, _GROUP, id="0", mkstream=True)
    await _door(redis, approval_id, "approve")
    [[_stream, [(entry_id, _fields)]]] = await redis.xreadgroup(
        _GROUP, _CONSUMER, {APPROVALS_STREAM: ">"}
    )
    _first_publish_is_lost(redis)

    await _deliver_decision(redis, entry_id, engine, delivery=1)
    assert await forward_range(redis, USER_RESPONSES_STREAM) == []
    await _deliver_decision(redis, entry_id, engine, delivery=2)

    router.route.assert_awaited_once()
    assert script.call_count == 2
    [reply] = await _approval_responses(redis)
    effects = await EffectLedger(redis).read(approval_id)
    assert reply.text == ledger_reply(effects)
    assert (reply.followup, reply.in_reply_to) == (True, request.event_id)
    assert await redis.exists(ANSWERED_KEY.format(approval_id))
    assert int((await redis.xpending(APPROVALS_STREAM, _GROUP))["pending"]) == 0
    session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")
    assert _tail(session.messages) == [
        (ModelRequest, APPROVED_MARKER),
        (ModelResponse, ledger_reply(effects)),
    ]
