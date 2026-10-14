"""The side-effect ledger (#304): a turn that already acted answers from it, never twice.

Spec 6 ("A failed turn answers once"): every side-effecting tool call is recorded
before it runs and its outcome after, keyed by the request's ``event_id``. A turn that
fails after acting, or is redelivered after a crash, answers from the ledger with a
fixed template instead of running again.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError

from bus.schemas.events import UserRequest
from core.conscious.agent import build_system2_agent, system2_toolsets
from core.conscious.context_assembler import ContextAssembler
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
from core.conscious.session import SessionManager
from core.conscious.tools.attention import attention_toolset
from core.conscious.tools.catalog import CatalogToolset
from core.conscious.tools.registry import RegistryToolset
from core.conscious.tools.triggers import TriggerToolset
from core.reflex.tool_registry import ToolInfo
from core.triggers.feature import TriggerFeature, TriggerFeatureContext
from core.triggers.store import TriggerStore
from shared.redis_streams import forward_range
from shared.streams import (
    DEAD_LETTER_STREAM,
    EFFECTS_KEY,
    TOOL_REGISTRY_KEY,
    USER_REQUESTS_STREAM,
    USER_RESPONSES_STREAM,
)
from tests.core.conscious.helpers import identity, make_deps, make_services
from tests.llm_fakes import Script, tool_call

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


def test_a_name_without_a_service_prefix_is_humanised_whole() -> None:
    assert ledger_reply([Effect("attention_add", {}, "ok")]).startswith(
        "I'd started on that — I did: attention add — "
    )


# ── what is recorded ─────────────────────────────────────────────────────────


def _info(name: str, risk: str = "benign") -> ToolInfo:
    return ToolInfo(
        name=name,
        description=f"{name} does a thing",
        feature_name=name.split(".")[0],
        feature_description="Home control",
        target_service="home-service",
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
    [("attention_add", True), ("attention_remove", True), ("attention_list", False)],
)
async def test_attention_changes_are_recorded_and_listing_is_not(name: str, recorded: bool) -> None:
    redis = fakeredis.FakeAsyncRedis()
    args = {"domain": "home"} | ({} if name == "attention_list" else {"entity_id": "sensor.x"})
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


def _engine(redis: fakeredis.FakeAsyncRedis, script: Script, router: AsyncMock) -> ConsciousEngine:
    """A real engine and System 2 agent over fakeredis, with one registry tool."""
    identity_gate = MagicMock()
    identity_gate.resolve.return_value = identity("sir")
    cost = AsyncMock()
    cost.is_budget_exceeded.return_value = False
    registry = AsyncMock()
    registry.get_tools.return_value = [_info("home.light_turn_on")]
    return ConsciousEngine(
        deps=ConsciousDeps(
            redis=redis,
            identity_gate=identity_gate,
            session_mgr=SessionManager(redis),
            cost_tracker=cost,
            domain_router=router,
            tool_registry=registry,
            context_reader=AsyncMock(),
            agent=build_system2_agent(
                toolsets=system2_toolsets(None),
                assembler=ContextAssembler(),
                max_tokens=256,
                timeout_s=5.0,
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
    history = await SessionManager(redis).get_history("sess-1")
    assert history == [
        {"role": "user", "content": "turn on the porch light"},
        {"role": "assistant", "content": _PORCH_REPLY},
    ]


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
