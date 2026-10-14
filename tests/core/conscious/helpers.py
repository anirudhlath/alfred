"""Builders shared by the System 2 tests: identities, turn dependencies, a scripted agent."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from unittest.mock import AsyncMock, MagicMock

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from core.conscious.deps import TurnDeps, TurnServices
from core.conscious.tools.catalog import CatalogToolset
from core.identity.schemas import IdentityResult

if TYPE_CHECKING:
    from pydantic_ai.toolsets import AbstractToolset

    from tests.llm_fakes import Script


def identity(who: Literal["sir", "guest"] = "sir") -> IdentityResult:
    if who == "sir":
        return IdentityResult(
            identity="sir",
            confidence=0.99,
            method="webauthn",
            factors=["webauthn"],
            risk_clearance="high",
        )
    return IdentityResult(
        identity="guest", confidence=0.0, method="none", factors=[], risk_clearance="low"
    )


def mock_redis() -> AsyncMock:
    """A mocked async Redis whose ``pipeline()`` works as redis-py's does: a sync call
    returning an async context manager, whose ``execute`` answers ``[1, True]`` (an RPUSH
    and an EXPIRE, as the side-effect ledger sends)."""
    redis = AsyncMock()
    pipe = MagicMock()
    pipe.execute = AsyncMock(return_value=[1, True])
    context = MagicMock()
    context.__aenter__.return_value = pipe
    redis.pipeline = MagicMock(return_value=context)
    return redis


def make_services(**overrides: Any) -> TurnServices:
    fields: dict[str, Any] = {
        "redis": mock_redis(),
        "router": AsyncMock(),
        "context_reader": AsyncMock(),
        "context_index": None,
    }
    fields.update(overrides)
    return TurnServices(**fields)


def make_deps(who: Literal["sir", "guest"] = "sir", **overrides: Any) -> TurnDeps:
    fields: dict[str, Any] = {
        "identity": identity(who),
        "session_id": "sess-1",
        "channel": "web_pwa",
        "request_id": "req-1",
        "tz_name": "UTC",
        "now": datetime(2026, 10, 13, 12, 0, tzinfo=UTC),
        "services": make_services(),
    }
    fields.update(overrides)
    return TurnDeps(**fields)


def tool_agent(script: Script, *toolsets: AbstractToolset[TurnDeps]) -> Agent[TurnDeps, str]:
    """An agent with *toolsets* in one catalog, as System 2 has, answering from *script*."""
    return Agent(
        script.model, deps_type=TurnDeps, toolsets=[CatalogToolset(list(toolsets))], retries=2
    )


def run_context(deps: TurnDeps) -> RunContext[TurnDeps]:
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage())


async def call_direct(
    toolset: AbstractToolset[TurnDeps],
    name: str,
    args: dict[str, Any],
    *,
    offered_to: TurnDeps,
    called_by: TurnDeps,
) -> Any:
    """Call *name* as *called_by*, with the tool as *toolset* offered it to *offered_to*.

    Offering to sir and calling as a guest is how a test reaches the call-time check
    a run can never reach through the model.
    """
    tools = await toolset.get_tools(run_context(offered_to))
    return await toolset.call_tool(name, args, run_context(called_by), tools[name])
