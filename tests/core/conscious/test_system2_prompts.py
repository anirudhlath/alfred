"""System 2's prompt: which sections a turn gets, from the store, assembled as before."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.conscious.context_assembler import assemble
from core.conscious.system2_prompts import (
    System2Prompts,
    fetch_system2_prompts,
    repo_system2_prompts,
)
from core.memory.vector_store import ContextMetadata, SearchResult
from shared.prompts.names import SYSTEM2_IDENTITY_SIR, SYSTEM2_PERSONALITY
from shared.prompts.store import PromptStore
from tests.shared.prompts.fakes import FakeLangfuse

GOLDEN = Path(__file__).parent / "golden"
NOW = datetime(2026, 10, 14, 18, 30, tzinfo=UTC)
RECALL = [
    SearchResult(
        id="sr-1",
        score=0.82,
        content="Sir prefers dim lighting after 8pm",
        semantic_key="dim lighting",
        metadata=ContextMetadata(
            type="semantic",
            source="t",
            entities="",
            timestamp=0.0,
            significance=1.0,
            retrieval_count=0,
        ),
    )
]


@pytest.mark.parametrize(
    ("golden", "turn", "data"),
    [
        (
            "sir_voice_full",
            {
                "who": "sir",
                "channel": "satellite",
                "content_type": "audio",
                "area": "kitchen",
                "integrations_available": True,
            },
            {
                "relevant_context": RECALL,
                "tz_name": "America/Denver",
                "routine_hint": "Routine hint: evening_dim",
            },
        ),
        (
            "sir_text_plain",
            {
                "who": "sir",
                "channel": "web_pwa",
                "content_type": "text",
                "area": None,
                "integrations_available": False,
            },
            {},
        ),
        (
            "guest_voice_area",
            {
                "who": "guest",
                "channel": "satellite",
                "content_type": "audio",
                "area": "office",
                "integrations_available": True,
            },
            {"relevant_context": RECALL},
        ),
    ],
)
def test_the_repo_defaults_assemble_todays_prompt(
    golden: str, turn: dict[str, object], data: dict[str, object]
) -> None:
    prompts = repo_system2_prompts(**turn)  # type: ignore[arg-type]
    who = str(turn["who"])
    text = assemble(prompts, who=who, now=NOW, **data)  # type: ignore[arg-type]
    assert text == (GOLDEN / f"{golden}.txt").read_text(encoding="utf-8")


def test_a_text_turn_on_a_voice_channel_gets_no_voice_section() -> None:
    prompts = repo_system2_prompts(
        who="sir",
        channel="satellite",
        content_type="text",
        area=None,
        integrations_available=False,
    )
    assert prompts.voice_delivery is None


def test_a_guest_gets_no_integrations_or_proactivity() -> None:
    prompts = repo_system2_prompts(
        who="guest",
        channel="web_pwa",
        content_type="text",
        area=None,
        integrations_available=True,
    )
    assert (prompts.integrations, prompts.proactivity) == (None, None)


def test_refs_put_the_personality_first() -> None:
    prompts = repo_system2_prompts(
        who="sir", channel="web_pwa", content_type="text", area=None, integrations_available=False
    )
    refs = prompts.refs()
    assert refs[0].name == SYSTEM2_PERSONALITY
    assert SYSTEM2_IDENTITY_SIR in {r.name for r in refs}


@pytest.mark.asyncio
async def test_fetch_serves_langfuse_text_for_each_section() -> None:
    store = PromptStore(langfuse=FakeLangfuse({SYSTEM2_PERSONALITY: "You are Jeeves."}))
    prompts = await fetch_system2_prompts(
        store,
        who="sir",
        channel="web_pwa",
        content_type="text",
        area=None,
        integrations_available=False,
    )
    assert isinstance(prompts, System2Prompts)
    assert (prompts.personality.text, prompts.personality.source) == ("You are Jeeves.", "langfuse")
    assert prompts.identity.source == "repo"  # unseeded in the fake
