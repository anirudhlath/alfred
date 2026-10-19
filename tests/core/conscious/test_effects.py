"""The side-effect ledger (#304): a turn that already acted answers from it, never twice.

Spec 6 ("A failed turn answers once"): every side-effecting tool call is recorded
before it runs and its outcome after, keyed by the request's ``event_id``. A turn that
fails after acting, or is redelivered after a crash, answers from the ledger with a
fixed template instead of running again.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError

from bus.schemas.events import ActionRequest, UserRequest
from core.conscious.agent import build_system2_agent, system2_toolsets
from core.conscious.deps import TurnDeps
from core.conscious.effects import (
    EFFECTS_TTL_S,
    Effect,
    EffectLedger,
    RecordEffects,
    ledger_reply,
)
from core.conscious.engine import ConsciousDeps, ConsciousEngine
from core.conscious.runner import process_request_entry
from core.conscious.session import SessionManager, stored_messages
from core.conscious.tools.attention import attention_toolset
from core.conscious.tools.catalog import CatalogToolset
from core.conscious.tools.integrations import IntegrationTool, IntegrationToolset
from core.conscious.tools.registry import RegistryToolset
from core.conscious.tools.triggers import TriggerToolset
from core.integrations.base import IntegrationResult
from core.reflex.tool_registry import REFLEX_AUDIENCE, ToolInfo
from core.routing.domain_router import CONFIRMATION_REQUIRED_PREFIX
from core.routing.pending import store_pending_action
from core.triggers.feature import TriggerFeature, TriggerFeatureContext
from core.triggers.store import TriggerStore
from shared.redis_streams import MAX_REPLAY_AGE_MS, forward_range
from shared.spend import build_spend_limits, local_clock
from shared.streams import (
    DEAD_LETTER_STREAM,
    EFFECTS_KEY,
    SESSIONS_KEY_PREFIX,
    TOOL_REGISTRY_KEY,
    USER_REQUESTS_STREAM,
    USER_RESPONSES_STREAM,
)
from tests.core.conscious.helpers import identity, make_deps, make_services
from tests.llm_fakes import Script, tool_call, tool_calls

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelResponse

_GROUP = "conscious-engine"
_CONSUMER = "worker-1"


# ── the ledger ───────────────────────────────────────────────────────────────


async def test_the_ledger_round_trips_and_expires() -> None:
    redis = fakeredis.FakeAsyncRedis()
    ledger = EffectLedger(redis)

    first = await ledger.started("req-1", "home.light_turn_on", {"entity_id": "light.porch"})
    second = await ledger.started("req-1", "home.lock_unlock", {"entity_id": "lock.front"})
    await ledger.finished("req-1", first, ok=True)
    await ledger.finished("req-1", second, ok=False)

    assert (first, second) == (0, 1)
    assert await ledger.read("req-1") == [
        Effect("home.light_turn_on", {"entity_id": "light.porch"}, "ok"),
        Effect("home.lock_unlock", {"entity_id": "lock.front"}, "failed"),
    ]
    assert 0 < await redis.ttl(EFFECTS_KEY.format("req-1")) <= EFFECTS_TTL_S
    assert EFFECTS_TTL_S == 3600
    assert await ledger.read("req-2") == []


async def test_an_unfinished_call_reads_as_started() -> None:
    ledger = EffectLedger(fakeredis.FakeAsyncRedis())
    await ledger.started("req-1", "home.lock_unlock", {})
    assert await ledger.read("req-1") == [Effect("home.lock_unlock", {}, "started")]


async def test_a_parked_call_reads_as_parked() -> None:
    ledger = EffectLedger(fakeredis.FakeAsyncRedis())
    index = await ledger.started("req-1", "home.lock_unlock", {})
    await ledger.parked("req-1", index)
    assert await ledger.read("req-1") == [Effect("home.lock_unlock", {}, "parked")]


async def test_started_writes_the_entry_and_its_ttl_in_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = fakeredis.FakeAsyncRedis()
    pipeline = MagicMock(wraps=redis.pipeline)
    monkeypatch.setattr(redis, "pipeline", pipeline)
    monkeypatch.setattr(redis, "rpush", AsyncMock(side_effect=AssertionError("not atomic")))

    await EffectLedger(redis).started("req-1", "home.light_turn_on", {})

    pipeline.assert_called_once_with(transaction=True)
    assert await redis.llen(EFFECTS_KEY.format("req-1")) == 1
    assert 0 < await redis.ttl(EFFECTS_KEY.format("req-1")) <= EFFECTS_TTL_S


async def test_the_ledger_is_stored_as_json_entries() -> None:
    redis = fakeredis.FakeAsyncRedis()
    await EffectLedger(redis).started("req-1", "home.light_turn_on", {"entity_id": "x"})
    [raw] = await redis.lrange(EFFECTS_KEY.format("req-1"), 0, -1)
    assert json.loads(raw) == {
        "tool": "home.light_turn_on",
        "args": {"entity_id": "x"},
        "status": "started",
    }


# ── the reply ────────────────────────────────────────────────────────────────


def test_the_reply_when_every_call_went_through() -> None:
    effects = [
        Effect("home.light_turn_on", {}, "ok"),
        Effect("home.scene_activate", {}, "ok"),
    ]
    assert ledger_reply(effects) == (
        "I'd started on that — I did: light turn on, scene activate — then ran into a "
        "problem before I could finish, sir."
    )


def test_the_reply_separates_done_from_in_doubt() -> None:
    effects = [
        Effect("home.light_turn_on", {}, "ok"),
        Effect("home.lock_unlock", {}, "started"),
    ]
    assert ledger_reply(effects) == (
        "I'd started on that — I did: light turn on — then ran into a problem before I "
        "could finish, sir. I'm not certain these went through: lock unlock. Please check "
        "before asking again."
    )


def test_the_reply_when_nothing_is_known_to_have_gone_through() -> None:
    assert ledger_reply([Effect("home.lock_unlock", {}, "failed")]) == (
        "I ran into a problem partway through, sir. I'm not certain these went through: "
        "lock unlock. Please check before asking again."
    )


_PARKED = " I've asked for your confirmation before: lock unlock."


def test_the_reply_when_the_only_call_was_parked() -> None:
    assert ledger_reply([Effect("home.lock_unlock", {}, "parked")]) == (
        "I ran into a problem partway through, sir." + _PARKED
    )


def test_the_reply_never_counts_a_parked_call_as_done() -> None:
    effects = [
        Effect("home.light_turn_on", {}, "ok"),
        Effect("home.lock_unlock", {}, "parked"),
    ]
    assert ledger_reply(effects) == (
        "I'd started on that — I did: light turn on — then ran into a problem before I "
        "could finish, sir." + _PARKED
    )


def test_the_reply_puts_parked_calls_after_those_in_doubt() -> None:
    effects = [
        Effect("home.lock_unlock", {}, "parked"),
        Effect("home.light_turn_on", {}, "started"),
    ]
    assert ledger_reply(effects) == (
        "I ran into a problem partway through, sir. I'm not certain these went through: "
        "light turn on. Please check before asking again." + _PARKED
    )


def test_a_name_without_a_service_prefix_is_humanised_whole() -> None:
    assert ledger_reply([Effect("attention_add", {}, "ok")]).startswith(
        "I'd started on that — I did: attention add — "
    )


# ── what is recorded ─────────────────────────────────────────────────────────


def _info(name: str, risk: str = "benign", audience: str = REFLEX_AUDIENCE) -> ToolInfo:
    return ToolInfo(
        name=name,
        description=f"{name} does a thing",
        feature_name=name.split(".")[0],
        feature_description="Home control",
        target_service="home-service",
        audience=audience,
        risk=risk,  # type: ignore[arg-type]
        input_schema={
            "type": "object",
            "properties": {"entity_id": {"type": "string"}},
            "required": ["entity_id"],
        },
    )


def _router(status: str = "success", error: str | None = None) -> AsyncMock:
    router = AsyncMock()
    router.route.return_value = MagicMock(status=status, result={"ok": True}, error=error)
    return router


def _recording_agent(script: Script, *toolsets: Any) -> Agent[TurnDeps, str]:
    """System 2's catalog and its ledger capability, over *toolsets*."""
    return Agent(
        script.model,
        deps_type=TurnDeps,
        toolsets=[CatalogToolset(list(toolsets))],
        capabilities=[RecordEffects()],
        retries=2,
    )


