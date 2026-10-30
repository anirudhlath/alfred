"""Reflex prompt: what the Reflex Engine sees for one event (#285).

Pure: live state, the event and the clock go in, a string comes out, with no I/O.
The sections run from the most stable to the least so vLLM's prefix cache covers as
much as it can: rules and tools, preferences, now, the house by room, then the change.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from core.attribution.cause import cause_words
from core.reflex.availability import UNAVAILABLE_STATES
from core.reflex.context_reader import LIVE_STATE_UNAVAILABLE
from core.reflex.tool_registry import REFLEX_AUDIENCE, ToolInfo

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from bus.schemas.events import StateChangedEvent, TriggerFired
    from sdk.alfred_sdk.context import ContextSnapshot

# Domains the House section always lists. A rendering choice, not a tool list: Reflex acts only
# through its registered tools, but it judges better for seeing the thermostat. The House also
# shows what the prompt's own Reflex tools can act on (see ``render_house``), so a domain Reflex
# may only ask about needs no place in this set.
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
WEATHER_DOMAIN = "weather"
OTHER_ROOM = "Other"
NO_DEVICES = "No devices to show."
NO_TOOLS = "No tools available."
NO_PREFERENCES = "None recorded yet."
# The What changed line: "{name} ({room}): {old} → {new} · {cause} · {detail} · …".
STATE_CHANGE_HEADING = "What changed"
NO_OLD_STATE = "(none)"
# The last line of the Now section when a person has just changed something.
LEAVE_ALONE_HEADING = "Leave alone, a person just changed: "
_ARROW = " → "
_DETAIL_SEP = " · "
_HEAD = re.compile(r"(?P<name>.+) \((?P<area>[^()]+)\)")
# The tools section: "Tools:", then one "- name(parameters) [service]: description" line each.
_TOOLS_HEADING = "\n\nTools:\n"
_PREFERENCES_HEADING = "\n\n## Preferences\n"
_TOOL_LINE = re.compile(
    r"- (?P<name>[^\s(]+)\((?P<parameters>[^)]*)\) \[(?P<service>[^\]]*)\]"
    r"(?:: (?P<description>.*))?"
)


@dataclass(frozen=True)
class ReflexProse:
    """The prose around the data: the intro that says what happened, and the decision rules.

    Both come from the prompt store (``core.reflex.prose``); this module only places them.
    """

    intro: str
    rules: str


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
    try:
        number = float(value)
    except OverflowError:  # an int too large for a float
        return None
    return number if math.isfinite(number) else None


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


def _head(entity_id: str, attributes: Mapping[str, Any]) -> str:
    """How an entity is named for the model: ``Name (Room)``, or just ``Name`` with no room."""
    name = _name(entity_id, attributes)
    area = _area(attributes)
    return f"{name} ({area})" if area else name


def _leave_alone(entity_ids: Sequence[str], entities: Mapping[str, LiveEntity] | None) -> str:
    """The Leave alone line: what a person just changed, named as the House block names it.

    An entity the live state does not hold is named by its id.
    """
    known = entities or {}
    heads = [
        _head(entity_id, known[entity_id].attributes) if entity_id in known else entity_id
        for entity_id in entity_ids
    ]
    return f"{LEAVE_ALONE_HEADING}{', '.join(sorted(heads, key=lambda h: (h.casefold(), h)))}"


def _weather(entities: Mapping[str, LiveEntity]) -> str | None:
    """The weather condition, as the model reads it: the state of the first ``weather`` entity
    by ID, with HA's hyphens turned to spaces (``clear-night`` is ``clear night``).

    None when the house has no weather entity or the first says nothing.
    """
    first = min(
        (e for e in entities.values() if e.domain == WEATHER_DOMAIN),
        key=lambda e: e.entity_id,
        default=None,
    )
    if first is None or first.state in UNAVAILABLE_STATES:
        return None
    return _clip(first.state.replace("-", " ")) or None


def render_now(
    now: datetime,
    tz_name: str,
    entities: Mapping[str, LiveEntity] | None,
    leave_alone: Sequence[str] = (),
) -> str:
    """Local time and time of day, then the sun and the weather, and who is where, when live
    state has them.

    *leave_alone* is the entity IDs a person just changed; they get a last line of their own.
    With none, the section is what it was.
    """
    local = now.astimezone(_zone(tz_name))
    clock = f"{local:%a} {local.day} {local:%b}, {local:%H:%M} ({time_of_day(local.hour)})"
    lines = [clock]
    if entities is not None:
        sun = entities.get("sun.sun")
        if sun is not None and sun.state in ("above_horizon", "below_horizon"):
            lines[0] += " · sun up" if sun.state == "above_horizon" else " · sun down"
        if (weather := _weather(entities)) is not None:
            lines[0] += f"{_DETAIL_SEP}{weather}"
        people = sorted(
            (e for e in entities.values() if e.domain == "person"),
            key=lambda e: _name(e.entity_id, e.attributes).casefold(),
        )
        if people:
            who = " · ".join(f"{_name(p.entity_id, p.attributes)} {_clip(p.state)}" for p in people)
            lines.append(f"People: {who}")
    if leave_alone:
        lines.append(_leave_alone(leave_alone, entities))
    return "\n".join(lines)


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


def split_tool(tool_name: str, snapshot: Mapping[str, LiveEntity]) -> tuple[str, str] | None:
    """The tool's HA domain and service, or ``None`` when no snapshot domain starts its name.

    The domain is the longest one in the snapshot that the tool's last dotted segment starts
    with (``media_player_media_pause`` is ``media_player``, not a shorter prefix of it).
    """
    segment = tool_name.rsplit(".", 1)[-1]
    domains = {entity.domain for entity in snapshot.values()}
    domain = max((d for d in domains if segment.startswith(f"{d}_")), key=len, default=None)
    return None if domain is None else (domain, segment.removeprefix(f"{domain}_"))


def render_house(entities: Mapping[str, LiveEntity] | None, tools: Sequence[ToolInfo] = ()) -> str:
    """The actionable house, one line per room; entities with no room come last.

    It lists the controllable entities of ``HOUSE_DOMAINS`` and of every domain one of *tools*
    acts on (``split_tool``), so Reflex sees what its own tools can reach. A tool whose name
    starts with no domain of the live state adds nothing.
    """
    if entities is None:
        return LIVE_STATE_UNAVAILABLE
    shown = HOUSE_DOMAINS | {
        split[0] for tool in tools if (split := split_tool(tool.name, entities)) is not None
    }
    rooms: dict[str, list[LiveEntity]] = {}
    for entity in entities.values():
        if entity.controllable and entity.domain in shown:
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


def _cause(event: StateChangedEvent, entities: Mapping[str, LiveEntity] | None) -> str | None:
    """Who caused the change ("by Alex"), or None when the event carries no context.
    A person is named by their live-state friendly name; without one, ``cause_words``
    falls back to the context's person entity, then to "a person"."""
    context = event.context
    if context is None:
        return None
    person = entities.get(context.person) if entities is not None and context.person else None
    friendly = person.attributes.get("friendly_name") if person is not None else None
    name = _clip(friendly) if isinstance(friendly, str) and friendly.strip() else None
    if name is None and context.person is not None and context.person.strip():
        name = _clip(context.person)  # the entity id stands in for the name, and is clipped alike
    return cause_words(context, name)


