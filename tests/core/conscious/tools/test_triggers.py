"""Trigger tools: served in-process from TriggerFeature, validated by its signatures."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.conscious.tools.triggers import TriggerToolset
from core.triggers.feature import TriggerFeature, TriggerFeatureContext
from core.triggers.store import TriggerStore
from tests.core.conscious.helpers import make_deps, run_context, tool_agent
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