def _triggers() -> tuple[TriggerToolset, AsyncMock]:
    store = AsyncMock(spec_set=TriggerStore)
    store.list_all = AsyncMock(return_value=[])
    store.get = AsyncMock(return_value=None)
    return TriggerToolset(TriggerFeature(TriggerFeatureContext(store=store))), store


async def test_a_registry_call_is_recorded_ok() -> None:
    redis = fakeredis.FakeAsyncRedis()
    deps = make_deps(
        services=make_services(redis=redis, router=_router()),
        registry_tools=[_info("home.light_turn_on")],
    )
    script = Script(tool_call("home_light_turn_on", {"entity_id": "light.porch"}), "done")

    await _recording_agent(script, RegistryToolset()).run("go", deps=deps)

    assert await EffectLedger(redis).read("req-1") == [
        Effect("home.light_turn_on", {"entity_id": "light.porch"}, "ok")
    ]


async def test_a_failed_registry_call_is_recorded_failed() -> None:
    redis = fakeredis.FakeAsyncRedis()
    deps = make_deps(
        services=make_services(redis=redis, router=_router("error", "unreachable")),
        registry_tools=[_info("home.lock_unlock")],
    )
    script = Script(tool_call("home_lock_unlock", {"entity_id": "lock.front"}), "sorry")

    await _recording_agent(script, RegistryToolset()).run("go", deps=deps)

    assert [e.status for e in await EffectLedger(redis).read("req-1")] == ["failed"]


