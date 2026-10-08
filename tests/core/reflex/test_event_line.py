"""Reading back the What changed line, so the eval harness can tell which state change each
System 1 call was about (#298). The line's format has one source: ``core/reflex/prompt.py``."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bus.schemas.events import StateChangedEvent, TriggerFired
from core.reflex.prompt import (
    LiveEntity,
    RenderedEvent,
    build_state_change_prompt,
    build_trigger_prompt,
    parse_event,
    read_state_change,
    render_event,
    state_change_line,
)

NOW = datetime(2026, 10, 8, 3, 30, tzinfo=UTC)


def _change(entity_id: str, old: str | None, new: str, **attributes: object) -> StateChangedEvent:
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id=entity_id,
        old_state=old,
        new_state=new,
        attributes=attributes,
    )


def _live(entity_id: str, **attributes: object) -> dict[str, LiveEntity]:
    entity = LiveEntity(
        entity_id=entity_id,
        domain=entity_id.split(".", 1)[0],
        controllable=True,
        state="on",
        attributes=attributes,
    )
    return {entity_id: entity}


@pytest.mark.parametrize(
    ("event", "entities", "read"),
    [
        (
            _change("media_player.tv", "paused", "playing", media_title="A Film", app_name="App"),
            _live("media_player.tv", friendly_name="Living Room TV", area="Living Room"),
            RenderedEvent(name="Living Room TV", area="Living Room", old="paused", new="playing"),
        ),
        (
            _change("person.alex", "home", "not_home", friendly_name="Alex"),
            None,
            RenderedEvent(name="Alex", area=None, old="home", new="not_home"),
        ),
        (
            _change("person.sam", None, "home", friendly_name="Sam"),
            None,
            RenderedEvent(name="Sam", area=None, old=None, new="home"),
        ),
        (  # no name anywhere: Reflex writes the entity id
            _change("binary_sensor.front_door", "off", "on"),
            {},
            RenderedEvent(name="binary_sensor.front_door", area=None, old="off", new="on"),
        ),
        (  # a name with a colon and a parenthesis, in a room
            _change("light.desk", "off", "on", brightness=128),
            _live("light.desk", friendly_name="Desk: Lamp (left)", area="Study"),
            RenderedEvent(name="Desk: Lamp (left)", area="Study", old="off", new="on"),
        ),
    ],
)
def test_render_event_reads_back(
    event: StateChangedEvent, entities: dict[str, LiveEntity] | None, read: RenderedEvent
) -> None:
    assert parse_event(render_event(event, entities)) == read


def test_a_line_render_event_did_not_write_reads_as_none() -> None:
    assert parse_event("Lamp turned on") is None
    assert parse_event("") is None


def test_read_state_change_finds_the_change_in_the_whole_prompt() -> None:
    prompt = build_state_change_prompt(
        event=_change("person.alex", "home", "not_home", friendly_name="Alex"),
        preferences="## What changed\nNot this: a preference that looks like the heading.",
        tools=[],
        entities=None,
        now=NOW,
        tz_name="UTC",
    )
    assert read_state_change(prompt) == RenderedEvent(
        name="Alex", area=None, old="home", new="not_home"
    )


def test_state_change_line_is_the_line_under_the_heading_read_or_not() -> None:
    event = _change("person.alex", "home", "not_home", friendly_name="Alex")
    prompt = build_state_change_prompt(
        event=event, preferences="", tools=[], entities=None, now=NOW, tz_name="UTC"
    )
    assert state_change_line(prompt) == render_event(event, None)
    odd = "## What changed\nAlex left\n\n## Decision (JSON only):"
    assert state_change_line(odd) == "Alex left" and read_state_change(odd) is None


def test_a_trigger_prompt_is_about_no_state_change() -> None:
    prompt = build_trigger_prompt(
        event=TriggerFired(trigger_id="t1", trigger_name="Laundry", trigger_type="time"),
        preferences="",
        tools=[],
        entities=None,
        now=NOW,
        tz_name="UTC",
    )
    assert read_state_change(prompt) is None
    assert state_change_line(prompt) is None
