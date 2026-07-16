"""Tests for ContextAssembler."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from core.conscious.context_assembler import ContextAssembler
from core.identity.schemas import IdentityResult
from core.memory.vector_store import ContextMetadata, SearchResult


def _sir() -> IdentityResult:
    return IdentityResult(
        identity="sir",
        confidence=0.99,
        method="webauthn",
        factors=["webauthn"],
        risk_clearance="high",
    )


def _guest() -> IdentityResult:
    return IdentityResult(
        identity="guest",
        confidence=1.0,
        method="unauthenticated",
        factors=[],
        risk_clearance="low",
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


@pytest.fixture
def assembler(tmp_path: Path) -> ContextAssembler:
    personality_path = tmp_path / "personality.md"
    personality_path.write_text("You are Alfred, a butler.")
    return ContextAssembler(personality_path=str(personality_path))


def test_assemble_for_sir(assembler: ContextAssembler) -> None:
    prompt = assembler.assemble(
        identity=_sir(),
        tools_section="- smart_home.dim_lights(room, level)",
        integrations_section="available",
        proactivity_level="opinionated",
    )
    assert "Alfred" in prompt
    assert "smart_home.dim_lights" in prompt
    assert "calendar" in prompt  # integration hint
    assert "opinionated" in prompt


def test_assemble_for_guest_excludes_personal(assembler: ContextAssembler) -> None:
    prompt = assembler.assemble(
        identity=_guest(),
        tools_section="- smart_home.dim_lights(room, level)",
        integrations_section="available",
        proactivity_level="moderate",
    )
    assert "Alfred" in prompt
    # Guest should NOT see integrations or proactivity
    assert "calendar" not in prompt
    assert "Proactivity" not in prompt


def test_assemble_includes_relevant_context_for_sir(assembler: ContextAssembler) -> None:
    """Involuntary recall results appear as 'Relevant Context' for sir."""
    results = [
        _make_search_result(content="Sir prefers dim lighting", type_="semantic", score=0.92),
        _make_search_result(content="Lights dimmed at 8pm yesterday", type_="episodic", score=0.75),
    ]
    prompt = assembler.assemble(
        identity=_sir(),
        tools_section="",
        relevant_context=results,
    )
    assert "## Relevant Context" in prompt
    assert "dim lighting" in prompt
    assert "[semantic]" in prompt
    assert "[episodic]" in prompt
    assert "0.92" in prompt


def test_assemble_guest_excludes_relevant_context(assembler: ContextAssembler) -> None:
    """Guest should NOT see relevant context from involuntary recall."""
    results = [_make_search_result(content="Sir's secret preference")]
    prompt = assembler.assemble(
        identity=_guest(),
        tools_section="",
        relevant_context=results,
    )
    assert "Relevant Context" not in prompt
    assert "secret" not in prompt


def test_assemble_empty_sections_omitted(assembler: ContextAssembler) -> None:
    """Empty optional sections don't produce headers in the prompt."""
    prompt = assembler.assemble(
        identity=_sir(),
        tools_section="",
        integrations_section="",
    )
    assert "## Available Tools" not in prompt
    assert "## Integrations" not in prompt
    assert "## Relevant Context" not in prompt


def test_assemble_no_relevant_context_omits_section(assembler: ContextAssembler) -> None:
    """When relevant_context is None or empty, no section is added."""
    prompt = assembler.assemble(
        identity=_sir(),
        tools_section="",
        relevant_context=None,
    )
    assert "## Relevant Context" not in prompt

    prompt2 = assembler.assemble(
        identity=_sir(),
        tools_section="",
        relevant_context=[],
    )
    assert "## Relevant Context" not in prompt2


def test_current_time_rendered_in_user_timezone(assembler: ContextAssembler) -> None:
    """Current Time is rendered as local wall-clock in the requested tz."""
    prompt = assembler.assemble(
        identity=_sir(),
        tools_section="- t: tool",
        now=datetime(2026, 7, 16, 20, 5, 32, tzinfo=UTC),
        tz_name="America/Denver",
    )
    assert "## Current Time" in prompt
    assert "Thursday 2026-07-16T14:05:32-06:00 (America/Denver)" in prompt


def test_current_time_utc_fallback(assembler: ContextAssembler) -> None:
    """Default tz_name of UTC renders the current time unchanged."""
    prompt = assembler.assemble(
        identity=_sir(),
        tools_section="- t: tool",
        now=datetime(2026, 7, 16, 20, 5, 32, tzinfo=UTC),
        tz_name="UTC",
    )
    assert "## Current Time" in prompt
    assert "Thursday 2026-07-16T20:05:32+00:00 (UTC)" in prompt