async def test_a_call_parked_for_confirmation_is_recorded_parked() -> None:
    redis = fakeredis.FakeAsyncRedis()
    deps = make_deps(
        services=make_services(
            redis=redis, router=_router("error", f"{CONFIRMATION_REQUIRED_PREFIX}abc")
        ),
        registry_tools=[_info("home.lock_unlock", risk="critical")],
    )
    script = Script(tool_call("home_lock_unlock", {"entity_id": "lock.front"}), "asked")

    await _recording_agent(script, RegistryToolset()).run("go", deps=deps)

    assert [e.status for e in await EffectLedger(redis).read("req-1")] == ["parked"]


async def test_a_call_with_bad_arguments_is_not_recorded() -> None:
    """A schema retry never reaches the tool, so it is not a side effect."""
    redis = fakeredis.FakeAsyncRedis()
    router = _router()
    deps = make_deps(
        services=make_services(redis=redis, router=router),
        registry_tools=[_info("home.light_turn_on")],
    )
    script = Script(tool_call("home_light_turn_on", {"colour": "red"}), "gave up")

    await _recording_agent(script, RegistryToolset()).run("go", deps=deps)

    router.route.assert_not_awaited()
    assert await EffectLedger(redis).read("req-1") == []


@pytest.mark.parametrize(
    ("name", "args", "recorded"),
    [
        ("triggers_list_triggers", {}, False),
        ("triggers_delete_trigger", {"trigger_id": "t-1"}, True),
        ("triggers_toggle_trigger", {"trigger_id": "t-1", "enabled": False}, True),
        ("triggers_update_trigger", {"trigger_id": "t-1", "name": "x"}, True),
        (
            "triggers_create_trigger",
            {"name": "later", "trigger_type": "time", "conditions": {"run_in_seconds": 60}},
            True,
        ),
    ],
)
async def test_trigger_mutations_are_recorded_and_listing_is_not(
    name: str, args: dict[str, Any], recorded: bool
) -> None:
    redis = fakeredis.FakeAsyncRedis()
    toolset, _ = _triggers()
    script = Script(tool_call(name, args), "ok")

    await _recording_agent(script, toolset).run(
        "go", deps=make_deps(services=make_services(redis=redis))
    )

    ledger = await EffectLedger(redis).read("req-1")
    assert [e.tool for e in ledger] == ([name.replace("_", ".", 1)] if recorded else [])


@pytest.mark.parametrize(
    ("name", "recorded"),
    [
        ("attention_add", True),
        ("attention_remove", True),
        ("attention_list", False),
        ("confirm_pending_action", True),
    ],
)
async def test_attention_changes_are_recorded_and_listing_is_not(name: str, recorded: bool) -> None:
    redis = fakeredis.FakeAsyncRedis()
    args: dict[str, Any] = {"domain": "home"}
    if name == "confirm_pending_action":
        args = {"request_id": "p-1"}
    elif name != "attention_list":
        args["entity_id"] = "sensor.x"
    script = Script(tool_call(name, args), "ok")

    await _recording_agent(script, attention_toolset()).run(
        "go", deps=make_deps(services=make_services(redis=redis))
    )

    assert [e.tool for e in await EffectLedger(redis).read("req-1")] == ([name] if recorded else [])


