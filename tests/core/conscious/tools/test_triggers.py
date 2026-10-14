"""Trigger tools: served in-process from TriggerFeature, validated by its signatures."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic_ai import ToolFailed

import core.triggers.types  # noqa: F401 — registers the trigger types create_trigger names
from core.conscious.tools.triggers import TriggerToolset
from core.triggers.feature import TriggerFeature, TriggerFeatureContext
from core.triggers.store import TriggerStore
from shared.streams import TOOL_REGISTRY_KEY
from tests.core.conscious.helpers import (
    call_direct,
    make_deps,
    make_services,
    run_context,
    tool_agent,
)
from tests.llm_fakes import Script, tool_call


def _toolset() -> tuple[TriggerToolset, AsyncMock]:
    store = AsyncMock(spec_set=TriggerStore)
    store.list_all = AsyncMock(return_value=[])
    store.get = AsyncMock(return_value=None)
    return TriggerToolset(TriggerFeature(TriggerFeatureContext(store=store))), store


@pytest.mark.asyncio
async def test_required_lists_exactly_the_parameters_without_defaults() -> None:
    """#300, ported from test_engine.py: the SDK's schemas reach the model."""
    toolset, _ = _toolset()
    script = Script("ok")
    await tool_agent(script, toolset).run("go", deps=make_deps())

    assert script.schema("triggers_delete_trigger")["required"] == ["trigger_id"]
    assert script.schema("triggers_toggle_trigger")["required"] == ["trigger_id", "enabled"]
    assert script.schema("triggers_create_trigger")["required"] == [
        "name",
        "trigger_type",
        "conditions",
    ]
    assert script.schema("triggers_list_triggers")["required"] == []


@pytest.mark.asyncio
async def test_create_trigger_carries_the_timing_guidance() -> None:
    toolset, _ = _toolset()
    tools = await toolset.get_tools(run_context(make_deps()))
    description = tools["triggers_create_trigger"].tool_def.description or ""
    assert "pass run_in_seconds (e.g. 1200)" in description
    assert "WITH the user's current UTC offset" in description
    assert "Never pass both." in description


@pytest.mark.asyncio
async def test_bad_arguments_name_each_field_and_never_run() -> None:
    toolset, store = _toolset()
    script = Script(
        tool_call(
            "triggers_create_trigger", {"name": "wake", "conditions": "daily", "colour": "red"}
        ),
        "fixed it",
    )
    deps = make_deps()

    await tool_agent(script, toolset).run("go", deps=deps)

    retry = script.retries(1)[0]
    assert "Tool 'triggers.create_trigger' got invalid arguments: " in retry
    for field_name in ("trigger_type:", "conditions:", "colour:"):
        assert field_name in retry
    store.save.assert_not_awaited()
    assert deps.actions_taken == []


@pytest.mark.asyncio
async def test_arguments_are_coerced_to_the_signature() -> None:
    toolset, store = _toolset()
    script = Script(tool_call("triggers_list_triggers", {"enabled_only": "false"}), "none")
    deps = make_deps()

    await tool_agent(script, toolset).run("go", deps=deps)

    assert script.returns(1) == ["[]"]
    store.list_all.assert_awaited_once_with(enabled_only=False)
    assert deps.actions_taken == ["triggers.list_triggers"]


@pytest.mark.asyncio
async def test_an_error_result_is_a_failed_tool_result() -> None:
    toolset, _ = _toolset()
    script = Script(
        tool_call("triggers_toggle_trigger", {"trigger_id": "t-9", "enabled": True}), "gone"
    )
    await tool_agent(script, toolset).run("go", deps=make_deps())
    assert script.returns(1) == ["Trigger 't-9' not found"]
    assert script.retries(1) == []


def test_tools_are_read_once_when_the_toolset_is_built() -> None:
    toolset, _ = _toolset()
    with patch.object(TriggerFeature, "get_tools", side_effect=AssertionError("re-read")):
        import asyncio

        tools = asyncio.run(toolset.get_tools(run_context(make_deps())))
    assert "triggers_delete_trigger" in tools


# ── guests: create (benign actions only) and list; never another trigger's settings


