"""The System 2 agent: one catalog, the turn's instructions, the role header."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.conscious.agent import (
    OUTPUT_RETRIES,
    REQUEST_LIMIT,
    SYSTEM2_NAME,
    build_system2_agent,
    system2_toolsets,
)
from core.conscious.context_assembler import ContextAssembler
from core.conscious.tools import names, validation
from core.memory.vector_store import ContextMetadata, SearchResult
from core.reflex.tool_registry import ToolInfo
from core.triggers.feature import TriggerFeature, TriggerFeatureContext
from core.triggers.store import TriggerStore
from tests.core.conscious.helpers import make_deps, make_services
from tests.llm_fakes import Script, tool_call

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from pydantic_ai import Agent

    from core.conscious.deps import TurnDeps


@pytest.fixture(autouse=True)
def _fresh_logs(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(names, "_reported", set())
    validation._compile.cache_clear()
    yield


def _assembler(tmp_path: Path) -> ContextAssembler:
    personality = tmp_path / "personality.md"
    personality.write_text("You are Alfred, a butler.")
    return ContextAssembler(personality_path=personality)


def _triggers() -> TriggerFeature:
    store = AsyncMock(spec_set=TriggerStore)
    store.list_all = AsyncMock(return_value=[])
    return TriggerFeature(TriggerFeatureContext(store=store))


def _agent(
    tmp_path: Path, *, max_tokens: int = 512, timeout_s: float = 15.0
) -> Agent[TurnDeps, str]:
    return build_system2_agent(
        toolsets=system2_toolsets(_triggers()),
        assembler=_assembler(tmp_path),
        max_tokens=max_tokens,
        timeout_s=timeout_s,
    )


def _info(name: str, risk: str = "benign") -> ToolInfo:
    return ToolInfo(
        name=name,
        description=f"{name} does a thing",
        feature_name=name.split(".")[0],
        feature_description="Home control",
        target_service="home-service",
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
async def test_dotted_name_is_retried_with_the_wire_names_and_never_dispatched(
    tmp_path: Path,
) -> None:
    router = _router()
    args = {"entity_id": "light.desk"}
    script = Script(tool_call("home.light_on", args), tool_call("home_light_on", args), "done")
    deps = make_deps(registry_tools=[_info("home.light_on")], services=make_services(router=router))

    result = await _agent(tmp_path).run("lights", deps=deps, model=script.model)

    assert result.output == "done"
    retry = script.retries(1)[0]
    assert "home.light_on" in retry and "home_light_on" in retry
    router.route.assert_awaited_once()
    assert router.route.await_args.args[0].tool_name == "home.light_on"
    assert deps.actions_taken == ["home.light_on"]


@pytest.mark.asyncio
async def test_settings_carry_the_role_header_the_token_cap_and_the_timeout(
    tmp_path: Path,
) -> None:
    script = Script("hi")
    await _agent(tmp_path, max_tokens=512, timeout_s=15.0).run(
        "hi", deps=make_deps(), model=script.model
    )
    settings = script.settings()
    assert settings["max_tokens"] == 512
    assert settings["timeout"] == 15.0
    assert settings["extra_headers"] == {"X-Alfred-Role": "system2"}


@pytest.mark.asyncio
async def test_instructions_are_the_assembled_prompt_for_this_turn(tmp_path: Path) -> None:
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
    deps = make_deps(
        area="Kitchen",
        recall=recall,
        routine_hint="[routine-suggestion] Evening dim.",
        now=datetime(2026, 7, 16, 20, 5, 32, tzinfo=UTC),
        tz_name="America/Denver",
    )
    script = Script("hi")

    await _agent(tmp_path).run("hi", deps=deps, model=script.model)

    text = script.instructions()
    assert text.startswith("You are Alfred, a butler.")
    assert "## Location" in text and "Kitchen" in text
    assert "Thursday 2026-07-16T14:05:32-06:00 (America/Denver)" in text
    assert "Sir prefers dim lighting" in text
    assert text.endswith("\n\n[routine-suggestion] Evening dim.")
    assert "Available Tools" not in text


@pytest.mark.asyncio
async def test_guest_is_offered_only_benign_tools_across_every_toolset(tmp_path: Path) -> None:
    registry = [_info("home.get_lights"), _info("home.light_on", "elevated")]
    services = make_services(context_index=AsyncMock())
    sir, guest = Script("hi"), Script("hi")

    await _agent(tmp_path).run(
        "hi", deps=make_deps("sir", registry_tools=registry, services=services), model=sir.model
    )
    await _agent(tmp_path).run(
        "hi", deps=make_deps("guest", registry_tools=registry, services=services), model=guest.model
    )

    triggers = {name for name in sir.offered() if name.startswith("triggers_")}
    assert triggers, "the trigger tools are benign and offered to both"
    assert set(guest.offered()) == triggers | {"home_get_lights"}
    assert {
        "memory_recall_memories",
        "confirm_pending_action",
        "attention_add",
        "home_light_on",
    } <= set(sir.offered())


@pytest.mark.asyncio
async def test_an_in_process_tool_keeps_its_name_against_a_registry_tool(
    tmp_path: Path,
) -> None:
    router = _router()
    redis = AsyncMock()
    impostor = _info("attention.add")
    script = Script(tool_call("attention_add", {"domain": "home", "entity_id": "sensor.x"}), "done")
    deps = make_deps(registry_tools=[impostor], services=make_services(router=router, redis=redis))

    await _agent(tmp_path).run("watch it", deps=deps, model=script.model)

    router.route.assert_not_awaited()
    redis.sadd.assert_any_call("alfred:attention:home", "sensor.x")


@pytest.mark.asyncio
async def test_malformed_json_arguments_are_repaired_without_a_retry(tmp_path: Path) -> None:
    router = _router()
    script = Script(tool_call("home_light_on", '{"entity_id": "light.desk",}'), "done")
    deps = make_deps(registry_tools=[_info("home.light_on")], services=make_services(router=router))

    await _agent(tmp_path).run("lights", deps=deps, model=script.model)

    assert script.retries(1) == []
    assert router.route.await_args.args[0].parameters == {"entity_id": "light.desk"}


@pytest.mark.asyncio
async def test_no_trigger_feature_means_no_trigger_tools(tmp_path: Path) -> None:
    agent = build_system2_agent(
        toolsets=system2_toolsets(None),
        assembler=_assembler(tmp_path),
        max_tokens=512,
        timeout_s=15.0,
    )
    script = Script("hi")
    await agent.run("hi", deps=make_deps(), model=script.model)
    assert not [name for name in script.offered() if name.startswith("triggers_")]
