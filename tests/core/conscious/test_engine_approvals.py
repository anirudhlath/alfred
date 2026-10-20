"""A turn whose critical call waits for the owner: the run is parked, the owner is asked.

System 2 can no longer confirm its own critical action (#317 PR 5): the approval gate
defers the call, the engine parks the run in the pending store, notifies the owner and
replies with a fixed template. A redelivery answers from the side-effect ledger.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)

from bus.schemas.events import ParkedCall, ParkedRun, UserRequest
from core.conscious.agent import build_system2_agent, system2_toolsets
from core.conscious.approvals import approval_id_for, confirmation_reply
from core.conscious.effects import Effect, EffectLedger, ledger_reply
from core.conscious.engine import ConsciousDeps, ConsciousEngine
from core.conscious.runner import process_request_entry
from core.conscious.session import SessionManager, text_turn
from core.notifications.schema import Urgency
from core.reflex.tool_registry import REFLEX_AUDIENCE, ToolInfo
from core.routing.pending import park_run, pending_key, read_pending
from shared.redis_streams import forward_range
from shared.spend import build_spend_limits, local_clock
from shared.streams import USER_REQUESTS_STREAM, USER_RESPONSES_STREAM
from tests.core.conscious.helpers import identity
from tests.llm_fakes import Script, tool_call, tool_calls

_GROUP = "conscious-engine"
_CONSUMER = "worker-1"
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


_TOOLS = [_info("home.light_turn_on"), _info("home.lock_unlock", risk="critical")]


def _router() -> AsyncMock:
    router = AsyncMock()
    router.route.return_value = MagicMock(status="success", result={"ok": True}, error=None)
    return router


def _request(content: str = "let the dog walker in") -> UserRequest:
    return UserRequest(
        source="web-pwa",
        channel="web_pwa",
        session_id="sess-1",
        identity_claim="sir",
        authenticated=True,
        content_type="text",
        content=content,
    )


def _engine(
    redis: fakeredis.FakeAsyncRedis, script: Script, router: AsyncMock, notifier: AsyncMock
) -> ConsciousEngine:
    """A real engine, System 2 agent and session store over fakeredis."""
    identity_gate = MagicMock()
    identity_gate.resolve.return_value = identity("sir")
    registry = AsyncMock()
    registry.get_tools.return_value = _TOOLS
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
    assert response.text == confirmation_reply("The dog walker is here")
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
    Pydantic AI refuses a new prompt after one. The next turn that does not resume closes
    it as interrupted, and nothing runs."""
    redis = fakeredis.FakeAsyncRedis()
    router, notifier = _router(), AsyncMock()
    script = Script(tool_call("home_lock_unlock", _LOCK), "Sunny, sir.")
    engine = _engine(redis, script, router, notifier)
    await engine.process_request(_request())

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
    assert response.text == confirmation_reply("lock unlock")
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
        summary="garage door open",
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

    assert response.text == confirmation_reply("garage door open")
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
