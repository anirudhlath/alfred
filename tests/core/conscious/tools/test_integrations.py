"""Integration tools: one per adapter capability, checked against its schema."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.conscious.tools import names, validation
from core.conscious.tools.integrations import (
    IntegrationTool,
    fetch_integration_tools,
    integration_toolset,
)
from core.integrations.base import IntegrationCapability, IntegrationRequest, IntegrationResult
from tests.core.conscious.helpers import make_deps, tool_agent
from tests.llm_fakes import Script, tool_call

if TYPE_CHECKING:
    from collections.abc import Iterator

_WEATHER_SCHEMA: dict[str, Any] = {
    "properties": {
        "location": {"type": "string", "description": "City"},
        "units": {"type": "string", "default": "metric"},
    }
}


@pytest.fixture(autouse=True)
def _fresh_logs(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(names, "_reported", set())
    validation._compile.cache_clear()
    yield


def _adapter(data: dict[str, Any]) -> MagicMock:
    adapter = MagicMock()
    adapter.get_capabilities = AsyncMock(
        return_value=[
            IntegrationCapability(
                name="get_current", description="Current weather", params_schema=_WEATHER_SCHEMA
            )
        ]
    )
    adapter.execute = AsyncMock(
        return_value=IntegrationResult(data=data, freshness=datetime.now(UTC), confidence=1.0)
    )
    return adapter


@pytest.mark.asyncio
async def test_each_capability_becomes_one_tool() -> None:
    adapter = _adapter({})
    with (
        patch(
            "core.conscious.tools.integrations.IntegrationRegistry.available",
            return_value=["weather"],
        ),
        patch("core.conscious.tools.integrations.IntegrationRegistry.get", return_value=adapter),
    ):
        tools = await fetch_integration_tools()

    assert tools == [
        IntegrationTool(
            name="integration_weather_get_current",
            integration="weather",
            capability="get_current",
            description="[weather] Current weather",
            schema={
                "type": "object",
                "properties": _WEATHER_SCHEMA["properties"],
                "required": ["location"],
            },
        )
    ]


@pytest.mark.asyncio
async def test_an_adapter_that_cannot_list_its_capabilities_is_skipped() -> None:
    broken = MagicMock()
    broken.get_capabilities = AsyncMock(side_effect=RuntimeError("no creds"))
    with (
        patch(
            "core.conscious.tools.integrations.IntegrationRegistry.available",
            return_value=["weather"],
        ),
        patch("core.conscious.tools.integrations.IntegrationRegistry.get", return_value=broken),
    ):
        assert await fetch_integration_tools() == []


async def _run(script: Script, adapter: MagicMock, who: str = "sir") -> Any:
    with (
        patch(
            "core.conscious.tools.integrations.IntegrationRegistry.available",
            return_value=["weather"],
        ),
        patch("core.conscious.tools.integrations.IntegrationRegistry.get", return_value=adapter),
    ):
        tools = await fetch_integration_tools()
        deps = make_deps(who, integration_tools=tools)  # type: ignore[arg-type]
        await tool_agent(script, integration_toolset()).run("go", deps=deps)
    return deps


@pytest.mark.asyncio
async def test_a_call_executes_the_capability_by_metadata_not_by_name() -> None:
    adapter = _adapter({"temp_c": 12})
    script = Script(tool_call("integration_weather_get_current", {"location": "Denver"}), "12C")

    deps = await _run(script, adapter)

    adapter.execute.assert_awaited_once_with(
        IntegrationRequest(action="get_current", params={"location": "Denver"})
    )
    assert json.loads(script.returns(1)[0]) == {"temp_c": 12}
    assert deps.actions_taken == ["integration_weather_get_current"]


@pytest.mark.asyncio
async def test_missing_arguments_are_retried_and_never_executed() -> None:
    adapter = _adapter({"temp_c": 12})
    script = Script(tool_call("integration_weather_get_current", {}), "gave up")
    await _run(script, adapter)
    assert "'location' is a required property" in script.retries(1)[0]
    adapter.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_adapter_error_is_a_failed_result() -> None:
    script = Script(tool_call("integration_weather_get_current", {"location": "X"}), "sorry")
    await _run(script, _adapter({"error": "rate limited"}))
    assert script.returns(1) == ["rate limited"]


@pytest.mark.asyncio
async def test_a_guest_is_offered_no_integration_tools() -> None:
    script = Script("hi")
    await _run(script, _adapter({}), who="guest")
    assert script.offered() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("side_effect", [True, False])
async def test_a_tool_carries_its_capabilitys_side_effect_mark(side_effect: bool) -> None:
    adapter = _adapter({})
    adapter.get_capabilities.return_value = [
        IntegrationCapability(
            name="get_current",
            description="Current weather",
            params_schema=_WEATHER_SCHEMA,
            side_effect=side_effect,
        )
    ]
    with (
        patch(
            "core.conscious.tools.integrations.IntegrationRegistry.available",
            return_value=["weather"],
        ),
        patch("core.conscious.tools.integrations.IntegrationRegistry.get", return_value=adapter),
    ):
        [tool] = await fetch_integration_tools()

    assert tool.side_effect is side_effect
