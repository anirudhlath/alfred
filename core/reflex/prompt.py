"""Reflex prompt: what the Reflex Engine sees for one event (#285).

Pure: live state, the event and the clock go in, a string comes out, with no I/O.
The sections run from the most stable to the least so vLLM's prefix cache covers as
much as it can: rules and tools, preferences, now, the house by room, then the change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from core.reflex.context_reader import LIVE_STATE_UNAVAILABLE

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from bus.schemas.events import StateChangedEvent, TriggerFired
    from core.reflex.tool_registry import ToolInfo
    from sdk.alfred_sdk.context import ContextSnapshot

# Domains the House section lists. A rendering choice, not a tool list: Reflex acts only
# through its registered tools, but it judges better for seeing the thermostat.
HOUSE_DOMAINS: frozenset[str] = frozenset(
    {"light", "media_player", "switch", "climate", "fan", "cover", "lock", "vacuum"}
)
# Event attributes worth a word on the What changed line, in display order.
EVENT_ATTRIBUTES: tuple[str, ...] = (
    "media_title",
    "media_artist",
    "app_name",
    "brightness",
    "temperature",
    "current_temperature",
    "hvac_action",
)
MAX_EVENT_DETAILS = 3
# One long name, title or trigger context must not bloat the prompt.
MAX_DETAIL_CHARS = 80
MAX_TRIGGER_CONTEXT_CHARS = 500
OTHER_ROOM = "Other"
NO_DEVICES = "No devices to show."
NO_TOOLS = "No tools available."
NO_PREFERENCES = "None recorded yet."

STATE_CHANGE_INTRO = (
    "You are Alfred's Reflex Engine, the quiet steward of a home. One thing in the house "
    "just changed. Decide whether to do something about it."
)
TRIGGER_INTRO = (
    "You are Alfred's Reflex Engine, the quiet steward of a home. A trigger the owner set "
    "up has just fired, and the owner is already being notified about it. Decide whether "
    "the home should also do something."
)
DECISION_RULES = """\
- act: the right move is obvious. Common sense or a stated preference makes it plainly
  what the household wants, and doing it would surprise no one at home.
- ask: a move is plausible, but you are not sure it is wanted.
- none: nothing needs doing. This is the usual answer.

Use only the tools below. Target a room by its name in the House section, or a device by
its name.

Respond with JSON only. Either {"decision": "none"} or
{"decision": "act" | "ask", "reason": "<one short sentence>", "tool_name": "...",
 "target_service": "...", "parameters": {...}}"""


@dataclass(frozen=True)
class LiveEntity:
    """One live-state entry, lifted out of its snapshot bucket."""

    entity_id: str
    domain: str
    controllable: bool
    state: str
    attributes: Mapping[str, Any]


def index_snapshot(snapshot: ContextSnapshot) -> dict[str, LiveEntity]:
    """Every entry of both buckets, keyed by entity ID."""
    index: dict[str, LiveEntity] = {}
    for controllable, bucket in ((True, snapshot.controllable), (False, snapshot.sensors)):
        for domain, entries in bucket.items():
            for entry in entries:
                index[entry.entity_id] = LiveEntity(
                    entity_id=entry.entity_id,
                    domain=domain,
                    controllable=controllable,
                    state=entry.state,
                    attributes=entry.attributes,
                )
    return index


def _clip(value: object, limit: int = MAX_DETAIL_CHARS) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _name(entity_id: str, attributes: Mapping[str, Any]) -> str:
    name = attributes.get("friendly_name")
    return _clip(name) if isinstance(name, str) and name.strip() else entity_id


def _area(attributes: Mapping[str, Any]) -> str | None:
    area = attributes.get("area")
    return _clip(area) if isinstance(area, str) and area.strip() else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _percent(brightness: object) -> str | None:
    value = _number(brightness)
    return None if value is None else f"{round(value / 255 * 100)}%"


def _degrees(value: object) -> str | None:
    number = _number(value)
    return None if number is None else f"{number:g}°"


def time_of_day(hour: int) -> str:
    """A coarse label for a local hour, so the model need not reason about 22:57."""
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def _zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:  # unknown, empty or path-like key
        return ZoneInfo("UTC")


def render_now(now: datetime, tz_name: str, entities: Mapping[str, LiveEntity] | None) -> str:
    """Local time and time of day, then the sun and who is where, when live state has them."""
    local = now.astimezone(_zone(tz_name))
    clock = f"{local:%a} {local.day} {local:%b}, {local:%H:%M} ({time_of_day(local.hour)})"
    if entities is None:
        return clock
    sun = entities.get("sun.sun")
    if sun is not None and sun.state in ("above_horizon", "below_horizon"):
        clock += " · sun up" if sun.state == "above_horizon" else " · sun down"
    people = sorted(
        (e for e in entities.values() if e.domain == "person"),
        key=lambda e: _name(e.entity_id, e.attributes).casefold(),
    )
    if not people:
        return clock
    who = " · ".join(f"{_name(p.entity_id, p.attributes)} {_clip(p.state)}" for p in people)
    return f"{clock}\nPeople: {who}"


def _house_detail(entity: LiveEntity) -> str | None:
    attributes = entity.attributes
    if entity.domain == "light" and entity.state == "on":
        return _percent(attributes.get("brightness"))
    if entity.domain == "media_player" and entity.state == "playing":
        title = attributes.get("media_title")
        return f'"{_clip(title)}"' if isinstance(title, str) and title.strip() else None
    if entity.domain == "climate":
        target = _degrees(attributes.get("temperature"))
        current = _degrees(attributes.get("current_temperature"))
        if target and current:
            return f"{target} (now {current})"
        if target:
            return target
        return f"now {current}" if current else None
    return None


def _house_item(entity: LiveEntity) -> str:
    text = f"{_name(entity.entity_id, entity.attributes)} {_clip(entity.state)}"
    detail = _house_detail(entity)
    return f"{text} {detail}" if detail else text


def render_house(entities: Mapping[str, LiveEntity] | None) -> str:
    """The actionable house, one line per room; entities with no room come last."""
    if entities is None:
        return LIVE_STATE_UNAVAILABLE
    rooms: dict[str, list[LiveEntity]] = {}
    for entity in entities.values():
        if entity.controllable and entity.domain in HOUSE_DOMAINS:
            rooms.setdefault(_area(entity.attributes) or OTHER_ROOM, []).append(entity)
    if not rooms:
        return NO_DEVICES
    order = sorted(room for room in rooms if room != OTHER_ROOM)
    if OTHER_ROOM in rooms:
        order.append(OTHER_ROOM)
    lines: list[str] = []
    for room in order:
        members = sorted(
            rooms[room],
            key=lambda e: (_name(e.entity_id, e.attributes).casefold(), e.entity_id),
        )
        lines.append(f"{room}: " + " · ".join(_house_item(e) for e in members))
    return "\n".join(lines)


def _event_detail(key: str, value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if key == "brightness":
        return _percent(value)
    if key == "temperature":
        degrees = _degrees(value)
        return f"set {degrees}" if degrees else None
    if key == "current_temperature":
        degrees = _degrees(value)
        return f"now {degrees}" if degrees else None
    text = _clip(value)
    if not text:
        return None
    return f'"{text}"' if key == "media_title" else text


def render_event(event: StateChangedEvent, entities: Mapping[str, LiveEntity] | None) -> str:
    """One line: name (room): old → new, then up to three details from the event."""
    known = entities.get(event.entity_id) if entities is not None else None
    # Live state names and places the entity; the event carries only HA's raw attributes.
    attributes = {**event.attributes, **(known.attributes if known is not None else {})}
    name = _name(event.entity_id, attributes)
    area = _area(attributes)
    head = f"{name} ({area})" if area else name
    old = _clip(event.old_state) if event.old_state is not None else "(none)"
    line = f"{head}: {old} → {_clip(event.new_state)}"
    details = [
        detail
        for key in EVENT_ATTRIBUTES
        if (detail := _event_detail(key, event.attributes.get(key))) is not None
    ][:MAX_EVENT_DETAILS]
    return " · ".join([line, *details])


def render_trigger(event: TriggerFired) -> str:
    """The trigger's name and type, then its context as compact JSON."""
    line = f"{_clip(event.trigger_name)} ({_clip(event.trigger_type)})"
    if not event.context:
        return line
    context = json.dumps(event.context, sort_keys=True, default=str)
    return f"{line}\nContext: {_clip(context, MAX_TRIGGER_CONTEXT_CHARS)}"


