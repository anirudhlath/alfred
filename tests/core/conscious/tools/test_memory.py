"""Memory tools: the verified user's, and only when the turn has a memory index."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from core.conscious.tools.memory import MEMORY_FUNCTIONS, memory_toolset
from tests.core.conscious.helpers import make_deps, make_services, tool_agent
from tests.llm_fakes import Script, tool_call


def test_the_functions_are_named_as_before() -> None:
    assert sorted(MEMORY_FUNCTIONS.tools) == ["memory_get_live_state", "memory_recall_memories"]


@pytest.mark.asyncio
async def test_offered_to_sir_with_an_index_only() -> None:
    with_index = make_services(context_index=AsyncMock())
    cases = {
        ("sir", True): ["memory_get_live_state", "memory_recall_memories"],
        ("sir", False): [],
        ("guest", True): [],
    }
    for (who, has_index), expected in cases.items():
        script = Script("hi")
        services = with_index if has_index else make_services()
        await tool_agent(script, memory_toolset()).run(
            "go",
            deps=make_deps(who, services=services),  # type: ignore[arg-type]
        )
        assert sorted(script.offered()) == expected, (who, has_index)


@pytest.mark.asyncio
async def test_recall_is_offered_with_its_descriptions_and_query_required() -> None:
    script = Script("hi")
    await tool_agent(script, memory_toolset()).run(
        "go", deps=make_deps(services=make_services(context_index=AsyncMock()))
    )
    schema = script.schema("memory_recall_memories")
    assert schema["required"] == ["query"]
    assert schema["properties"]["query"]["description"] == "Natural language search query."
    tool = next(
        t for t in script.requests[0][1].function_tools if t.name == "memory_recall_memories"
    )
    assert (tool.description or "").startswith("Search Alfred's memory for relevant information")


@pytest.mark.asyncio
async def test_live_state_reaches_the_model() -> None:
    reader = AsyncMock()
    reader.get_entity_states.return_value = [{"entity_id": "light.desk", "state": "on"}]
    script = Script(tool_call("memory_get_live_state", {"entities": ["light.*"]}), "on")
    services = make_services(context_index=AsyncMock(), context_reader=reader)

    await tool_agent(script, memory_toolset()).run("go", deps=make_deps(services=services))

    reader.get_entity_states.assert_awaited_once_with(patterns=["light.*"])
    assert json.loads(script.returns(1)[0]) == {
        "available": True,
        "entities": [{"entity_id": "light.desk", "state": "on"}],
    }
