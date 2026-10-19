"""The catalog: one tool list per run, first owner of a wire name wins, failures contained."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from pydantic_ai import ModelRetry, ToolFailed
from pydantic_ai.toolsets import FunctionToolset

from core.conscious.tools import names
from core.conscious.tools.catalog import TOOL_RETRIES, CatalogToolset
from core.conscious.tools.search import SEARCH_TOOL_NAME
from tests.core.conscious.helpers import make_deps, run_context, tool_agent
from tests.llm_fakes import Script, tool_call

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _fresh_error_log(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(names, "_reported", set())
    yield


def _toolset(*funcs: object) -> FunctionToolset[object]:
    return FunctionToolset(list(funcs), max_retries=TOOL_RETRIES)  # type: ignore[arg-type]


def test_tool_retries_is_two() -> None:
    assert TOOL_RETRIES == 2


@pytest.mark.asyncio
async def test_a_wire_name_two_toolsets_offer_goes_to_the_first(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def ping() -> str:
        """First."""
        return "first"

    first = _toolset(ping)

    def ping_again() -> str:
        """Second."""
        return "second"

    second = FunctionToolset([], max_retries=TOOL_RETRIES)
    second.add_function(ping_again, name="ping")
    catalog = CatalogToolset([first, second])
    ctx = run_context(make_deps())

    with caplog.at_level(logging.ERROR, logger="core.conscious.tools.names"):
        tools = await catalog.get_tools(ctx)
        await catalog.get_tools(ctx)

    assert list(tools) == ["ping"]
    assert tools["ping"].tool_def.description == "First."
    assert caplog.text.count("both map to wire name 'ping'") == 1


@pytest.mark.asyncio
async def test_a_tool_offering_the_tool_search_name_is_dropped_and_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def ping() -> str:
        """Pings."""
        return "pong"

    impostor = FunctionToolset([], max_retries=TOOL_RETRIES)
    impostor.add_function(ping, name=SEARCH_TOOL_NAME)
    catalog = CatalogToolset([_toolset(ping), impostor])
    ctx = run_context(make_deps())

    with caplog.at_level(logging.ERROR, logger="core.conscious.tools.names"):
        tools = await catalog.get_tools(ctx)
        await catalog.get_tools(ctx)

    assert list(tools) == ["ping"]
    assert caplog.text.count(f"both map to wire name '{SEARCH_TOOL_NAME}'") == 1


@pytest.mark.asyncio
async def test_each_dispatched_call_is_recorded_and_an_unexpected_error_is_a_failed_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def ok() -> str:
        """Works."""
        return "fine"

    def boom() -> str:
        """Breaks."""
        raise RuntimeError("socket closed")

    script = Script(tool_call("ok"), tool_call("boom"), "done")
    deps = make_deps()

    with caplog.at_level(logging.WARNING, logger="core.conscious.tools.catalog"):
        result = await tool_agent(script, _toolset(ok, boom)).run("go", deps=deps)

    assert result.output == "done"
    assert deps.actions_taken == ["ok", "boom"]
    # The exception's text can carry hosts or paths: it is logged, never shown the model.
    assert script.returns(2) == ["boom failed"]
    assert script.outcomes(2) == ["failed"]
    assert "socket closed" in caplog.text


@pytest.mark.asyncio
async def test_retries_and_tool_failures_pass_through_unchanged() -> None:
    attempts: list[int] = []

    def flaky(n: int) -> str:
        """Needs a positive n."""
        attempts.append(n)
        if n < 1:
            raise ModelRetry("n must be positive")
        raise ToolFailed("the lamp is offline")

    script = Script(tool_call("flaky", {"n": 0}), tool_call("flaky", {"n": 1}), "ok")

    deps = make_deps()

    await tool_agent(script, _toolset(flaky)).run("go", deps=deps)

    assert script.retries(1) and "n must be positive" in script.retries(1)[0]
    assert script.returns(2) == ["the lamp is offline"]
    assert script.outcomes(2) == ["failed"]
    assert attempts == [0, 1]
    assert deps.actions_taken == ["flaky", "flaky"]


@pytest.mark.asyncio
async def test_an_empty_list_reaches_the_model_as_an_empty_json_array() -> None:
    def find() -> list[str]:
        """Nothing to find."""
        return []

    script = Script(tool_call("find", {}), "none")
    await tool_agent(script, _toolset(find)).run("go", deps=make_deps())

    assert script.returns(1) == ["[]"]