async def test_a_guest_refused_call_is_not_recorded() -> None:
    """A guest scheduling an elevated action is refused before anything happens."""
    redis = fakeredis.FakeAsyncRedis()
    manifest = {
        "service_name": "home-service",
        "features": [{"name": "home", "tools": [{"name": "home.unlock_door", "risk": "elevated"}]}],
    }
    await redis.hset(TOOL_REGISTRY_KEY, "home-service", json.dumps(manifest))
    toolset, store = _triggers()
    action = {"tool_name": "home.unlock_door", "target_service": "home-service", "parameters": {}}
    args = {
        "name": "later",
        "trigger_type": "time",
        "conditions": {"run_in_seconds": 60},
        "action": action,
    }
    script = Script(tool_call("triggers_create_trigger", args), "no")

    await _recording_agent(script, toolset).run(
        "go", deps=make_deps("guest", services=make_services(redis=redis))
    )

    assert script.outcomes(1) == ["failed"]
    store.save.assert_not_awaited()
    assert await EffectLedger(redis).read("req-1") == []


# ── integration capabilities ────────────────────────────────────────────────


_CREATE_EVENT = IntegrationTool(
    name="integration_apple_calendar_create_event",
    integration="apple_calendar",
    capability="create_event",
    description="[apple_calendar] Create a new calendar event",
    schema={
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    },
)
_GET_UPCOMING = IntegrationTool(
    name="integration_apple_calendar_get_upcoming",
    integration="apple_calendar",
    capability="get_upcoming",
    description="[apple_calendar] Get events for the next N days",
    schema={"type": "object", "properties": {}, "required": []},
    side_effect=False,
)


