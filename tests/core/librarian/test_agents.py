"""The Librarian's agents: typed output, the role header, retries and spend."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.librarian.agents import (
    OUTPUT_RETRIES,
    LibrarianTask,
    ObservationAnalysis,
    build_librarian_agents,
    pattern_instructions,
    resolve_instructions,
)
from shared.agents import model_key
from tests.llm_fakes import Script

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
        cost=None,
    )

    assert out == [ObservationAnalysis.model_validate(_ANALYSIS)]
    assert script.settings()["extra_headers"] == {"X-Alfred-Role": "librarian"}
    assert script.settings()["max_tokens"] == 300
    assert script.instructions() == "ANALYSE"
    assert script.prompt() == "1. kitchen on"


@pytest.mark.asyncio
@pytest.mark.parametrize(("native", "mode"), [(True, "native"), (False, "tool")])
async def test_output_mode_follows_the_model_profile(native: bool, mode: str) -> None:
    script = Script([_ANALYSIS], json_schema_output=native)
    agents = build_librarian_agents(script.model)

    out = await agents.ask(
        agents.analyse, "1. x", LibrarianTask("I", expected_items=1), max_tokens=300, cost=None
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
        cost=None,
    )

    assert len(out) == 2
    assert OUTPUT_RETRIES == 1
    assert any(
        "Return exactly 2 elements" in r and "you returned 1" in r for r in script.retries(1)
    )


@pytest.mark.asyncio
async def test_spend_is_recorded_without_counting_a_request() -> None:
    script = Script([])
    agents = build_librarian_agents(script.model)
    cost = AsyncMock()

    await agents.ask(agents.extract, "- obs", LibrarianTask("I"), max_tokens=500, cost=cost)

    cost.record_spend.assert_awaited_once()
    args, kwargs = cost.record_spend.await_args
    assert args[0] > 0 and args[1] > 0  # FunctionModel estimates usage when none is given
    assert args[2] == model_key(script.model)
    assert kwargs == {"count_request": False}


@pytest.mark.asyncio
async def test_a_failed_run_still_records_its_spend() -> None:
    script = Script(RuntimeError("gateway down"))
    agents = build_librarian_agents(script.model)
    cost = AsyncMock()

    with pytest.raises(RuntimeError, match="gateway down"):
        await agents.ask(agents.extract, "- obs", LibrarianTask("I"), max_tokens=500, cost=cost)

    cost.record_spend.assert_awaited_once()


def test_resolve_instructions_state_the_contradiction_threshold() -> None:
    text = resolve_instructions(5, 14)
    assert "at least 5 consistent observations over at least 14 days" in text


def test_pattern_instructions_keep_thresholds_zone_and_the_reflex_paragraph() -> None:
    text = pattern_instructions(3, 7, "America/Los_Angeles")
    assert "at least 3 occurrences" in text
    assert "at least 7 different days" in text
    assert "Entry times are local (America/Los_Angeles)" in text
    assert "System 1 (Reflex Engine)" in text