def render_change(event: StateChangedEvent, entities: Mapping[str, LiveEntity] | None) -> str:
    """The change itself, as ``render_event`` opens its line: ``name (room): old → new``."""
    known = entities.get(event.entity_id) if entities is not None else None
    # Live state names and places the entity; the event carries only HA's raw attributes.
    attributes = {**event.attributes, **(known.attributes if known is not None else {})}
    head = _head(event.entity_id, attributes)
    old = _clip(event.old_state) if event.old_state is not None else NO_OLD_STATE
    return f"{head}: {old}{_ARROW}{_clip(event.new_state)}"


def render_event(event: StateChangedEvent, entities: Mapping[str, LiveEntity] | None) -> str:
    """One line: name (room): old → new, then who caused it and up to three details from
    the event. ``parse_event`` reads it back."""
    line = render_change(event, entities)
    details = [
        detail
        for key in EVENT_ATTRIBUTES
        if (detail := _event_detail(key, event.attributes.get(key))) is not None
    ][:MAX_EVENT_DETAILS]
    cause = _cause(event, entities)
    return _DETAIL_SEP.join([line, *([cause] if cause else []), *details])


@dataclass(frozen=True)
class RenderedEvent:
    """A What changed line read back: the entity's name as written (its friendly name, or
    its id when it has none), its room, and its states (clipped as written)."""

    name: str
    area: str | None
    old: str | None
    new: str