def _calendar(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    adapter = MagicMock()
    adapter.execute = AsyncMock(
        return_value=IntegrationResult(
            data={"created": True}, freshness=datetime.now(UTC), confidence=1.0
        )
    )
    monkeypatch.setattr(
        "core.conscious.tools.integrations.IntegrationRegistry.get", lambda _name: adapter
    )
    return adapter


@pytest.mark.parametrize(
    ("tool", "args", "recorded"),
    [(_CREATE_EVENT, {"summary": "dentist"}, True), (_GET_UPCOMING, {}, False)],
)
async def test_integration_writes_are_recorded_and_reads_are_not(
    monkeypatch: pytest.MonkeyPatch, tool: IntegrationTool, args: dict[str, Any], recorded: bool
) -> None:
    adapter = _calendar(monkeypatch)
    redis = fakeredis.FakeAsyncRedis()
    deps = make_deps(services=make_services(redis=redis), integration_tools=[tool])
    script = Script(tool_call(tool.name, args), "ok")

    await _recording_agent(script, IntegrationToolset()).run("go", deps=deps)

    adapter.execute.assert_awaited_once()
    expected = [Effect(tool.name, args, "ok")] if recorded else []
    assert await EffectLedger(redis).read("req-1") == expected


# ── the replay window, at the moment of acting ──────────────────────────────


def _now_ms() -> int:
    return int(time.time() * 1000)


async def test_a_side_effect_inside_the_replay_window_acts() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router = _router()
    deps = make_deps(
        services=make_services(redis=redis, router=router),
        registry_tools=[_info("home.lock_unlock")],
        received_ms=_now_ms() - MAX_REPLAY_AGE_MS + 5_000,
    )
    script = Script(tool_call("home_lock_unlock", {"entity_id": "lock.front"}), "done")

    await _recording_agent(script, RegistryToolset()).run("go", deps=deps)

    router.route.assert_awaited_once()
    assert [e.status for e in await EffectLedger(redis).read("req-1")] == ["ok"]


async def test_a_side_effect_past_the_replay_window_is_refused_and_never_acts() -> None:
    """A slow turn that reaches "unlock the door" five minutes after it was asked must not
    unlock it; the model is told why, and nothing is recorded, since nothing happened."""
    redis = fakeredis.FakeAsyncRedis()
    router = _router()
    deps = make_deps(
        services=make_services(redis=redis, router=router),
        registry_tools=[_info("home.lock_unlock")],
        received_ms=_now_ms() - MAX_REPLAY_AGE_MS - 1_000,
    )
    script = Script(tool_call("home_lock_unlock", {"entity_id": "lock.front"}), "sorry")

    await _recording_agent(script, RegistryToolset()).run("go", deps=deps)

    router.route.assert_not_awaited()
    assert script.outcomes(1) == ["failed"]
    assert "more than 5 minutes ago" in script.returns(1)[0]
    assert await EffectLedger(redis).read("req-1") == []
    assert deps.actions_taken == []


async def test_a_read_past_the_replay_window_still_runs() -> None:
    redis = fakeredis.FakeAsyncRedis()
    toolset, store = _triggers()
    deps = make_deps(services=make_services(redis=redis), received_ms=_now_ms() - 3_600_000)
    script = Script(tool_call("triggers_list_triggers", {}), "none")

    await _recording_agent(script, toolset).run("go", deps=deps)

    store.list_all.assert_awaited_once()
    assert script.outcomes(1) != ["failed"]


async def test_a_past_window_integration_write_never_reaches_the_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _calendar(monkeypatch)
    deps = make_deps(
        services=make_services(redis=fakeredis.FakeAsyncRedis()),
        integration_tools=[_CREATE_EVENT],
        received_ms=_now_ms() - MAX_REPLAY_AGE_MS - 1_000,
    )
    script = Script(tool_call(_CREATE_EVENT.name, {"summary": "dentist"}), "sorry")

    await _recording_agent(script, IntegrationToolset()).run("go", deps=deps)

    adapter.execute.assert_not_awaited()
    assert script.outcomes(1) == ["failed"]


# ── #304's acceptance test: a turn that acted, then failed ───────────────────


def _request() -> UserRequest:
    return UserRequest(
        source="web-pwa",
        channel="web_pwa",
        session_id="sess-1",
        identity_claim="sir",
        authenticated=True,
        content_type="text",
        content="turn on the porch light",
    )


def _engine(
    redis: fakeredis.FakeAsyncRedis,
    script: Script,
    router: AsyncMock,
    registry_tools: list[ToolInfo] | None = None,
) -> ConsciousEngine:
    """A real engine and System 2 agent over fakeredis; the registry holds *registry_tools*,
    by default one light tool."""
    identity_gate = MagicMock()
    identity_gate.resolve.return_value = identity("sir")
    registry = AsyncMock()
    registry.get_tools.return_value = (
        registry_tools if registry_tools is not None else [_info("home.light_turn_on")]
    )
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
        )
    )


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


_PORCH_REPLY = (
    "I'd started on that — I did: light turn on — then ran into a problem before I could "
    "finish, sir."
)


@pytest.fixture(autouse=True)
def _no_integrations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.conscious.engine.fetch_integration_tools", AsyncMock(return_value=[]))


async def test_a_turn_that_acted_and_then_failed_answers_from_its_ledger() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router = _router()
    script = Script(
        tool_call("home_light_turn_on", {"entity_id": "light.porch"}), ModelHTTPError(503, "m")
    )
    request = _request()
    entry_id = await _pending(redis, request)

    await _deliver(redis, entry_id, _engine(redis, script, router), delivery=1)

    assert await EffectLedger(redis).read(request.event_id) == [
        Effect("home.light_turn_on", {"entity_id": "light.porch"}, "ok")
    ]
    responses = await forward_range(redis, USER_RESPONSES_STREAM)
    assert [json.loads(f[b"event"])["text"] for _id, f in responses] == [_PORCH_REPLY]
    assert int((await redis.xpending(USER_REQUESTS_STREAM, _GROUP))["pending"]) == 0
    letters = [f[b"reason"] for _id, f in await forward_range(redis, DEAD_LETTER_STREAM)]
    assert letters == [b"partial"]
    stored = stored_messages(await redis.hgetall(f"{SESSIONS_KEY_PREFIX}sess-1"))
    assert [type(m).__name__ for m in stored] == ["ModelRequest", "ModelResponse"]
    assert stored[0].parts[0].content == "turn on the porch light"
    assert stored[1].parts[0].content == _PORCH_REPLY


