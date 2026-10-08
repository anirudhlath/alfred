"""Reflex prompt assembly — order, cacheable prefix, tools, size (#285)."""

from __future__ import annotations

from datetime import UTC, datetime

from bus.schemas.events import StateChangedEvent, TriggerFired
from core.reflex.prompt import (
    LiveEntity,
    build_state_change_prompt,
    build_trigger_prompt,
    render_tools,
)
from core.reflex.tool_registry import ToolInfo

NIGHT_UTC = datetime(2026, 10, 8, 3, 30, tzinfo=UTC)


def _tool(service: str, *params: str, description: str = "") -> ToolInfo:
    return ToolInfo(
        name="home." + service.replace(".", "_"),
        description=service,
        parameters={p: {"type": "str", "description": description} for p in params},
        feature_name="home",
        feature_description="",
        target_service="home-service",
        audience="reflex",
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


def _lamp(state: str) -> LiveEntity:
    return LiveEntity(
        "light.tv_lamp", "light", True, state, {"friendly_name": "TV Lamp", "area": "Living Room"}
    )


def _event(old: str, new: str) -> StateChangedEvent:
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="light.tv_lamp",
        old_state=old,
        new_state=new,
    )


def _prompt(
    event: StateChangedEvent | None = None,
    entities: dict[str, LiveEntity] | None = None,
    preferences: str = "- Prefers dim light for films",
    tools: list[ToolInfo] = TOOLS,
) -> str:
    return build_state_change_prompt(
        event=event or _event("off", "on"),
        preferences=preferences,
        tools=tools,
        entities=entities if entities is not None else {"light.tv_lamp": _lamp("on")},
        now=NIGHT_UTC,
        tz_name="UTC",
    )


def test_sections_run_from_stable_to_volatile() -> None:
    prompt = _prompt()
    marks = [
        "Tools:",
        "## Preferences",
        "## Now",
        "## House",
        "## What changed",
        "## Decision (JSON only):",
    ]

    positions = [prompt.index(mark) for mark in marks]

    assert positions == sorted(positions)
    assert prompt.endswith("## Decision (JSON only):")


def test_the_cacheable_prefix_is_identical_across_events() -> None:
    first = _prompt(_event("off", "on"), {"light.tv_lamp": _lamp("on")})
    second = _prompt(_event("on", "off"), {"light.tv_lamp": _lamp("off")})

    assert first[: first.index("## Now")] == second[: second.index("## Now")]
    assert first != second


def test_tools_are_compact_sorted_and_free_of_entity_lists() -> None:
    assert render_tools(TOOLS) == (
        "Tools:\n"
        "- home.light_turn_on(target, brightness_pct) [home-service]: light.turn_on\n"
        "- home.switch_turn_off(target) [home-service]: switch.turn_off"
    )
    assert "Available light entities" not in _prompt()


def test_no_tools_says_so() -> None:
    assert render_tools([]) == "No tools available."


def test_blank_preferences_say_none_recorded() -> None:
    assert "## Preferences\nNone recorded yet." in _prompt(preferences="  \n")


def test_the_rules_spell_out_the_decision_format() -> None:
    prompt = _prompt()

    assert '{"decision": "none"}' in prompt
    assert '"decision": "act" | "ask"' in prompt
    for label in ("- act:", "- ask:", "- none:"):
        assert label in prompt


def test_the_reply_format_does_not_ask_the_model_for_a_service() -> None:
    assert '"target_service"' not in _prompt()


def test_a_state_change_prompt_says_never_undo_or_repeat_the_change() -> None:
    assert "Never undo or repeat the change itself" in _prompt()


def test_a_trigger_prompt_names_the_trigger_and_says_the_owner_knows() -> None:
    prompt = build_trigger_prompt(
        event=TriggerFired(trigger_id="t-1", trigger_name="bedtime", trigger_type="time"),
        preferences="",
        tools=TOOLS,
        entities={"light.tv_lamp": _lamp("on")},
        now=NIGHT_UTC,
        tz_name="UTC",
    )

    assert "already being notified" in prompt
    assert "## Trigger fired\nbedtime (time)" in prompt
    assert "## House\nLiving Room: TV Lamp on" in prompt
    assert "## What changed" not in prompt


def _production_tools() -> list[ToolInfo]:
    """The nine Reflex tools production registered on 2026-10-07."""
    return [
        _tool("light.turn_on", "target", "brightness_pct"),
        _tool("light.turn_off", "target"),
        _tool("media_player.turn_on", "target"),
        _tool("media_player.turn_off", "target"),
        _tool("media_player.media_play", "target"),
        _tool("media_player.media_pause", "target"),
        _tool("media_player.volume_set", "target", "volume_level"),
        _tool("switch.turn_on", "target"),
        _tool("switch.turn_off", "target"),
    ]


def _production_shaped() -> dict[str, LiveEntity]:
    """79 actionable entities over five rooms and none, as measured on 2026-10-07."""
    rooms = ["Bedroom", "Entrance", "Kitchen", "Living Room", "Office", None]
    counts = {"light": 34, "media_player": 22, "switch": 21, "climate": 1, "vacuum": 1}
    entities: dict[str, LiveEntity] = {}
    n = 0
    for domain, count in counts.items():
        for i in range(count):
            room = rooms[n % len(rooms)]
            n += 1
            label = domain.replace("_", " ").title()
            attributes: dict[str, object] = {"friendly_name": f"{room or 'Spare'} {label} {i + 1}"}
            if room:
                attributes["area"] = room
            entity_id = f"{domain}.{domain}_{i + 1}"
            entities[entity_id] = LiveEntity(entity_id, domain, True, "off", attributes)
    for p in ("a", "b"):
        entities[f"person.{p}"] = LiveEntity(
            f"person.{p}", "person", True, "home", {"friendly_name": f"Person {p.upper()}"}
        )
    entities["sun.sun"] = LiveEntity("sun.sun", "sun", False, "below_horizon", {})
    return entities


def test_a_production_shaped_house_keeps_the_prompt_small() -> None:
    prompt = _prompt(entities=_production_shaped(), tools=_production_tools())

    assert prompt.count(" · ") >= 70  # every actionable entity made it in
    # At ~4 characters a token, 4,500 characters is about 1,100 tokens (was ~14,400).
    assert len(prompt) < 4_500