def _registry_redis() -> AsyncMock:
    """A tool registry holding one benign and one elevated home tool."""
    manifest = {
        "service_name": "home-service",
        "features": [
            {
                "name": "home",
                "tools": [
                    {"name": "home.get_lights", "risk": "benign"},
                    {"name": "home.unlock_door", "risk": "elevated"},
                ],
            }
        ],
    }
    registry = {"home-service": json.dumps(manifest).encode()}
    redis = AsyncMock()
    redis.hget.side_effect = lambda key, field: (
        registry.get(field) if key == TOOL_REGISTRY_KEY else None
    )
    return redis


def _create_args(action: dict[str, Any] | None = None) -> dict[str, Any]:
    args: dict[str, Any] = {
        "name": "later",
        "trigger_type": "time",
        "conditions": {"run_in_seconds": 60},
    }
    if action is not None:
        args["action"] = action
    return args


def _action(tool_name: str, target_service: str = "home-service") -> dict[str, Any]:
    return {"tool_name": tool_name, "target_service": target_service, "parameters": {}}


@pytest.mark.asyncio
async def test_guest_is_offered_only_create_and_list() -> None:
    toolset, _ = _toolset()
    guest = await toolset.get_tools(run_context(make_deps("guest")))
    sir = await toolset.get_tools(run_context(make_deps("sir")))

    assert set(guest) == {"triggers_create_trigger", "triggers_list_triggers"}
    assert {"triggers_update_trigger", "triggers_delete_trigger", "triggers_toggle_trigger"} <= (
        set(sir)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["update_trigger", "delete_trigger", "toggle_trigger"])
async def test_a_guest_reaching_a_trigger_mutation_at_call_time_is_refused(tool_name: str) -> None:
    toolset, store = _toolset()
    name = f"triggers_{tool_name}"
    with pytest.raises(ToolFailed, match=f"^{name} is only available to the verified user$"):
        await call_direct(
            toolset,
            name,
            {"trigger_id": "t-1", "enabled": False},
            offered_to=make_deps("sir"),
            called_by=make_deps("guest"),
        )
    store.get.assert_not_awaited()
    store.delete.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action", [None, _action("home.get_lights")], ids=["no action", "benign action"]
)
async def test_guest_create_trigger_without_a_risky_action_is_dispatched(
    action: dict[str, Any] | None,
) -> None:
    toolset, store = _toolset()
    script = Script(tool_call("triggers_create_trigger", _create_args(action)), "set")
    deps = make_deps("guest", services=make_services(redis=_registry_redis()))

    await tool_agent(script, toolset).run("go", deps=deps)

    store.save.assert_awaited_once()
    assert script.outcomes(1) == ["success"]
    assert deps.actions_taken == ["triggers.create_trigger"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [_action("home.unlock_door"), _action("home.not_registered"), _action("x", "nowhere")],
    ids=["elevated", "unknown tool", "unknown service"],
)
async def test_guest_create_trigger_with_a_risky_action_is_refused(action: dict[str, Any]) -> None:
    toolset, store = _toolset()
    script = Script(tool_call("triggers_create_trigger", _create_args(action)), "no")
    deps = make_deps("guest", services=make_services(redis=_registry_redis()))

    await tool_agent(script, toolset).run("go", deps=deps)

    assert script.returns(1) == ["triggers_create_trigger is only available to the verified user"]
    assert script.outcomes(1) == ["failed"]
    store.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_trigger_action_naming_a_trigger_mutation_is_refused_to_a_guest() -> None:
    """A guest cannot schedule what they may not call: the trigger tools' own risks count."""
    toolset, store = _toolset()
    manifest = {
        "features": [
            {"name": "triggers", "tools": [{"name": "triggers.delete_trigger", "risk": "elevated"}]}
        ]
    }
    redis = AsyncMock()
    redis.hget.return_value = json.dumps(manifest).encode()
    action = _action("triggers.delete_trigger", "trigger-engine")
    script = Script(tool_call("triggers_create_trigger", _create_args(action)), "no")

    await tool_agent(script, toolset).run(
        "go", deps=make_deps("guest", services=make_services(redis=redis))
    )

    assert script.outcomes(1) == ["failed"]
    store.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_sir_create_trigger_with_an_elevated_action_is_dispatched_unchecked() -> None:
    toolset, store = _toolset()
    redis = _registry_redis()
    script = Script(
        tool_call("triggers_create_trigger", _create_args(_action("home.unlock_door"))), "set"
    )

    await tool_agent(script, toolset).run(
        "go", deps=make_deps("sir", services=make_services(redis=redis))
    )

    store.save.assert_awaited_once()
    redis.hget.assert_not_awaited()