def parse_event(line: str) -> RenderedEvent | None:
    """``render_event``'s line read back, or None for a line it did not write.

    A name that ends in a parenthesis, on an entity with no room, reads as a name and a
    room: the line cannot tell them apart.
    """
    change = line.split(_DETAIL_SEP, 1)[0]
    before, arrow, new = change.partition(_ARROW)
    head, colon, old = before.rpartition(": ")
    if not (arrow and colon and head and new):
        return None
    m = _HEAD.fullmatch(head)
    return RenderedEvent(
        name=head if m is None else m["name"],
        area=None if m is None else m["area"],
        old=None if old == NO_OLD_STATE else old,
        new=new,
    )


def state_change_line(prompt: str) -> str | None:
    """The What changed line of a ``build_state_change_prompt`` prompt, as written; None for
    any other prompt. The section comes last but one, so the last heading of its name is
    the one."""
    _, heading, section = prompt.rpartition(f"## {STATE_CHANGE_HEADING}\n")
    return section.split("\n", 1)[0] if heading else None


def read_state_change(prompt: str) -> RenderedEvent | None:
    """The state change a ``build_state_change_prompt`` prompt is about; None for any other
    prompt, or for a line ``parse_event`` cannot read."""
    line = state_change_line(prompt)
    return None if line is None else parse_event(line)


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
        line = f"- {tool.name}({', '.join(tool.parameter_names)}) [{tool.target_service}]"
        lines.append(f"{line}: {tool.description}" if tool.description else line)
    return "\n".join(lines)


def parse_tools(prompt: str) -> list[ToolInfo]:
    """The tools a Reflex prompt showed, read back from the lines ``render_tools`` wrote.

    Each comes back with its name, parameter names, target service and description, and as
    a Reflex tool: the prompt does not say anything else about it. That is all
    ``parse_decision`` needs to check a reply, so a recorded prompt can be decided again
    without asking the registry what it offered then. A prompt that showed no tools, or is
    not a Reflex prompt, gives an empty list. A description that runs over several lines
    reads back only as far as its first.

    The section sits right before ``## Preferences``, so the last ``Tools:`` heading ahead
    of that one is the section's, whatever the rules before it say.
    """
    head = prompt.partition(_PREFERENCES_HEADING)[0]
    _, found, section = f"\n\n{head}".rpartition(_TOOLS_HEADING)
    if not found:
        return []
    tools: list[ToolInfo] = []
    for line in section.splitlines():
        m = _TOOL_LINE.fullmatch(line)
        if m is None:
            continue
        names = m["parameters"].split(", ") if m["parameters"] else []
        tools.append(
            ToolInfo(
                name=m["name"],
                description=m["description"] or "",
                feature_name="",
                feature_description="",
                target_service=m["service"],
                audience=REFLEX_AUDIENCE,
                input_schema={
                    "type": "object",
                    "properties": {name: {} for name in names},
                    "required": [],
                },
            )
        )
    return tools


def _assemble(
    prose: ReflexProse,
    *,
    preferences: str,
    tools: Sequence[ToolInfo],
    entities: Mapping[str, LiveEntity] | None,
    now: datetime,
    tz_name: str,
    change_heading: str,
    change: str,
    leave_alone: Sequence[str],
) -> str:
    # Stable → volatile. Keep this order: it is what lets vLLM reuse the prefix.
    return "\n\n".join(
        [
            f"{prose.intro}\n\n{prose.rules}\n\n{render_tools(tools)}",
            f"## Preferences\n{preferences.strip() or NO_PREFERENCES}",
            f"## Now\n{render_now(now, tz_name, entities, leave_alone)}",
            f"## House\n{render_house(entities, tools)}",
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
    prose: ReflexProse,
    leave_alone: Sequence[str] = (),
) -> str:
    """The Reflex prompt for one state change.

    *leave_alone* is the entity IDs a person just changed (see ``render_now``).
    """
    return _assemble(
        prose,
        preferences=preferences,
        tools=tools,
        entities=entities,
        now=now,
        tz_name=tz_name,
        change_heading=STATE_CHANGE_HEADING,
        change=render_event(event, entities),
        leave_alone=leave_alone,
    )


def build_trigger_prompt(
    *,
    event: TriggerFired,
    preferences: str,
    tools: Sequence[ToolInfo],
    entities: Mapping[str, LiveEntity] | None,
    now: datetime,
    tz_name: str,
    prose: ReflexProse,
    leave_alone: Sequence[str] = (),
) -> str:
    """The Reflex prompt for a trigger that fired; the owner is already being notified."""
    return _assemble(
        prose,
        preferences=preferences,
        tools=tools,
        entities=entities,
        now=now,
        tz_name=tz_name,
        change_heading="Trigger fired",
        change=render_trigger(event),
        leave_alone=leave_alone,
    )