def render_tools(tools: Sequence[ToolInfo]) -> str:
    """One line per tool, sorted. Parameter descriptions stay out: they list every entity."""
    if not tools:
        return NO_TOOLS
    lines = ["Tools:"]
    for tool in sorted(tools, key=lambda t: t.name):
        line = f"- {tool.name}({', '.join(tool.parameters)}) [{tool.target_service}]"
        lines.append(f"{line}: {tool.description}" if tool.description else line)
    return "\n".join(lines)


def _assemble(
    intro: str,
    *,
    preferences: str,
    tools: Sequence[ToolInfo],
    entities: Mapping[str, LiveEntity] | None,
    now: datetime,
    tz_name: str,
    change_heading: str,
    change: str,
) -> str:
    # Stable → volatile. Keep this order: it is what lets vLLM reuse the prefix.
    return "\n\n".join(
        [
            f"{intro}\n\n{DECISION_RULES}\n\n{render_tools(tools)}",
            f"## Preferences\n{preferences.strip() or NO_PREFERENCES}",
            f"## Now\n{render_now(now, tz_name, entities)}",
            f"## House\n{render_house(entities)}",
            f"## {change_heading}\n{change}",
            "## Decision (JSON only):",
        ]
    )


def build_state_change_prompt(
    *,
    event: StateChangedEvent,
    preferences: str,
    tools: Sequence[ToolInfo],
    entities: Mapping[str, LiveEntity] | None,
    now: datetime,
    tz_name: str,
) -> str:
    """The Reflex prompt for one state change."""
    return _assemble(
        STATE_CHANGE_INTRO,
        preferences=preferences,
        tools=tools,
        entities=entities,
        now=now,
        tz_name=tz_name,
        change_heading="What changed",
        change=render_event(event, entities),
    )


def build_trigger_prompt(
    *,
    event: TriggerFired,
    preferences: str,
    tools: Sequence[ToolInfo],
    entities: Mapping[str, LiveEntity] | None,
    now: datetime,
    tz_name: str,
) -> str:
    """The Reflex prompt for a trigger that fired; the owner is already being notified."""
    return _assemble(
        TRIGGER_INTRO,
        preferences=preferences,
        tools=tools,
        entities=entities,
        now=now,
        tz_name=tz_name,
        change_heading="Trigger fired",
        change=render_trigger(event),
    )
