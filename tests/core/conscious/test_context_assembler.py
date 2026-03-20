"""Tests for ContextAssembler."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from core.conscious.context_assembler import ContextAssembler
from core.identity.schemas import IdentityResult


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


@pytest.fixture
def assembler(tmp_path: Path) -> ContextAssembler:
    personality_path = tmp_path / "personality.md"
    personality_path.write_text("You are Alfred, a butler.")
    return ContextAssembler(personality_path=str(personality_path))


def test_assemble_for_sir(assembler: ContextAssembler) -> None:
    prompt = assembler.assemble(
        identity=_sir(),
        tools_section="- smart_home.dim_lights(room, level)",
        integrations_section="- calendar: get_today_events",
        preferences_text="Prefers dim lighting after 8pm",
        context_text="Living room light: on",
        history=[],
        proactivity_level="opinionated",
    )
    assert "Alfred" in prompt
    assert "smart_home.dim_lights" in prompt
    assert "Prefers dim lighting" in prompt
    assert "calendar" in prompt
    assert "opinionated" in prompt


def test_assemble_for_guest_excludes_personal(assembler: ContextAssembler) -> None:
    prompt = assembler.assemble(
        identity=_guest(),
        tools_section="- smart_home.dim_lights(room, level)",
        integrations_section="- calendar: get_today_events",
        preferences_text="Prefers dim lighting after 8pm",
        context_text="Living room light: on",
        history=[],
        proactivity_level="moderate",
    )
    assert "Alfred" in prompt
    # Guest should NOT see preferences, integrations, or proactivity
    assert "Prefers dim lighting" not in prompt
    assert "calendar" not in prompt
    assert "Proactivity" not in prompt


def test_assemble_includes_episodic_for_sir(assembler: ContextAssembler) -> None:
    """Non-empty episodic_text produces a 'Recent Events' section for sir."""
    prompt = assembler.assemble(
        identity=_sir(),
        tools_section="",
        integrations_section="",
        preferences_text="",
        context_text="",
        history=[],
        episodic_text="Sir asked about the weather at 10am. Lights dimmed at 8pm.",
    )
    assert "## Recent Events" in prompt
    assert "weather at 10am" in prompt


def test_assemble_includes_procedural_for_sir(assembler: ContextAssembler) -> None:
    """Non-empty procedural_text produces a 'Known Routines' section for sir."""
    prompt = assembler.assemble(
        identity=_sir(),
        tools_section="",
        integrations_section="",
        preferences_text="",
        context_text="",
        history=[],
        procedural_text="evening_movie: Dim lights to 30% at 8pm",
    )
    assert "## Known Routines" in prompt
    assert "evening_movie" in prompt


def test_assemble_guest_excludes_episodic_and_procedural(
    assembler: ContextAssembler,
) -> None:
    """Guest should NOT see episodic or procedural memory, even if provided."""
    prompt = assembler.assemble(
        identity=_guest(),
        tools_section="",
        integrations_section="",
        preferences_text="",
        context_text="",
        history=[],
        episodic_text="Sir asked about weather",
        procedural_text="evening_movie routine",
    )
    assert "Recent Events" not in prompt
    assert "Known Routines" not in prompt
    assert "weather" not in prompt
    assert "evening_movie" not in prompt


def test_assemble_empty_sections_omitted(assembler: ContextAssembler) -> None:
    """Empty optional sections don't produce headers in the prompt."""
    prompt = assembler.assemble(
        identity=_sir(),
        tools_section="",
        integrations_section="",
        preferences_text="",
        context_text="",
        history=[],
    )
    assert "## Available Tools" not in prompt
    assert "## Available Integrations" not in prompt
    assert "## Preferences" not in prompt
    assert "## Current State" not in prompt
    assert "## Recent Events" not in prompt
    assert "## Known Routines" not in prompt
