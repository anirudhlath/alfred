"""Tests for ``assemble``: the turn's data placed between the rendered prose sections."""

from __future__ import annotations

from datetime import UTC, datetime

from core.conscious.context_assembler import assemble
from core.conscious.system2_prompts import System2Prompts, repo_system2_prompts
from core.memory.vector_store import ContextMetadata, SearchResult
from shared.prompts.names import SYSTEM2_PERSONALITY
from shared.prompts.template import repo_text

PERSONALITY_FIRST_LINE = repo_text(SYSTEM2_PERSONALITY).splitlines()[0]


def _prompts(
    who: str = "sir",
    *,
    integrations_available: bool = False,
    proactivity_level: str = "opinionated",
) -> System2Prompts:
    return repo_system2_prompts(
        who=who,
        channel="",
        content_type="text",
        area=None,
        integrations_available=integrations_available,
        proactivity_level=proactivity_level,
    )


def _make_search_result(
    content: str = "test content",
    type_: str = "semantic",
    source: str = "test",
    score: float = 0.85,
) -> SearchResult:
    return SearchResult(
        id="sr-1",
        score=score,
        content=content,
        semantic_key=content,
        metadata=ContextMetadata(
            type=type_,
            source=source,
            entities="",
            timestamp=0.0,
            significance=1.0,
            retrieval_count=0,
        ),
    )


def test_assemble_for_sir() -> None:
    prompts = _prompts(integrations_available=True, proactivity_level="opinionated")
    prompt = assemble(prompts, who="sir")
    assert PERSONALITY_FIRST_LINE in prompt
    assert "Available Tools" not in prompt
    assert "calendar" in prompt  # integration hint
    # The personality mentions "opinionated" too; the section is what this turn adds.
    assert "## Proactivity Level: opinionated" in prompt


def test_assemble_for_guest_excludes_personal() -> None:
    prompts = _prompts("guest", integrations_available=True, proactivity_level="moderate")
    prompt = assemble(prompts, who="guest")
    assert PERSONALITY_FIRST_LINE in prompt
    # Guest should NOT see integrations or proactivity
    assert "calendar" not in prompt
    assert "Proactivity" not in prompt


def test_assemble_includes_relevant_context_for_sir() -> None:
    """Involuntary recall results appear as 'Relevant Context' for sir."""
    results = [
        _make_search_result(content="Sir prefers dim lighting", type_="semantic", score=0.92),
        _make_search_result(content="Lights dimmed at 8pm yesterday", type_="episodic", score=0.75),
    ]
    prompt = assemble(_prompts(), who="sir", relevant_context=results)
    assert "## Relevant Context" in prompt
    assert "dim lighting" in prompt
    assert "[semantic]" in prompt
    assert "[episodic]" in prompt
    assert "0.92" in prompt


def test_assemble_guest_excludes_relevant_context() -> None:
    """Guest should NOT see relevant context from involuntary recall."""
    results = [_make_search_result(content="Sir's secret preference")]
    prompt = assemble(_prompts("guest"), who="guest", relevant_context=results)
    assert "Relevant Context" not in prompt
    assert "secret" not in prompt


def test_assemble_empty_sections_omitted() -> None:
    """Empty optional sections don't produce headers in the prompt."""
    prompt = assemble(_prompts(integrations_available=False), who="sir")
    assert "## Available Tools" not in prompt
    assert "## Integrations" not in prompt
    assert "## Relevant Context" not in prompt


def test_assemble_no_relevant_context_omits_section() -> None:
    """When relevant_context is None or empty, no section is added."""
    prompt = assemble(_prompts(), who="sir", relevant_context=None)
    assert "## Relevant Context" not in prompt

    prompt2 = assemble(_prompts(), who="sir", relevant_context=[])
    assert "## Relevant Context" not in prompt2


def test_current_time_rendered_in_user_timezone() -> None:
    """Current Time is rendered as local wall-clock in the requested tz."""
    prompt = assemble(
        _prompts(),
        who="sir",
        now=datetime(2026, 7, 16, 20, 5, 32, tzinfo=UTC),
        tz_name="America/Denver",
    )
    assert "## Current Time" in prompt
    assert "Thursday 2026-07-16T14:05:32-06:00 (America/Denver)" in prompt


def test_current_time_utc_fallback() -> None:
    """Default tz_name of UTC renders the current time unchanged."""
    prompt = assemble(
        _prompts(),
        who="sir",
        now=datetime(2026, 7, 16, 20, 5, 32, tzinfo=UTC),
        tz_name="UTC",
    )
    assert "## Current Time" in prompt
    assert "Thursday 2026-07-16T20:05:32+00:00 (UTC)" in prompt


def test_routine_hint_ends_the_prompt() -> None:
    prompt = assemble(_prompts(), who="sir", routine_hint="[routine-suggestion] Dim.")
    assert prompt.endswith("\n\n[routine-suggestion] Dim.")


def test_no_routine_hint_adds_nothing() -> None:
    assert assemble(_prompts(), who="sir") == assemble(_prompts(), who="sir", routine_hint="")
