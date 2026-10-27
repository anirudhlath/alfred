"""Reflex's prose lives in the prompt store, and its repo defaults are today's prose.

The fixtures under ``fixtures/`` are the prompts the builders rendered before the prose
moved out of ``core/reflex/prompt.py``. Reflex is in its shadow period, so moving the text
must not change a byte of what the model sees.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from bus.schemas.events import StateChangedEvent, TriggerFired
from core.reflex.engine import ReflexEngine
from core.reflex.prompt import (
    LiveEntity,
    ReflexProse,
    build_state_change_prompt,
    build_trigger_prompt,
)
from core.reflex.prose import fetch_prose, repo_prose
from core.reflex.tool_registry import ToolInfo
from shared.prompts.names import PROMPTS, REFLEX_RULES, REFLEX_STATE_CHANGE, REFLEX_TRIGGER
from shared.prompts.store import PromptStore
from tests.core.reflex.fakes import reflex_agent
from tests.shared.prompts.fakes import FakeLangfuse

if TYPE_CHECKING:
    from tests.llm_fakes import Script

FIXTURES = Path(__file__).parent / "fixtures"
NIGHT_UTC = datetime(2026, 10, 8, 3, 30, tzinfo=UTC)


def _tool(service: str, *params: str, description: str = "") -> ToolInfo:
    return ToolInfo(
        name="home." + service.replace(".", "_"),
        description=service,
        feature_name="home",
        feature_description="",
        target_service="home-service",
        audience="reflex",
        input_schema={
            "type": "object",
            "properties": {p: {"type": "string", "description": description} for p in params},
            "required": list(params),
        },
    )


TOOLS = [
    _tool("switch.turn_off", "target"),
    _tool(
        "light.turn_on",
        "target",
        "brightness_pct",
        description="Available light entities: Lamp A, Lamp B",
    ),
]
ENTITIES = {
    "light.tv_lamp": LiveEntity(
        "light.tv_lamp", "light", True, "on", {"friendly_name": "TV Lamp", "area": "Living Room"}
    )
}
STATE_CHANGE = StateChangedEvent(
    source="home-service",
    domain="home",
    entity_id="light.tv_lamp",
    old_state="off",
    new_state="on",
)
TRIGGER = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")


def _state_change_prompt(prose: ReflexProse) -> str:
    return build_state_change_prompt(
        event=STATE_CHANGE,
        preferences="- Prefers dim light for films",
        tools=TOOLS,
        entities=ENTITIES,
        now=NIGHT_UTC,
        tz_name="UTC",
        prose=prose,
    )


def _trigger_prompt(prose: ReflexProse) -> str:
    return build_trigger_prompt(
        event=TRIGGER,
        preferences="",
        tools=TOOLS,
        entities=ENTITIES,
        now=NIGHT_UTC,
        tz_name="UTC",
        prose=prose,
    )


def test_repo_prose_renders_todays_prompts() -> None:
    state_change = _state_change_prompt(repo_prose("state_change"))
    trigger = _trigger_prompt(repo_prose("trigger_fired"))

    assert state_change == (FIXTURES / "prompt_state_change.txt").read_text(encoding="utf-8")
    assert trigger == (FIXTURES / "prompt_trigger.txt").read_text(encoding="utf-8")


def test_reflex_prompts_take_no_variables() -> None:
    for name in (REFLEX_STATE_CHANGE, REFLEX_TRIGGER, REFLEX_RULES):
        assert PROMPTS[name] == frozenset(), name


@pytest.mark.asyncio
async def test_the_origin_picks_the_intro_and_the_rules_are_shared() -> None:
    store = PromptStore(
        langfuse=FakeLangfuse(  # type: ignore[arg-type]
            {
                REFLEX_STATE_CHANGE: "STATE CHANGE INTRO",
                REFLEX_TRIGGER: "TRIGGER INTRO",
                REFLEX_RULES: "THE RULES",
            }
        )
    )

    assert await fetch_prose(store, "state_change") == ReflexProse(
        "STATE CHANGE INTRO", "THE RULES"
    )
    assert await fetch_prose(store, "trigger_fired") == ReflexProse("TRIGGER INTRO", "THE RULES")


@pytest.mark.asyncio
async def test_a_store_with_no_langfuse_serves_the_repo_prose() -> None:
    for origin in ("state_change", "trigger_fired"):
        assert await fetch_prose(PromptStore(), origin) == repo_prose(origin)  # type: ignore[arg-type]


def _engine(prompts: PromptStore | None) -> tuple[ReflexEngine, Script]:
    registry = AsyncMock()
    registry.get_tools = AsyncMock(return_value=TOOLS)
    agent, script = reflex_agent('{"decision": "none"}', '{"decision": "none"}')
    engine = ReflexEngine(
        preferences_dir="/unused",
        tool_registry=registry,
        agent=agent,
        clock=lambda: NIGHT_UTC,
        prompts=prompts,
    )
    engine._cached_preferences = ""
    return engine, script


@pytest.mark.asyncio
async def test_the_engine_prompts_with_the_stores_prose_on_both_paths() -> None:
    store = PromptStore(
        langfuse=FakeLangfuse(  # type: ignore[arg-type]
            {
                REFLEX_STATE_CHANGE: "EDITED STATE CHANGE INTRO",
                REFLEX_TRIGGER: "EDITED TRIGGER INTRO",
                REFLEX_RULES: "EDITED RULES",
            }
        )
    )
    engine, script = _engine(store)

    await engine.process_event(STATE_CHANGE)
    await engine.process_trigger_fired(TRIGGER)

    state_change_prompt, trigger_prompt = script.prompt(0), script.prompt(1)
    assert state_change_prompt.startswith("EDITED STATE CHANGE INTRO\n\nEDITED RULES\n\nTools:")
    assert trigger_prompt.startswith("EDITED TRIGGER INTRO\n\nEDITED RULES\n\nTools:")


@pytest.mark.asyncio
async def test_without_a_store_the_engine_prompts_with_the_repo_prose() -> None:
    engine, script = _engine(None)

    await engine.process_event(STATE_CHANGE)
    await engine.process_trigger_fired(TRIGGER)

    state_change_prompt, trigger_prompt = script.prompt(0), script.prompt(1)
    state_change_prose, trigger_prose = repo_prose("state_change"), repo_prose("trigger_fired")
    assert state_change_prompt.startswith(
        f"{state_change_prose.intro}\n\n{state_change_prose.rules}"
    )
    assert trigger_prompt.startswith(f"{trigger_prose.intro}\n\n{trigger_prose.rules}")
