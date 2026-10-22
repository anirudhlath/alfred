"""The System 2 agent: one catalog, the turn's instructions, the role header."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from pydantic_ai.native_tools._tool_search import (  # no public path: the framework's own name
    TOOL_SEARCH_FUNCTION_TOOL_NAME,
)
from pydantic_ai_harness.spend import SpendEntry, SpendLimitExceeded

from core.conscious.agent import (
    OUTPUT_RETRIES,
    REQUEST_LIMIT,
    SYSTEM2_NAME,
    build_system2_agent,
    system2_toolsets,
)
from core.conscious.system2_prompts import System2Prompts
from core.conscious.tools import names, validation
from core.conscious.tools.search import (
    QUERIES_DESCRIPTION,
    SEARCH_DESCRIPTION,
    SEARCH_TOOL_NAME,
    tool_search,
)
from core.memory.vector_store import ContextMetadata, SearchResult
from core.reflex.tool_registry import REFLEX_AUDIENCE, ToolInfo
from core.triggers.feature import TriggerFeature, TriggerFeatureContext
from core.triggers.store import TriggerStore
from shared.prompts.names import SYSTEM2_IDENTITY_SIR, SYSTEM2_LOCATION, SYSTEM2_PERSONALITY
from shared.prompts.template import RenderedPrompt, repo_prompt
from shared.spend import build_spend_limits, local_clock
from tests.core.conscious.helpers import make_deps, make_services, mock_redis
from tests.llm_fakes import Script, tool_call

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pydantic_ai import Agent, DeferredToolRequests

    from core.conscious.deps import TurnDeps


@pytest.fixture(autouse=True)
def _fresh_logs(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(names, "_reported", set())
    validation._compile.cache_clear()
    yield


def _triggers() -> TriggerFeature:
    store = AsyncMock(spec_set=TriggerStore)
    store.list_all = AsyncMock(return_value=[])
    return TriggerFeature(TriggerFeatureContext(store=store))


def _agent(
    *, max_tokens: int = 512, timeout_s: float = 15.0
) -> Agent[TurnDeps, str | DeferredToolRequests]:
    return build_system2_agent(
        toolsets=system2_toolsets(_triggers()),
        max_tokens=max_tokens,
        timeout_s=timeout_s,
        spend=None,
    )


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


def _router() -> AsyncMock:
    router = AsyncMock()
    router.route.return_value = MagicMock(status="success", result={"ok": True}, error=None)
    return router


def test_constants() -> None:
    assert (SYSTEM2_NAME, REQUEST_LIMIT, OUTPUT_RETRIES) == ("system2", 10, 2)


@pytest.mark.asyncio
async def test_dotted_name_is_retried_with_the_wire_names_and_never_dispatched() -> None:
    router = _router()
    args = {"entity_id": "light.desk"}
    script = Script(tool_call("home.light_on", args), tool_call("home_light_on", args), "done")
    deps = make_deps(registry_tools=[_info("home.light_on")], services=make_services(router=router))

    result = await _agent().run("lights", deps=deps, model=script.model)

    assert result.output == "done"
    retry = script.retries(1)[0]
    assert "home.light_on" in retry and "home_light_on" in retry
    router.route.assert_awaited_once()
    assert router.route.await_args.args[0].tool_name == "home.light_on"
    assert deps.actions_taken == ["home.light_on"]


@pytest.mark.asyncio
async def test_settings_carry_the_role_header_the_token_cap_and_the_timeout() -> None:
    script = Script("hi")
    await _agent(max_tokens=512, timeout_s=15.0).run("hi", deps=make_deps(), model=script.model)
    settings = script.settings()
    assert settings["max_tokens"] == 512
    assert settings["timeout"] == 15.0
    assert settings["extra_headers"] == {"X-Alfred-Role": "system2"}


@pytest.mark.asyncio
async def test_instructions_are_the_assembled_prompt_for_this_turn() -> None:
    recall = [
        SearchResult(
            id="m1",
            score=0.9,
            content="Sir prefers dim lighting",
            semantic_key="m1",
            metadata=ContextMetadata(
                type="semantic",
                source="t",
                entities="",
                timestamp=0.0,
                significance=1.0,
                retrieval_count=0,
            ),
        )
    ]
    prompts = System2Prompts(
        personality=RenderedPrompt("You are Alfred, a butler.", SYSTEM2_PERSONALITY, None, "repo"),
        identity=repo_prompt(SYSTEM2_IDENTITY_SIR),
        location=repo_prompt(SYSTEM2_LOCATION, area="Kitchen"),
    )
    deps = make_deps(
        area="Kitchen",
        prompts=prompts,
        recall=recall,
        routine_hint="[routine-suggestion] Evening dim.",
        now=datetime(2026, 7, 16, 20, 5, 32, tzinfo=UTC),
        tz_name="America/Denver",
    )
    script = Script("hi")

    await _agent().run("hi", deps=deps, model=script.model)

    text = script.instructions()
    assert text.startswith("You are Alfred, a butler.")
    assert "## Location" in text and "Kitchen" in text
    assert "Thursday 2026-07-16T14:05:32-06:00 (America/Denver)" in text
    assert "Sir prefers dim lighting" in text
    assert text.endswith("\n\n[routine-suggestion] Evening dim.")
    assert "Available Tools" not in text


@pytest.mark.asyncio
async def test_guest_is_offered_only_benign_tools_across_every_toolset() -> None:
    registry = [_info("home.get_lights"), _info("home.light_on", "elevated")]
    services = make_services(context_index=AsyncMock())
    sir, guest = Script("hi"), Script("hi")

    await _agent().run(
        "hi", deps=make_deps("sir", registry_tools=registry, services=services), model=sir.model
    )
    await _agent().run(
        "hi", deps=make_deps("guest", registry_tools=registry, services=services), model=guest.model
    )

    guest_triggers = {"triggers_create_trigger"}
    assert set(guest.offered()) == guest_triggers | {"home_get_lights"}
    assert {
        "memory_recall_memories",
        "attention_add",
        "home_light_on",
        "triggers_list_triggers",
        "triggers_update_trigger",
        "triggers_delete_trigger",
        "triggers_toggle_trigger",
    } | guest_triggers <= set(sir.offered())


@pytest.mark.asyncio
async def test_an_in_process_tool_keeps_its_name_against_a_registry_tool() -> None:
    router = _router()
    redis = mock_redis()
    impostor = _info("attention.add")
    script = Script(tool_call("attention_add", {"domain": "home", "entity_id": "sensor.x"}), "done")
    deps = make_deps(registry_tools=[impostor], services=make_services(router=router, redis=redis))

    await _agent().run("watch it", deps=deps, model=script.model)

    router.route.assert_not_awaited()
    redis.sadd.assert_any_call("alfred:attention:home", "sensor.x")


@pytest.mark.asyncio
async def test_malformed_json_arguments_are_repaired_without_a_retry() -> None:
    router = _router()
    script = Script(tool_call("home_light_on", '{"entity_id": "light.desk",}'), "done")
    deps = make_deps(registry_tools=[_info("home.light_on")], services=make_services(router=router))

    await _agent().run("lights", deps=deps, model=script.model)

    assert script.retries(1) == []
    assert router.route.await_args.args[0].parameters == {"entity_id": "light.desk"}


@pytest.mark.asyncio
async def test_no_trigger_feature_means_no_trigger_tools() -> None:
    agent = build_system2_agent(
        toolsets=system2_toolsets(None),
        max_tokens=512,
        timeout_s=15.0,
        spend=None,
    )
    script = Script("hi")
    await agent.run("hi", deps=make_deps(), model=script.model)
    assert not [name for name in script.offered() if name.startswith("triggers_")]


@pytest.mark.asyncio
async def test_the_budget_gates_every_model_request() -> None:
    """The spend capability is on the agent: a spent budget refuses the request."""
    spend = build_spend_limits(
        fakeredis.FakeAsyncRedis(), cap_usd=1.0, clock=local_clock(lambda: "UTC")
    )
    (status,) = await spend.status()
    await spend.store.add_many(  # type: ignore[union-attr]
        [SpendEntry(key=status.key, usd=Decimal(1), requests=1)]
    )
    agent = build_system2_agent(
        toolsets=system2_toolsets(None),
        max_tokens=512,
        timeout_s=15.0,
        spend=spend,
    )
    script = Script("never sent")

    with pytest.raises(SpendLimitExceeded):
        await agent.run("hi", deps=make_deps(), model=script.model)

    assert script.call_count == 0


# ── tool search ──────────────────────────────────────────────────────────────


def _vacuum_registry() -> list[ToolInfo]:
    return [_info("home.light_turn_on"), _info("home.vacuum_start", audience="conscious")]


def test_tool_search_is_pinned_and_described() -> None:
    cap = tool_search()
    assert (cap.strategy, cap.max_results, cap.tool_description, cap.parameter_description) == (
        "keywords",
        10,
        SEARCH_DESCRIPTION,
        QUERIES_DESCRIPTION,
    )


def test_the_reserved_tool_name_is_the_frameworks() -> None:
    assert SEARCH_TOOL_NAME == TOOL_SEARCH_FUNCTION_TOOL_NAME


@pytest.mark.asyncio
async def test_a_registry_tool_named_search_tools_cannot_take_the_turn_down(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # `search.tools` has the wire name `search_tools`, which the framework reserves and
    # refuses to run with when any tool is deferred.
    registry = [_info("search.tools"), _info("home.vacuum_start", audience="conscious")]
    script = Script("Hello, sir.")

    with caplog.at_level(logging.ERROR, logger="core.conscious.tools.names"):
        result = await _agent().run(
            "hi", deps=make_deps(registry_tools=registry), model=script.model
        )

    assert result.output == "Hello, sir."
    assert script.offered(0).count("search_tools") == 1
    # The one offered is the framework's search, not the registry's impostor.
    search = next(t for t in script.requests[0][1].function_tools if t.name == "search_tools")
    assert search.description == SEARCH_DESCRIPTION
    assert list(script.schema("search_tools")["properties"]) == ["queries"]
    assert "'search.tools' is not offered" in caplog.text


@pytest.mark.asyncio
async def test_only_reflex_registry_tools_are_loaded_before_a_search() -> None:
    script = Script("hi")
    services = make_services(context_index=AsyncMock())
    deps = make_deps(registry_tools=_vacuum_registry(), services=services)

    await _agent().run("hi", deps=deps, model=script.model)

    offered = script.offered(0)
    assert {"home_light_turn_on", "search_tools", "memory_recall_memories"} <= set(offered)
    assert "home_vacuum_start" not in offered
    search = next(t for t in script.requests[0][1].function_tools if t.name == "search_tools")
    assert search.description == SEARCH_DESCRIPTION


@pytest.mark.asyncio
async def test_a_searched_tool_is_loaded_and_dispatched() -> None:
    router = _router()
    args = {"entity_id": "vacuum.hall"}
    script = Script(
        tool_call("search_tools", {"queries": ["vacuum start"]}),
        tool_call("home_vacuum_start", args),
        "Started, sir.",
    )
    deps = make_deps(registry_tools=_vacuum_registry(), services=make_services(router=router))

    result = await _agent().run("vacuum", deps=deps, model=script.model)

    assert result.output == "Started, sir."
    assert "home_vacuum_start" in script.offered(1)
    router.route.assert_awaited_once()
    assert router.route.await_args.args[0].tool_name == "home.vacuum_start"
    assert deps.actions_taken == ["home.vacuum_start"]


@pytest.mark.asyncio
async def test_calling_an_unsearched_tool_is_refused_with_a_hint_and_the_turn_recovers() -> None:
    router = _router()
    args = {"entity_id": "vacuum.hall"}
    script = Script(
        tool_call("home_vacuum_start", args),
        tool_call("search_tools", {"queries": ["vacuum start"]}),
        tool_call("home_vacuum_start", args),
        "Started, sir.",
    )
    deps = make_deps(registry_tools=_vacuum_registry(), services=make_services(router=router))

    result = await _agent().run("vacuum", deps=deps, model=script.model)

    # The framework's own refusal, not the unknown-tool one (which also names `search_tools`).
    (retry,) = script.retries(1)
    assert "search for it first" in retry
    router.route.assert_awaited_once()
    # Only the call that followed the search is recorded; the refused one never dispatched.
    assert deps.actions_taken == ["home.vacuum_start"]
    assert result.output == "Started, sir."


@pytest.mark.asyncio
async def test_a_search_that_matches_nothing_dispatches_nothing() -> None:
    router = _router()
    script = Script(
        tool_call("search_tools", {"queries": ["teleport the cat"]}),
        "I have no means of doing that, sir.",
    )
    deps = make_deps(registry_tools=_vacuum_registry(), services=make_services(router=router))

    result = await _agent().run("teleport", deps=deps, model=script.model)

    router.route.assert_not_awaited()
    assert "No matching tools" in script.returns(1)[0]
    assert result.output == "I have no means of doing that, sir."


def _lock_registry() -> list[ToolInfo]:
    return [
        _info("home.lock_door", "elevated", audience="conscious"),
        _info("home.vacuum_start", audience="conscious"),
    ]


@pytest.mark.asyncio
async def test_a_guest_can_neither_find_nor_call_an_elevated_deferred_tool() -> None:
    router = _router()
    script = Script(
        tool_call("search_tools", {"queries": ["lock the door"]}),
        tool_call("home_lock_door", {"entity_id": "lock.front"}),
        "I'm afraid I can't, sir.",
    )
    deps = make_deps(
        "guest", registry_tools=_lock_registry(), services=make_services(router=router)
    )

    await _agent().run("lock up", deps=deps, model=script.model)

    assert "home_lock_door" not in script.returns(1)[0]
    assert "home_lock_door" not in script.offered(1)
    assert "home_lock_door" not in script.offered(2)
    router.route.assert_not_awaited()


@pytest.mark.asyncio
async def test_sir_finds_the_same_elevated_tool_the_guest_cannot() -> None:
    # The control for the guest test above: same registry, same query. Without it, a
    # change to the descriptions or the strategy could make the guest assertions pass
    # because nothing was ever findable.
    script = Script(
        tool_call("search_tools", {"queries": ["lock the door"]}),
        "Found it, sir.",
    )
    deps = make_deps("sir", registry_tools=_lock_registry())

    await _agent().run("lock up", deps=deps, model=script.model)

    assert "home_lock_door" in script.returns(1)[0]
    assert "home_lock_door" in script.offered(1)