async def test_a_redelivery_after_a_crash_answers_from_the_ledger_without_running() -> None:
    """The process died mid-turn, after the light went on: the redelivery must not turn it
    on again, nor ask the model anything."""
    redis = fakeredis.FakeAsyncRedis()
    router = _router()
    script = Script(
        tool_call("home_light_turn_on", {"entity_id": "light.porch"}), ModelHTTPError(503, "m")
    )
    request = _request()
    entry_id = await _pending(redis, request)
    engine = _engine(redis, script, router)
    with pytest.raises(ModelHTTPError):
        await engine.process_request(request)  # the crashed delivery: no runner after it
    router.route.assert_awaited_once()
    spy = MagicMock(wraps=engine)
    spy.process_request = AsyncMock()
    spy.record_partial_turn = AsyncMock(wraps=engine.record_partial_turn)

    await _deliver(redis, entry_id, spy, delivery=2)

    spy.process_request.assert_not_awaited()
    router.route.assert_awaited_once()
    assert script.call_count == 2
    responses = await forward_range(redis, USER_RESPONSES_STREAM)
    assert [json.loads(f[b"event"])["text"] for _id, f in responses] == [_PORCH_REPLY]
    assert int((await redis.xpending(USER_REQUESTS_STREAM, _GROUP))["pending"]) == 0


async def test_a_turn_that_confirmed_an_action_and_then_failed_answers_from_its_ledger() -> None:
    """Confirming runs a parked critical action: a turn that did so must not be answered
    as though nothing happened."""
    redis = fakeredis.FakeAsyncRedis()
    action = ActionRequest(
        source="conscious-engine", target_service="home-service", tool_name="home.unlock_door"
    )
    await store_pending_action(redis, action)
    script = Script(
        tool_call("confirm_pending_action", {"request_id": action.request_id}),
        ModelHTTPError(503, "m"),
    )
    request = _request()
    entry_id = await _pending(redis, request)

    await _deliver(redis, entry_id, _engine(redis, script, _router()), delivery=1)

    responses = await forward_range(redis, USER_RESPONSES_STREAM)
    assert [json.loads(f[b"event"])["text"] for _id, f in responses] == [
        "I'd started on that — I did: confirm pending action — then ran into a problem "
        "before I could finish, sir."
    ]
    letters = [f[b"reason"] for _id, f in await forward_range(redis, DEAD_LETTER_STREAM)]
    assert letters == [b"partial"]


