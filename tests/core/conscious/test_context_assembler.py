"""Tests for ContextAssembler."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from core.conscious.context_assembler import ContextAssembler
from core.identity.schemas import IdentityResult


@pytest.fixture
def assembler(tmp_path: Path) -> ContextAssembler:
    personality_path = tmp_path / "personality.md"
    personality_path.write_text("You are Alfred, a butler.")
    return ContextAssembler(personality_path=str(personality_path))


def test_assemble_for_sir(assembler: ContextAssembler) -> None:
    identity = IdentityResult(
        identity="sir",
        confidence=0.99,
        method="webauthn",
        factors=["webauthn"],
        risk_clearance="high",
    )
    prompt = assembler.assemble(
        identity=identity,
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


def test_assemble_for_guest_excludes_personal(assembler: ContextAssembler) -> None:
    identity = IdentityResult(
        identity="guest",
        confidence=1.0,
        method="unauthenticated",
        factors=[],
        risk_clearance="low",
    )
    prompt = assembler.assemble(
        identity=identity,
        tools_section="- smart_home.dim_lights(room, level)",
        integrations_section="",
        preferences_text="Prefers dim lighting after 8pm",
        context_text="Living room light: on",
        history=[],
        proactivity_level="moderate",
    )
    assert "Alfred" in prompt
    # Guest should NOT see preferences or integrations
    assert "Prefers dim lighting" not in prompt
    assert "calendar" not in prompt
