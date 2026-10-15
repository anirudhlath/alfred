"""The Librarian's agents: typed output, the role header, retries and the budget."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import fakeredis
import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai_harness.spend import SpendEntry, SpendLimitExceeded

from core.librarian.agents import (
    OUTPUT_RETRIES,
    LibrarianTask,
    ObservationAnalysis,
    build_librarian_agents,
    pattern_instructions,
    resolve_instructions,
)
from shared.config import DEFAULT_SYSTEM2_TIMEOUT_S
from shared.spend import build_spend_limits, local_clock
from tests.llm_fakes import Script

if TYPE_CHECKING:
    from pydantic_ai_harness.spend import SpendLimits

_ANALYSIS = {
    "entities": ["light.kitchen"],
    "significance": {"safety": 0.0, "novelty": 0.4, "personal": 0.1, "emotional": 0.0},
    "semantic_key": "kitchen light on",
}


def test_agents_are_named_by_role() -> None:
    agents = build_librarian_agents(Script().model)
    names = [
        a.name
        for a in (agents.analyse, agents.extract, agents.resolve, agents.compress, agents.patterns)
    ]
    assert names == [
        "librarian-analyse",
        "librarian-extract",
        "librarian-resolve",
        "librarian-compress",
        "librarian-patterns",
    ]


@pytest.mark.asyncio
async def test_a_request_carries_the_role_header_the_cap_and_the_instructions() -> None:
    script = Script([_ANALYSIS])
    agents = build_librarian_agents(script.model)

    out = await agents.ask(
        agents.analyse,
        "1. kitchen on",
        LibrarianTask("ANALYSE", expected_items=1),
        max_tokens=300,
    )

    assert out == [ObservationAnalysis.model_validate(_ANALYSIS)]
    assert script.settings()["extra_headers"] == {"X-Alfred-Role": "librarian"}
    assert script.settings()["max_tokens"] == 300
    assert script.settings()["timeout"] == DEFAULT_SYSTEM2_TIMEOUT_S
    assert script.instructions() == "ANALYSE"
    assert script.prompt() == "1. kitchen on"


@pytest.mark.asyncio
@pytest.mark.parametrize(("native", "mode"), [(True, "native"), (False, "tool")])
async def test_output_mode_follows_the_model_profile(native: bool, mode: str) -> None:
    script = Script([_ANALYSIS], json_schema_output=native)
    agents = build_librarian_agents(script.model)

    out = await agents.ask(
        agents.analyse, "1. x", LibrarianTask("I", expected_items=1), max_tokens=300
    )

    assert out[0].semantic_key == "kitchen light on"
    assert script.requests[0][1].model_request_parameters.output_mode == mode


@pytest.mark.asyncio
async def test_a_wrong_count_is_retried_once_with_the_expected_count() -> None:
    script = Script([_ANALYSIS], [_ANALYSIS, _ANALYSIS])
    agents = build_librarian_agents(script.model)

    out = await agents.ask(
        agents.analyse,
        "1. a\n2. b",
        LibrarianTask("I", expected_items=2),
        max_tokens=300,
    )

    assert len(out) == 2
    assert OUTPUT_RETRIES == 1
    assert any(
        "Return exactly 2 elements" in r and "you returned 1" in r for r in script.retries(1)
    )


def _limits(cap_usd: float = 5.0) -> SpendLimits[Any]:
    return build_spend_limits(
        fakeredis.FakeAsyncRedis(), cap_usd=cap_usd, clock=local_clock(lambda: "UTC")
    )


async def _counted(spend: SpendLimits[Any]) -> int:
    (status,) = await spend.status()
    return status.spent.requests


@pytest.mark.asyncio
async def test_every_model_request_is_counted_against_the_budget() -> None:
    script = Script([])
    spend = _limits()
    agents = build_librarian_agents(script.model, spend=spend)

    await agents.ask(agents.extract, "- obs", LibrarianTask("I"), max_tokens=500)

    (status,) = await spend.status()
    assert status.spent.requests == 1
    assert status.spent.tokens > 0  # FunctionModel estimates usage when none is given


@pytest.mark.asyncio
async def test_a_run_that_exhausts_its_retries_counts_each_request() -> None:
    script = Script([_ANALYSIS], [_ANALYSIS])
    spend = _limits()
    agents = build_librarian_agents(script.model, spend=spend)

    with pytest.raises(UnexpectedModelBehavior):
        await agents.ask(
            agents.analyse,
            "1. a\n2. b",
            LibrarianTask("I", expected_items=2),
            max_tokens=300,
        )

    assert script.call_count == 2
    assert await _counted(spend) == 2


@pytest.mark.asyncio
async def test_a_spent_budget_refuses_the_request() -> None:
    script = Script([])
    spend = _limits(cap_usd=1.0)
    (status,) = await spend.status()
    await spend.store.add_many(  # type: ignore[union-attr]
        [SpendEntry(key=status.key, usd=Decimal(1), requests=1)]
    )
    agents = build_librarian_agents(script.model, spend=spend)

    with pytest.raises(SpendLimitExceeded):
        await agents.ask(agents.extract, "- obs", LibrarianTask("I"), max_tokens=500)

    assert script.call_count == 0


@pytest.mark.asyncio
async def test_the_hook_runs_after_every_ask_even_a_failed_one() -> None:
    hook = AsyncMock()
    agents = build_librarian_agents(Script([], RuntimeError("gateway down")).model, after_ask=hook)

    await agents.ask(agents.extract, "- obs", LibrarianTask("I"), max_tokens=500)
    with pytest.raises(RuntimeError, match="gateway down"):
        await agents.ask(agents.extract, "- obs", LibrarianTask("I"), max_tokens=500)

    assert hook.await_count == 2


@pytest.mark.asyncio
async def test_a_failed_hook_keeps_the_result() -> None:
    hook = AsyncMock(side_effect=ConnectionError("redis blip"))
    agents = build_librarian_agents(Script([]).model, after_ask=hook)

    out = await agents.ask(agents.extract, "- obs", LibrarianTask("I"), max_tokens=500)

    assert out == []
    hook.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failed_hook_keeps_the_runs_own_exception() -> None:
    hook = AsyncMock(side_effect=ConnectionError("redis blip"))
    agents = build_librarian_agents(Script(RuntimeError("gateway down")).model, after_ask=hook)

    with pytest.raises(RuntimeError, match="gateway down"):
        await agents.ask(agents.extract, "- obs", LibrarianTask("I"), max_tokens=500)


def test_resolve_instructions_state_the_contradiction_threshold() -> None:
    text = resolve_instructions(5, 14)
    assert "at least 5 consistent observations over at least 14 days" in text


def test_pattern_instructions_keep_thresholds_zone_and_the_reflex_paragraph() -> None:
    text = pattern_instructions(3, 7, "America/Los_Angeles")
    assert "at least 3 occurrences" in text
    assert "at least 7 different days" in text
    assert "Entry times are local (America/Los_Angeles)" in text
    assert "System 1 (Reflex Engine)" in text


@pytest.mark.asyncio
async def test_a_request_carries_the_configured_timeout() -> None:
    """A hung provider must not stall a nightly step: System 2's timeout applies."""
    script = Script([_ANALYSIS])
    agents = build_librarian_agents(script.model, timeout_s=12.5)

    await agents.ask(
        agents.analyse, "1. on", LibrarianTask("ANALYSE", expected_items=1), max_tokens=9
    )

    assert script.settings()["timeout"] == 12.5