async def test_an_integration_write_then_a_transient_failure_is_never_repeated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final review's I1: "book the dentist", the event is created, the next model
    request fails, and the redelivery must not create a second event."""
    monkeypatch.setattr(
        "core.conscious.engine.fetch_integration_tools", AsyncMock(return_value=[_CREATE_EVENT])
    )
    adapter = _calendar(monkeypatch)
    redis = fakeredis.FakeAsyncRedis()
    call = tool_call(_CREATE_EVENT.name, {"summary": "dentist"})
    script = Script(call, ModelHTTPError(503, "m"), call, "Booked, sir.")
    request = _request()
    entry_id = await _pending(redis, request)
    engine = _engine(redis, script, _router())

    await _deliver(redis, entry_id, engine, delivery=1)
    await _deliver(redis, entry_id, engine, delivery=2)

    adapter.execute.assert_awaited_once()
    assert script.call_count == 2
    assert await EffectLedger(redis).read(request.event_id) == [
        Effect(_CREATE_EVENT.name, {"summary": "dentist"}, "ok")
    ]
    responses = await forward_range(redis, USER_RESPONSES_STREAM)
    assert [json.loads(f[b"event"])["text"] for _id, f in responses] == [
        "I'd started on that — I did: integration apple calendar create event — then ran "
        "into a problem before I could finish, sir."
    ]
    assert int((await redis.xpending(USER_REQUESTS_STREAM, _GROUP))["pending"]) == 0


# ── two side effects in one response (the framework runs them concurrently) ──


_VACUUM = "home.vacuum_start"
_LIGHT = "home.light_turn_on"


def _vacuum_and_light() -> list[ToolInfo]:
    """One tool found by search (not a reflex tool) and one that is always offered."""
    return [_info(_VACUUM, audience="conscious"), _info(_LIGHT)]


def _search_for_the_vacuum() -> ModelResponse:
    return tool_call("search_tools", {"queries": ["vacuum start"]})


def _vacuum_and_light_together() -> ModelResponse:
    return tool_calls(
        ("home_vacuum_start", {"entity_id": "vacuum.hall"}),
        ("home_light_turn_on", {"entity_id": "light.porch"}),
    )


async def test_two_side_effects_in_one_response_both_act_and_settle() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router = _router()
    script = Script(_search_for_the_vacuum(), _vacuum_and_light_together(), "Done, sir.")
    request = _request()
    entry_id = await _pending(redis, request)

    await _deliver(redis, entry_id, _engine(redis, script, router, _vacuum_and_light()), delivery=1)

    assert router.route.await_count == 2
    assert {call.args[0].tool_name for call in router.route.await_args_list} == {_VACUUM, _LIGHT}
    assert sorted(await EffectLedger(redis).read(request.event_id), key=lambda e: e.tool) == [
        Effect(_LIGHT, {"entity_id": "light.porch"}, "ok"),
        Effect(_VACUUM, {"entity_id": "vacuum.hall"}, "ok"),
    ]
    [(_id, fields)] = await forward_range(redis, USER_RESPONSES_STREAM)
    response = json.loads(fields[b"event"])
    assert response["text"] == "Done, sir."
    assert sorted(response["actions_taken"]) == [_LIGHT, _VACUUM]
    assert int((await redis.xpending(USER_REQUESTS_STREAM, _GROUP))["pending"]) == 0


async def test_a_redelivery_after_two_concurrent_side_effects_acts_on_neither() -> None:
    """The process died after both calls of one response ran: the redelivery must run
    neither again, nor ask the model anything."""
    redis = fakeredis.FakeAsyncRedis()
    router = _router()
    script = Script(
        _search_for_the_vacuum(), _vacuum_and_light_together(), ModelHTTPError(503, "m")
    )
    request = _request()
    entry_id = await _pending(redis, request)
    engine = _engine(redis, script, router, _vacuum_and_light())
    with pytest.raises(ModelHTTPError):
        await engine.process_request(request)  # the crashed delivery: no runner after it
    assert router.route.await_count == 2
    spy = MagicMock(wraps=engine)
    spy.process_request = AsyncMock()
    spy.record_partial_turn = AsyncMock(wraps=engine.record_partial_turn)

    await _deliver(redis, entry_id, spy, delivery=2)

    spy.process_request.assert_not_awaited()
    assert router.route.await_count == 2
    assert script.call_count == 3
    responses = await forward_range(redis, USER_RESPONSES_STREAM)
    [text] = [json.loads(f[b"event"])["text"] for _id, f in responses]
    assert text == ledger_reply(await EffectLedger(redis).read(request.event_id))
    assert text.startswith("I'd started on that — I did: ")
    assert "vacuum start" in text
    assert "light turn on" in text
    assert int((await redis.xpending(USER_REQUESTS_STREAM, _GROUP))["pending"]) == 0


async def test_one_failed_call_of_two_concurrent_side_effects_leaves_the_other_settled_ok() -> None:
    def vacuum_stuck(action: ActionRequest) -> MagicMock:
        if action.tool_name == _VACUUM:
            return MagicMock(status="error", result=None, error="vacuum stuck")
        return MagicMock(status="success", result={"ok": True}, error=None)

    redis = fakeredis.FakeAsyncRedis()
    router = AsyncMock()
    router.route.side_effect = vacuum_stuck
    script = Script(
        _search_for_the_vacuum(),
        _vacuum_and_light_together(),
        "The light is on; the vacuum is stuck, sir.",
    )
    request = _request()
    entry_id = await _pending(redis, request)

    await _deliver(redis, entry_id, _engine(redis, script, router, _vacuum_and_light()), delivery=1)

    ledger = await EffectLedger(redis).read(request.event_id)
    assert {e.tool: e.status for e in ledger} == {_VACUUM: "failed", _LIGHT: "ok"}
    [(_id, fields)] = await forward_range(redis, USER_RESPONSES_STREAM)
    response = json.loads(fields[b"event"])
    assert response["text"] == "The light is on; the vacuum is stuck, sir."
    assert sorted(response["actions_taken"]) == [_LIGHT, _VACUUM]
    assert int((await redis.xpending(USER_REQUESTS_STREAM, _GROUP))["pending"]) == 0
