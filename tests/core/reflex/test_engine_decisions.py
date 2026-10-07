"""ReflexEngine — one event in, a ReflexProposal out (#285)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from bus.schemas.events import StateChangedEvent, TriggerFired
from core.reflex.context_reader import LIVE_STATE_UNAVAILABLE
from core.reflex.engine import ReflexEngine
from core.reflex.tool_registry import ToolInfo
from sdk.alfred_sdk.context import ContextEntry, ContextSnapshot

# 03:30 UTC on Thu 8 Oct 2026 is 22:30 on Wed 7 Oct in Chicago (CDT, UTC-5).
NIGHT_UTC = datetime(2026, 10, 8, 3, 30, tzinfo=UTC)

TOOLS = [
    ToolInfo(
        name="home.light_turn_on",
        description="light.turn_on",
        parameters={
            "target": {"type": "str", "description": "Available light entities: Lamp A, Lamp B"},
            "brightness_pct": {"type": "float"},
        },
        feature_name="home",
        feature_description="",
        target_service="home-service",
        audience="reflex",
    ),
    ToolInfo(
        name="home.lock_unlock",
        description="lock.unlock",
        parameters={"target": {"type": "str"}},
        feature_name="home",
        feature_description="",
        target_service="home-service",
        audience="conscious",
    ),
]

TV_PLAYS = StateChangedEvent(
    source="home-service",
    domain="home",
    entity_id="media_player.living_room_tv",
    old_state="paused",
    new_state="playing",
    attributes={"friendly_name": "Living Room TV", "media_title": "A Film"},
)

ACT = {
    "decision": "act",
    "reason": "Film at night",
    "tool_name": "home.light_turn_on",
    "target_service": "home-service",
    "parameters": {"target": "Living Room", "brightness_pct": 30},
}


def _snapshot() -> ContextSnapshot:
    return ContextSnapshot(
        controllable={
            "light": [
                ContextEntry(
                    entity_id="light.tv_lamp",
                    state="on",
                    attributes={
                        "friendly_name": "TV Lamp",
                        "area": "Living Room",
                        "brightness": 255,
                    },
                )
            ],
            "media_player": [
                ContextEntry(
                    entity_id="media_player.living_room_tv",
                    state="playing",
                    attributes={"friendly_name": "Living Room TV", "area": "Living Room"},
                )
            ],
            "person": [
                ContextEntry(
                    entity_id="person.a", state="home", attributes={"friendly_name": "Person A"}
                )
            ],
        },
        sensors={"sun": [ContextEntry(entity_id="sun.sun", state="below_horizon")]},
    )


def _engine(
    snapshot: ContextSnapshot | None = None, *, live: bool = True
) -> tuple[ReflexEngine, AsyncMock]:
    registry = AsyncMock()
    registry.get_tools = AsyncMock(return_value=TOOLS)
    reader = None
    if live:
        reader = AsyncMock()
        reader.get_snapshot = AsyncMock(return_value=snapshot)
        reader.get_user_timezone = AsyncMock(return_value="America/Chicago")
    engine = ReflexEngine(
        preferences_dir="/unused",
        tool_registry=registry,
        context_reader=reader,
        clock=lambda: NIGHT_UTC,
    )
    engine._cached_preferences = "- Prefers dim light for films"
    return engine, registry


def _reply(payload: dict[str, object] | str) -> AsyncMock:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return AsyncMock(return_value={"response": text})


async def test_an_act_reply_becomes_a_proposal_with_its_action() -> None:
    engine, _ = _engine(_snapshot())

    with patch("core.reflex.inference.infer", new=_reply(ACT)):
        proposal = await engine.process_event(TV_PLAYS)

    assert proposal.decision == "act"
    assert proposal.action is not None
    assert proposal.action.tool_name == "home.light_turn_on"
    assert proposal.action.reason == "Film at night"


async def test_the_prompt_shows_now_the_house_and_the_change() -> None:
    engine, _ = _engine(_snapshot())
    infer = _reply({"decision": "none"})

    with patch("core.reflex.inference.infer", new=infer):
        await engine.process_event(TV_PLAYS)

    prompt = infer.await_args.args[0]
    assert "## Preferences\n- Prefers dim light for films" in prompt
    assert "## Now\nWed 7 Oct, 22:30 (night) · sun down\nPeople: Person A home" in prompt
    assert "## House\nLiving Room: Living Room TV playing · TV Lamp on 100%" in prompt
    assert '## What changed\nLiving Room TV (Living Room): paused → playing · "A Film"' in prompt


async def test_only_reflex_tools_reach_the_prompt_without_their_entity_lists() -> None:
    engine, _ = _engine(_snapshot())
    infer = _reply({"decision": "none"})

    with patch("core.reflex.inference.infer", new=infer):
        await engine.process_event(TV_PLAYS)

    prompt = infer.await_args.args[0]
    assert "- home.light_turn_on(target, brightness_pct) [home-service]: light.turn_on" in prompt
    assert "home.lock_unlock" not in prompt
    assert "Available light entities" not in prompt


async def test_a_proposal_naming_a_conscious_tool_is_invalid() -> None:
    engine, _ = _engine(_snapshot())

    with patch("core.reflex.inference.infer", new=_reply({**ACT, "tool_name": "home.lock_unlock"})):
        proposal = await engine.process_event(TV_PLAYS)

    assert proposal.decision == "invalid"


async def test_no_live_state_still_decides() -> None:
    engine, _ = _engine(None)
    infer = _reply({"decision": "none"})

    with patch("core.reflex.inference.infer", new=infer):
        proposal = await engine.process_event(TV_PLAYS)

    prompt = infer.await_args.args[0]
    assert f"## Now\nWed 7 Oct, 22:30 (night)\n\n## House\n{LIVE_STATE_UNAVAILABLE}" in prompt
    assert proposal.decision == "none"


async def test_without_a_context_reader_the_clock_is_utc() -> None:
    engine, _ = _engine(live=False)
    infer = _reply({"decision": "none"})

    with patch("core.reflex.inference.infer", new=infer):
        await engine.process_event(TV_PLAYS)

    assert "## Now\nThu 8 Oct, 03:30 (night)" in infer.await_args.args[0]


async def test_tools_are_cached_between_events_until_reloaded() -> None:
    engine, registry = _engine(_snapshot())

    with patch("core.reflex.inference.infer", new=_reply({"decision": "none"})):
        await engine.process_event(TV_PLAYS)
        await engine.process_event(TV_PLAYS)
        assert registry.get_tools.await_count == 1
        await engine.reload_tools()
        await engine.process_event(TV_PLAYS)

    assert registry.get_tools.await_count == 2


async def test_a_model_failure_propagates() -> None:
    engine, _ = _engine(_snapshot())

    with (
        patch("core.reflex.inference.infer", new=AsyncMock(side_effect=ConnectionError("down"))),
        pytest.raises(ConnectionError),
    ):
        await engine.process_event(TV_PLAYS)


async def test_an_unparseable_reply_is_an_invalid_proposal() -> None:
    engine, _ = _engine(_snapshot())

    with patch("core.reflex.inference.infer", new=_reply("Sure! Dimming.")):
        proposal = await engine.process_event(TV_PLAYS)

    assert proposal.decision == "invalid"
    assert proposal.raw == "Sure! Dimming."


async def test_a_trigger_gets_now_and_the_house_and_names_itself() -> None:
    engine, _ = _engine(_snapshot())
    infer = _reply({"decision": "none"})
    trigger = TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time")

    with patch("core.reflex.inference.infer", new=infer):
        proposal = await engine.process_trigger_fired(trigger)

    prompt = infer.await_args.args[0]
    assert "already being notified" in prompt
    assert "## Trigger fired\nbedtime (time)" in prompt
    assert "## House\nLiving Room:" in prompt
    assert "## What changed" not in prompt
    assert proposal.decision == "none"
