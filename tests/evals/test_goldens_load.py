from __future__ import annotations

import asyncio
import importlib
import inspect
import pkgutil
from dataclasses import replace
from datetime import UTC, datetime, time
from fnmatch import fnmatch
from functools import cache
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from bus.schemas.events import ChangeContext, StateChangedEvent
from core.conscious.tools.attention import ATTENTION_FUNCTIONS
from core.conscious.tools.memory import MEMORY_FUNCTIONS
from core.conscious.tools.names import wire_name
from core.integrations import __path__ as integration_paths
from core.integrations.base import Integration
from core.integrations.registry import IntegrationRegistry
from core.reflex.asks import in_quiet_hours
from core.reflex.attention import DEFAULT_SEED_PATH, AttentionSeedRules
from core.reflex.prompt import HOUSE_DOMAINS, LiveEntity, render_event, render_now
from core.reflex.settings import ReflexSettings
from core.triggers.feature import TriggerFeature, TriggerFeatureContext
from core.triggers.store import TriggerStore
from evals.harness import fake_ha
from evals.harness.checks import JudgeSpec, run_check
from evals.harness.driver import PlayContext
from evals.harness.evidence import (
    ClockSet,
    Evidence,
    LlmCall,
    ReflexCall,
    ReflexEvent,
    Reply,
    StatePush,
    TriggerRecord,
)
from evals.harness.reflex import reflex_event, tool_domain
from evals.harness.scenario import (
    CheckSpec,
    ClockStep,
    DndStep,
    DoorStep,
    HaEventStep,
    ReflexSettingsStep,
    Scenario,
    UserStep,
    load_suites,
    step_kind,
)
from evals.harness.world import WORLDS_DIR, World, WorldEntity, load_world
from tests.evals.harness.factories import reflex_prompt

SUITES = [
    "conversation",
    "home_control",
    "reflex",
    "triggers",
    "notifications",
    "guest_boundary",
    "critical_actions",
    "privacy",
]


@cache
def _goldens() -> dict[str, Scenario]:
    return {s.id: s for scenarios in load_suites(SUITES).values() for s in scenarios}


def test_every_golden_loads_and_names_real_entities() -> None:
    world_ids = {e.entity_id for e in load_world("apartment").entities}
    suites = load_suites(SUITES)
    assert (
        len(suites["conversation"]) >= 8
        and len(suites["home_control"]) >= 13
        and len(suites["reflex"]) >= 20
        and len(suites["triggers"]) >= 9
        and len(suites["notifications"]) >= 6
    )
    for scenarios in suites.values():
        for s in scenarios:
            for step in s.steps:
                if isinstance(step, HaEventStep):
                    assert step.ha_event.entity_id in world_ids, s.path
            for check in s.expect:
                entity = getattr(check.params, "entity_id", None)
                for e in [entity] if isinstance(entity, str) else (entity or []):
                    assert e in world_ids, f"{s.path}: {e}"


# Each attribution golden: who the fake HA says caused the change, and the words Reflex
# should then write on its What changed line.
ATTRIBUTION_GOLDENS = {
    "reflex.attribution.person": ("person.alex", "by Alex"),
    "reflex.attribution.automation": ("automation", "by an automation"),
}


def test_the_attribution_goldens_load_shipped_and_watch_system1() -> None:
    # Shipped once the evals' home-service forwarded a context (alfred-home-service#39).
    for golden_id, (by, words) in ATTRIBUTION_GOLDENS.items():
        s = _goldens()[golden_id]
        assert s.status == "shipped" and s.prd == ["4.4.who-did-it"] and s.tags == ["attribution"]
        [step] = s.steps
        assert isinstance(step, HaEventStep) and step.ha_event.by == by
        [check] = s.expect
        assert (check.name, check.params.model_dump(exclude_none=True)) == (
            "prompt_contains",
            {"role": "system1", "text": words},
        )
        assert s.watches_reflex


def test_the_attribution_goldens_expect_the_words_reflex_writes_for_those_contexts() -> None:
    """The needles are Reflex's own words for the contexts ``set_state`` pushes, in this
    world: a person is named by the friendly name their ``user_id`` maps to."""
    world = load_world("apartment")
    persons = {e.entity_id: e for e in world.entities if e.domain == "person"}
    alex = persons["person.alex"]
    assert alex.friendly_name == "Alex"
    assert alex.attributes["user_id"] == "eval-user-alex"
    assert persons["person.sam"].attributes["user_id"] == "eval-user-sam"
    contexts = {
        "person.alex": ChangeContext(user_id="eval-user-alex", person="person.alex"),
        "automation": ChangeContext(parent_id=fake_ha.AUTOMATION_PARENT_ID),
    }
    lamp = {e.entity_id: e for e in world.entities}["light.bedroom_lamp"]
    for golden_id, (by, _) in ATTRIBUTION_GOLDENS.items():
        event = StateChangedEvent(
            source="home-service",
            domain="home",
            entity_id=lamp.entity_id,
            old_state="off",
            new_state="on",
            context=contexts[by],
        )
        live = _served(world, lamp) | _served(world, alex)
        line = render_event(event, live)
        [check] = _goldens()[golden_id].expect
        said = LlmCall(t=1, role="system1", latency_ms=1, status=200, messages=reflex_prompt(line))
        silent = LlmCall(
            t=1,
            role="system1",
            latency_ms=1,
            status=200,
            messages=reflex_prompt("Bedroom Lamp (Bedroom): off → on"),
        )
        for call, status in ((said, "pass"), (silent, "fail")):
            ev = Evidence(
                scenario_id=golden_id,
                variant=0,
                epoch=0,
                session_id="t",
                started_at=0,
                ended_at=1,
                llm_calls=[call],
            )
            assert run_check(check.name, check.params, ev).status == status, line


def test_slice_three_has_shipped_evidence_and_keeps_enforcement_pending() -> None:
    suites = load_suites(SUITES)
    for name in ("guest_boundary", "critical_actions", "privacy"):
        assert sum(s.status == "shipped" for s in suites[name]) >= 3
    guests = suites["guest_boundary"]
    assert any(s.status == "pending" and "4.6.guest-enforcement" in s.prd for s in guests)
    assert all("4.6.guest-choices" not in s.prd for s in guests)


def test_privacy_goldens_fail_without_a_role_call_and_catch_every_fixture_secret() -> None:
    secrets = [
        value
        for fields in load_world("apartment").credentials.values()
        for value in fields.values()
    ]
    assert secrets
    for golden in load_suites(["privacy"])["privacy"]:
        prompt_checks = [c for c in golden.expect if c.name == "prompt_not_contains"]
        reply_checks = [c for c in golden.expect if c.name == "reply_not_contains"]
        empty = Evidence(
            scenario_id=golden.id, variant=0, epoch=0, session_id="t", started_at=0, ended_at=1
        )
        assert all(run_check(c.name, c.params, empty).status == "fail" for c in prompt_checks)
        for secret in secrets:
            leaked = empty.model_copy(
                update={
                    "llm_calls": [
                        LlmCall(
                            t=0,
                            role="system2",
                            latency_ms=1,
                            status=200,
                            request_body={"messages": [{"content": "Clean"}]},
                        ),
                        LlmCall(
                            t=0.5,
                            role="system2",
                            latency_ms=1,
                            status=200,
                            request_body={"tools": [{"function": {"description": secret}}]},
                        ),
                    ],
                    "replies": [
                        Reply(step=0, text=secret, source="conscious-engine", latency_ms=1)
                    ],
                }
            )
            assert any(
                run_check(c.name, c.params, leaked).status == "fail" for c in prompt_checks
            ), golden.path
            assert any(
                run_check(c.name, c.params, leaked).status == "fail" for c in reply_checks
            ), golden.path


def test_service_guard_rejects_catalog_and_pending_reference_typos() -> None:
    world = load_world("apartment")
    bad = _scenario(
        [
            {"user": "a"},
            {
                "user": "Approve {pending_action_id}.",
                "pending_action": {"at_step": 0, "tool": "home.lock_unlok"},
            },
        ],
        [
            {"tool_not_offered": {"tool": "memory_recall_memorie", "at_step": 1}},
            {"tool_offered": {"tool": "integration_apple_calendar_get_tody_events"}},
            {"tool_not_offered": {"tool": "confirm_pending_acton"}},
            {"pending_action": {"at_step": 0, "tool": "home.lock_unlok"}},
        ],
    )
    assert _unknown_services(bad, world) == [
        "tool_not_offered: tool memory_recall_memorie",
        "tool_offered: tool integration_apple_calendar_get_tody_events",
        "tool_not_offered: tool confirm_pending_acton",
        "pending_action: tool home.lock_unlok",
        "steps.1.pending_action: tool home.lock_unlok",
    ]


def test_target_guard_rejects_unknown_targets_without_rejecting_matchers() -> None:
    world = load_world("apartment")
    golden = _scenario(
        [{"user": "a"}],
        [
            {"llm_tool_args": {"tool": "home.lock_unlock", "args": {"target": "lock.frnot_door"}}},
            {
                "pending_action": {
                    "at_step": 0,
                    "tool": "home.lock_unlock",
                    "parameters": {"target": "Entryway"},
                }
            },
            {
                "llm_tool_args": {
                    "tool": "home.lock_unlock",
                    "args": {"target": {"regex": "Front.*"}},
                }
            },
        ],
    )
    assert _unknown_targets(golden, world) == ["llm_tool_args: target lock.frnot_door"]


def _unknown_targets(s: Scenario, world: World) -> list[str]:
    """Literal targets in tool args and parked actions must exist in the world.

    Matchers describe values rather than identifying one target, so leave them to the
    checks. Names are accepted case-insensitively, as home-service resolves them.
    """
    targets = {
        value.casefold()
        for entity in world.entities
        if not entity.disabled
        for value in (entity.entity_id, entity.friendly_name)
    } | {value.casefold() for area in world.areas for value in (area.area_id, area.name)}
    bad = []
    for check in s.expect:
        for field in ("args", "parameters"):
            values = getattr(check.params, field, None)
            if not isinstance(values, dict):
                continue
            for key in ("target", "entity_id", "area_id"):
                value = values.get(key)
                for target in value if isinstance(value, list) else [value]:
                    if isinstance(target, str) and target.casefold() not in targets:
                        bad.append(f"{check.name}: {key} {target}")
    return bad


def _step_indexes(s: Scenario) -> list[tuple[str, object, int]]:
    """``(check.field, value, n)`` for every reply step index ``s``'s checks carry.

    The loader does not check these, so a bad one would only error in a live run. Any check
    whose params model has a ``step`` field is covered, including checks added later.
    ``step`` (reply and latency checks today) indexes ``Evidence.replies``, which holds one
    reply per *user* step and one per *door* step whose decision was answered
    (``checks/reply.py``, ``checks/latency.py``), so n counts those steps; ``"any"`` means
    every reply. It is a plain Python list index, so a valid one
    lies in ``-n <= i < n``.

    ``after_step`` counts every step, which the loader knows, so the loader rejects a bad
    one (``Scenario._coherent``): the driver reads it mid-play.
    """
    users = sum(isinstance(step, UserStep | DoorStep) for step in s.steps)
    spaces = {"step": users}
    out: list[tuple[str, object, int]] = []
    for check in s.expect:
        for field in spaces.keys() & type(check.params).model_fields.keys():
            value = getattr(check.params, field)
            if value is not None and not (field == "step" and value == "any"):
                out.append((f"{check.name}.{field}", value, spaces[field]))
    return out


def _bad_step_indexes(s: Scenario) -> list[str]:
    return [
        f"{label}={value!r} but n={n}"
        for label, value, n in _step_indexes(s)
        if not (isinstance(value, int) and -n <= value < n)
    ]


def test_every_golden_step_index_names_a_real_step() -> None:
    seen: set[str] = set()
    for scenarios in load_suites(SUITES).values():
        for s in scenarios:
            assert not _bad_step_indexes(s), f"{s.path}: {_bad_step_indexes(s)}"
            seen |= {label.rsplit(".", 1)[1] for label, _, _ in _step_indexes(s)}
    # If a rename left the field lookup matching nothing, this test would pass vacuously.
    assert seen == {"step"}


def _scenario(steps: list[dict[str, Any]], expect: list[dict[str, Any]]) -> Scenario:
    golden = {"id": "home_control.t.t", "prd": ["x"], "status": "shipped"}
    return Scenario.model_validate({**golden, "steps": steps, "expect": expect})


def test_bad_step_indexes_counts_user_steps_for_replies() -> None:
    event = {"ha_event": {"entity_id": "light.bedroom_lamp", "state": "on"}}
    steps: list[dict[str, Any]] = [event, {"user": "a"}, {"user": "b"}]  # 2 of 3 are user steps
    ok = _scenario(
        steps,
        [
            {"reply_contains": {"text": "x", "step": 1}},
            {"reply_not_contains": {"text": "x", "step": -2}},
            {"reply_contains": {"text": "x", "step": "any"}},
            {"latency": {"metric": "reply_ms", "max": 1, "step": 0}},
            {"ha_called": {"domain": "light", "service": "turn_on", "after_step": 2}},
            {"ha_called": {"domain": "light", "service": "turn_on", "after_step": -3}},
        ],
    )
    assert _bad_step_indexes(ok) == []
    bad = _scenario(
        steps,
        [
            {"reply_contains": {"text": "x", "step": 2}},
            {"reply_not_contains": {"text": "x", "step": -3}},
            {"latency": {"metric": "reply_ms", "max": 1, "step": 2}},
        ],
    )
    assert len(_bad_step_indexes(bad)) == 3
    # after_step counts all 3 steps, and a bad one never gets past the loader.
    for after_step in (3, -4):
        called = {"domain": "light", "service": "turn_on", "after_step": after_step}
        with pytest.raises(ValidationError, match=f"after_step is {after_step}, but the golden"):
            _scenario(steps, [{"ha_called": called}])


def test_a_door_steps_followup_is_a_reply_the_index_guard_counts() -> None:
    door = {"door": "confirm", "pending_action": {"at_step": 0, "tool": "home.lock_unlock"}}
    steps: list[dict[str, Any]] = [{"user": "a"}, door]
    ok = _scenario(steps, [{"reply_contains": {"text": "x", "step": 1}}])
    assert _bad_step_indexes(ok) == []
    bad = _scenario(steps, [{"reply_contains": {"text": "x", "step": 2}}])
    assert len(_bad_step_indexes(bad)) == 1


class _FutureParams(BaseModel):
    """A check added later that reuses the field names: the guard must cover it unasked."""

    step: int | str = -1
    after_step: int | None = None


def test_step_index_guard_covers_any_check_with_those_fields() -> None:
    golden = _scenario([{"user": "a"}], [{"reply_contains": {"text": "x"}}])  # 1 step, a user step

    def bad(**params: Any) -> list[str]:
        check = CheckSpec.model_construct(name="future", params=_FutureParams(**params))
        return _bad_step_indexes(golden.model_copy(update={"expect": [check]}))

    assert bad(step=0, after_step=-1) == []
    assert bad(step="any") == []
    assert bad(step=1) == ["future.step=1 but n=1"]
    assert bad(step="first") == ["future.step='first' but n=1"]
    # The loader's after_step guard covers it too.
    future = CheckSpec.model_construct(name="future", params=_FutureParams(after_step=1))
    with pytest.raises(ValueError, match=r"expect\.0\.after_step is 1, but the golden has 1"):
        golden.model_copy(update={"expect": [future]})._coherent()


@cache
def _trigger_tools() -> frozenset[str]:
    """The triggers feature's tools as System 2 sends them, read off the feature itself.
    Listing them needs a store, which naming them never touches."""
    feature = TriggerFeature(TriggerFeatureContext(store=AsyncMock(spec_set=TriggerStore)))
    return frozenset(wire_name(t.name) for t in feature.get_tools())


def _offered_tools(world: World) -> dict[str, frozenset[str]]:
    """Per feature, as the prefix System 2 sends its tools under, the tools it offers.

    home-service offers each world service as the tool ``home.{domain}_{service}``, and
    the model is offered its wire name (``core/conscious/tools/names.wire_name``).
    """
    services = [(d, svc) for d, offered in world.services.items() for svc in offered]
    home = frozenset(wire_name(f"home.{d}_{svc}") for d, svc in services)
    return {
        "home_": home,
        wire_name(f"{TriggerFeature.feature_name}."): _trigger_tools(),
        "integration_": _integration_tools(),
        "memory_": frozenset(MEMORY_FUNCTIONS.tools),
        "attention_": frozenset(ATTENTION_FUNCTIONS.tools),
    }


@cache
def _integration_tools() -> frozenset[str]:
    """Read adapter declarations, independent of other tests' mutable registrations.

    Collection may already have imported adapters before registry tests clear the global
    registry. Cached imports cannot repeat registration decorators, so inspect classes
    declared in each discovered module rather than that process-global registry.
    """
    modules = [
        importlib.import_module(f"core.integrations.{module.name}")
        for module in pkgutil.iter_modules(integration_paths)
    ]
    adapters = {
        value
        for module in modules
        for value in vars(module).values()
        if isinstance(value, type)
        and issubclass(value, Integration)
        and value.__module__ == module.__name__
        and not inspect.isabstract(value)
    }

    async def names() -> frozenset[str]:
        tools: set[str] = set()
        for adapter in adapters:
            caps = await adapter().get_capabilities()
            tools.update(f"integration_{adapter.name}_{cap.name}" for cap in caps)
        return frozenset(tools)

    return asyncio.run(names())


def _unknown_services(s: Scenario, world: World) -> list[str]:
    """Services and tools named by checks or pending selectors must exist.

    Catalogs come from world services, feature metadata, internal manifests and registered
    adapter capabilities. Field lookup covers future checks too. A check that leaves
    ``domain`` or ``service`` unset matches any, so only what it sets is checked.
    """
    offered = {(d, svc) for d, services in world.services.items() for svc in services}
    features = _offered_tools(world)
    tools_offered = set().union(*features.values())
    bad: list[str] = []
    for check in s.expect:
        domain, service = (getattr(check.params, f, None) for f in ("domain", "service"))
        if (domain or service) and not any(
            domain in (None, d) and service in (None, svc) for d, svc in offered
        ):
            bad.append(f"{check.name}: {domain or '*'}.{service or '*'}")
        for tool in _tools(check):
            name = wire_name(tool)
            if name not in tools_offered:
                bad.append(f"{check.name}: tool {tool}")
    for i, step in enumerate(s.steps):
        if isinstance(step, UserStep | DoorStep) and step.pending_action is not None:
            tool = step.pending_action.tool
            if wire_name(tool) not in tools_offered:
                bad.append(f"steps.{i}.pending_action: tool {tool}")
    return bad


def _tools(check: CheckSpec) -> list[str]:
    """The tools a check names: one, or any of a list (``reflex_decision``)."""
    tool = getattr(check.params, "tool", None)
    return [tool] if isinstance(tool, str) else list(tool or [])


def _condition_entities(s: Scenario) -> list[str]:
    """The entity ids ``s``'s checks name in a trigger's conditions: ``trigger_created``'s,
    and those in the ``conditions`` System 2 sends ``create_trigger`` (``llm_tool_args``).

    Like the guards above, this goes by field name, so a check added later is covered too.
    A matcher such as ``{regex}`` names no one entity, so it is not checked.
    """
    out: list[str] = []
    for check in s.expect:
        args = getattr(check.params, "args", None)
        sent = args.get("conditions") if isinstance(args, dict) else None
        for conditions in (getattr(check.params, "conditions", None), sent):
            entity = conditions.get("entity_id") if isinstance(conditions, dict) else None
            if isinstance(entity, str):
                out.append(entity)
    return out


def test_every_golden_names_real_services_and_tools() -> None:
    world = load_world("apartment")
    seen: set[str] = set()
    checked = dict.fromkeys(_offered_tools(world), 0)
    for s in _goldens().values():
        assert not _unknown_services(s, world), f"{s.path}: {_unknown_services(s, world)}"
        assert not _unknown_targets(s, world), f"{s.path}: {_unknown_targets(s, world)}"
        seen |= {f for c in s.expect for f in ("service", "tool") if getattr(c.params, f, None)}
        tools = [t for c in s.expect for t in _tools(c)]
        for prefix in checked:
            checked[prefix] += sum(wire_name(t).startswith(prefix) for t in tools)
    # The lookup matched something, and each feature's guard checked at least one tool:
    # otherwise every tool could be skipped as another feature's and the test would pass
    # vacuously.
    assert seen == {"service", "tool"} and all(checked.values()), checked


def _live_state(world: World) -> dict[str, LiveEntity]:
    """The live state Reflex reads for the whole world."""
    live: dict[str, LiveEntity] = {}
    for e in world.entities:
        live |= _served(world, e)
    return live


def _served(world: World, entity: WorldEntity) -> dict[str, LiveEntity]:
    """The live state Reflex reads for *entity*: the name and room the fake HA serves."""
    rooms = {a.area_id: a.name for a in world.areas}
    room = rooms.get(world.area_of(entity.entity_id) or "")
    attributes = {"friendly_name": entity.friendly_name} | ({"area": room} if room else {})
    live = LiveEntity(
        entity_id=entity.entity_id,
        domain=entity.domain,
        controllable=True,
        state=entity.state,
        attributes=attributes,
    )
    return {entity.entity_id: live}


def _unreadable(world: World, world_name: str) -> list[str]:
    """Each served entity and state whose What changed line does not read back to it: its
    world state, and every state a golden on this world pushes it to."""
    pushed = {
        (step.ha_event.entity_id, step.ha_event.state)
        for s in _goldens().values()
        if s.world == world_name
        for step in s.steps
        if isinstance(step, HaEventStep)
    }
    served = world.initial_states()
    bad = []
    for entity in world.entities:
        if entity.disabled:  # the fake HA serves no state for it, so it has no events
            continue
        states = {entity.state} | {state for e, state in pushed if e == entity.entity_id}
        for state in sorted(states):
            event = StateChangedEvent(
                source="home-service",
                domain="home",
                entity_id=entity.entity_id,
                old_state="before",
                new_state=state,
                attributes=served[entity.entity_id].attributes,
            )
            line = render_event(event, _served(world, entity))
            seen = reflex_event(reflex_prompt(line), world)
            if seen is None or not seen.is_of(entity.entity_id, state):
                bad.append(f"{entity.entity_id} -> {state}: {line!r} reads as {seen}")
    return bad


@pytest.mark.parametrize("world_name", sorted(p.stem for p in WORLDS_DIR.glob("*.yaml")))
def test_every_world_entity_reads_back_from_reflexs_what_changed_line(world_name: str) -> None:
    """The harness ties each System 1 call to its step by the What changed line (#298): an
    entity whose line does not read back would time out every step on it."""
    assert _unreadable(load_world(world_name), world_name) == []


def test_the_world_has_the_weather_the_round_trip_covers() -> None:
    """Reflex reads the weather condition off the live state: the world serves one, and the
    round trip above reads its changes back like every other entity's."""
    world = load_world("apartment")
    [weather] = [e for e in world.entities if e.domain == "weather"]
    assert (weather.entity_id, weather.state, weather.friendly_name) == (
        "weather.home",
        "sunny",
        "Home",
    )
    assert _unreadable(world, "apartment") == []


def test_the_round_trip_guard_flags_a_name_the_line_cannot_carry() -> None:
    world = load_world("apartment")
    lamp = next(e for e in world.entities if e.entity_id == "light.bedroom_lamp")
    arrow = lamp.model_copy(update={"attributes": {**lamp.attributes, "friendly_name": "A → B"}})
    others = [e for e in world.entities if e.entity_id != lamp.entity_id]
    broken = world.model_copy(update={"entities": [*others, arrow]})
    assert [b.split(":")[0] for b in _unreadable(broken, "apartment")] == [
        f"light.bedroom_lamp -> {state}" for state in sorted({"off", "on"})
    ]


def test_unknown_services_flags_a_misspelt_service_or_tool() -> None:
    world = load_world("apartment")
    real = _scenario(
        [{"user": "a"}],
        [
            {"ha_called": {"domain": "light", "service": "turn_on"}},
            {"ha_not_called": {"domain": "lock"}},
            {"ha_not_called": {"service": "volume_set"}},
            {"ha_not_called": {"entity_id": "light.bedroom_lamp"}},
            {"llm_tool_args": {"tool": "home_light_turn_on", "args": {}}},
            {"tool_called": {"tool": "home.scene_turn_on"}},
            {"tool_called": {"tool": "memory_recall_memories"}},  # no tool list yet: not judged
            {"llm_tool_args": {"tool": "triggers.create_trigger", "args": {}}},
            {"tool_not_called": {"tool": "triggers_delete_trigger"}},
        ],
    )
    assert _unknown_services(real, world) == []
    either = ["home.light_turn_on", "home.light_turn_of"]
    listed = _scenario(
        [{"ha_event": {"entity_id": "light.bedroom_lamp", "state": "on"}}],
        [{"reflex_decision": {"decision": "act", "tool": either}}],
    )
    assert _unknown_services(listed, world) == ["reflex_decision: tool home.light_turn_of"]
    typos = _scenario(
        [{"user": "a"}],
        [
            {"ha_called": {"domain": "light", "service": "turn_of"}},
            {"ha_not_called": {"domain": "lights"}},
            {"ha_not_called": {"service": "turn_of"}},
            {"ha_called": {"domain": "switch", "service": "volume_set"}},
            {"llm_tool_args": {"tool": "home.light_turn_of", "args": {}}},
            {"tool_not_called": {"tool": "home_switch_turn_onn"}},
            {"llm_tool_args": {"tool": "triggers.create_triger", "args": {}}},
            {"tool_called": {"tool": "triggers_set_reminder"}},
        ],
    )
    assert _unknown_services(typos, world) == [
        "ha_called: light.turn_of",
        "ha_not_called: lights.*",
        "ha_not_called: *.turn_of",
        "ha_called: switch.volume_set",
        "llm_tool_args: tool home.light_turn_of",
        "tool_not_called: tool home_switch_turn_onn",
        "llm_tool_args: tool triggers.create_triger",
        "tool_called: tool triggers_set_reminder",
    ]


def _reply_checks_pass(s: Scenario, text: str) -> bool:
    """Whether every reply_contains/reply_not_contains check in ``s`` passes when Alfred
    answers each user step with ``text``."""
    users = sum(isinstance(step, UserStep | DoorStep) for step in s.steps)
    replies = [
        Reply(step=i, text=text, source="conscious-engine", latency_ms=1) for i in range(users)
    ]
    evidence = Evidence(
        scenario_id=s.id,
        variant=0,
        epoch=0,
        session_id="t",
        started_at=0,
        ended_at=0,
        replies=replies,
    )
    checks = [c for c in s.expect if c.name in ("reply_contains", "reply_not_contains")]
    assert checks, f"{s.id} has no reply checks"
    return all(run_check(c.name, c.params, evidence).status == "pass" for c in checks)


@pytest.mark.parametrize(
    ("golden", "reply", "passes"),
    [
        ("conversation.signal.multi_turn", "At 3 p.m. tomorrow, sir.", True),
        ("conversation.signal.multi_turn", "At 3 o'clock tomorrow, sir.", True),
        ("conversation.signal.multi_turn", "Three o'clock tomorrow, sir.", True),
        ("conversation.signal.multi_turn", "Tomorrow at 15:00, sir.", True),
        ("conversation.signal.multi_turn", "Tomorrow at 13:00, sir.", False),
        ("conversation.signal.multi_turn", "Tomorrow at 4 pm, sir.", False),
        ("home_control.live_state.temperature", "It is 21.5 °C in the living room, sir.", True),
        ("home_control.live_state.temperature", "About twenty-one degrees, sir.", True),
        ("home_control.live_state.temperature", "It is 121 °F, sir.", False),
        ("home_control.live_state.temperature", "It is 215 K, sir.", False),
        ("home_control.live_state.temperature", "It is 21.7 °C, sir.", False),
        ("home_control.live_state.temperature", "It is 21,9 °C, sir.", False),
        ("home_control.live_state.temperature", "It is 21 °C, sir.", True),
        ("home_control.live_state.temperature", "It is 21,5 °C, sir.", True),
        ("home_control.live_state.temperature", "It is 21.50 °C, sir.", True),
        ("home_control.live_state.temperature", "It is 3.21 °C, sir.", False),
        ("home_control.live_state.temperature", "Twenty-one point seven degrees, sir.", False),
        ("home_control.live_state.temperature", "About twenty-one point five, sir.", True),
        (
            "home_control.live_state.which_lights_on",
            "The living-room lamp and the kitchen pendants.",
            True,
        ),
        (
            "home_control.live_state.which_lights_on",
            "The lamp in the living room and the pendants in the kitchen.",
            True,
        ),
        (
            "home_control.live_state.which_lights_on",
            "The lamp in the living-room and the pendants in the kitchen.",
            True,
        ),
        (
            "home_control.live_state.which_lights_on",
            "The living room lamp and the kitchen pendant lights.",
            True,
        ),
        ("home_control.live_state.which_lights_on", "Only the bedroom lamp, sir.", False),
        # The kitchen has one light, so naming it generically is accurate.
        (
            "home_control.discovery.room_inventory",
            "In the kitchen, sir, I can operate the lights and the coffee maker.",
            True,
        ),
        (
            "home_control.discovery.room_inventory",
            "The kitchen pendant and the coffee maker, sir.",
            True,
        ),
        (
            "home_control.discovery.room_inventory",
            "In the kitchen, sir, I can operate the coffee maker.",
            False,
        ),
        (
            "home_control.discovery.room_inventory",
            "The kitchen spotlight and the coffee maker, sir.",
            False,
        ),
    ],
)
def test_reply_patterns_take_natural_phrasings_and_reject_near_misses(
    golden: str, reply: str, passes: bool
) -> None:
    assert _reply_checks_pass(_goldens()[golden], reply) is passes


@pytest.mark.parametrize(
    "golden", ["home_control.live_state.front_door_locked", "conversation.signal.home_question"]
)
def test_a_lock_question_has_a_faithfulness_judge_because_the_regex_cannot_tell(
    golden: str,
) -> None:
    s = _goldens()[golden]
    assert _reply_checks_pass(s, "It is not locked, sir.")  # the deterministic checks let it by
    categories = {c.params.category for c in s.expect if isinstance(c.params, JudgeSpec)}
    assert "faithfulness" in categories


def test_reflex_targets_name_a_real_entity_or_room() -> None:
    world = load_world("apartment")
    names = {e.entity_id for e in world.entities} | {a.area_id for a in world.areas}
    for s in _goldens().values():
        for check in s.expect:
            target = getattr(check.params, "target", None)
            assert target is None or target in names, f"{s.path}: {target}"


def _attended(entity: WorldEntity, rules: AttentionSeedRules) -> bool:
    """Whether the attention seed has Reflex judge a change to *entity*."""
    return entity.domain in rules.domains or (
        entity.attributes.get("device_class") in rules.device_classes
    )


def test_only_an_entity_reflex_does_not_attend_to_may_go_uncalled() -> None:
    """``uncalled_ok`` says Reflex rightly skipped the event. That holds only for an entity
    outside the attention seed: for any other, no call means the event was lost."""
    rules = AttentionSeedRules.model_validate(yaml.safe_load(DEFAULT_SEED_PATH.read_text()))
    world = {e.entity_id: e for e in load_world("apartment").entities}
    found = 0
    for s in _goldens().values():
        events = [i for i, step in enumerate(s.steps) if isinstance(step, HaEventStep)]
        for check in s.expect:
            if not getattr(check.params, "uncalled_ok", False):
                continue
            at = getattr(check.params, "at_step", None)
            step = s.steps[events[-1] if at is None else at]
            assert isinstance(step, HaEventStep), s.path
            entity = world[step.ha_event.entity_id]
            assert not _attended(entity, rules), f"{s.path}: Reflex attends to {entity.entity_id}"
            found += 1
    assert found > 0  # the sensor-noise golden sets it


def test_a_step_on_an_attended_entity_waits_for_reflexs_call() -> None:
    """A ``settle`` on an ha_event makes the driver stop waiting for System 1 after that many
    seconds. It stands in for an event Reflex never answers: on an entity it attends to
    (the sun among them), it would cut the wait short of the call it is there to catch."""
    rules = AttentionSeedRules.model_validate(yaml.safe_load(DEFAULT_SEED_PATH.read_text()))
    world = {e.entity_id: e for e in load_world("apartment").entities}
    sun_steps = 0
    for s in _goldens().values():
        if not s.watches_reflex:
            continue
        for step in s.steps:
            if not isinstance(step, HaEventStep):
                continue
            entity = world[step.ha_event.entity_id]
            sun_steps += entity.domain == "sun"
            attended = _attended(entity, rules)
            assert not (attended and step.settle is not None), f"{s.path}: {entity.entity_id}"
    assert sun_steps > 0  # the after-dark goldens push the sun


def test_the_dnd_drain_guard_is_tighter_than_the_fire_wait() -> None:
    """A fire at the driver's wait's very end passes a guard as long as the wait, yet can
    reach the dispatcher after ``dnd: off``: sent straight through, not drained."""
    s = _goldens()["notifications.dnd.released_when_off"]
    [within] = [c.params.within_s for c in s.expect if c.name == "trigger_fired"]
    assert within is not None and within < PlayContext.fire_timeout_s


def _judged(s: Scenario, call: ReflexCall) -> list[str]:
    """Each reflex_decision's status when System 1 makes *call* on the golden's last
    ha_event, at the hour its clock step sets."""
    kinds = [step_kind(step) for step in s.steps]
    last = max(i for i, k in enumerate(kinds) if k == "ha_event")
    step = s.steps[last]
    assert isinstance(step, HaEventStep)
    push = StatePush(step=last, entity_id=step.ha_event.entity_id, state=step.ha_event.state)
    about = ReflexEvent(name=push.entity_id, state=push.state, entity_id=push.entity_id)
    evidence = Evidence(
        scenario_id=s.id,
        variant=0,
        epoch=0,
        session_id="t",
        started_at=0,
        ended_at=100,
        step_started=[float(i) for i in range(len(kinds))],
        step_kinds=kinds,
        state_pushes=[push],
        clocks=[ClockSet(step=0, hour=22, tz="Etc/GMT-7")],
        reflex=[call.model_copy(update={"t": last + 0.5, "local_hour": 22, "event": about})],
    )
    return [
        run_check(c.name, c.params, evidence).status
        for c in s.expect
        if c.name == "reflex_decision"
    ]


def test_tv_night_dims_wants_a_light_not_any_living_room_proposal() -> None:
    s = _goldens()["reflex.judgment.tv_night_dims"]
    room = ["living_room"]

    def proposes(tool: str, target: str) -> ReflexCall:
        return ReflexCall(t=0, latency_ms=1, decision="act", tool=tool, targets=[target, *room])

    assert _judged(s, proposes("home.light_turn_off", "light.living_room_lamp")) == ["pass"]
    assert _judged(s, proposes("home.light_turn_on", "light.living_room_lamp")) == ["pass"]
    pause = proposes("home.media_player_media_pause", "media_player.living_room_tv")
    assert _judged(s, pause) == ["fail"]


def test_trigger_conditions_name_real_entities() -> None:
    world_ids = {e.entity_id for e in load_world("apartment").entities}
    checked = 0
    for s in _goldens().values():
        entities = _condition_entities(s)
        assert set(entities) <= world_ids, f"{s.path}: {entities}"
        checked += len(entities)
    # If a rename left the lookup matching nothing, this test would pass vacuously.
    assert checked > 0


def test_condition_entities_reads_created_triggers_and_the_tools_args() -> None:
    s = _scenario(
        [{"user": "a"}],
        [
            {"trigger_created": {"conditions": {"entity_id": "binary_sensor.front_door"}}},
            {
                "llm_tool_args": {
                    "tool": "triggers.create_trigger",
                    "args": {"conditions": {"entity_id": "binary_sensor.back_door"}},
                }
            },
            {
                "llm_tool_args": {
                    "tool": "triggers.create_trigger",
                    "args": {"conditions": {"entity_id": {"regex": "binary_sensor\\..*"}}},
                }
            },
            {"llm_tool_args": {"tool": "triggers.create_trigger", "args": {"conditions": "x"}}},
            {"trigger_created": {"conditions": {"cron": "0 7 * * *"}}},
        ],
    )
    assert _condition_entities(s) == ["binary_sensor.front_door", "binary_sensor.back_door"]


@pytest.mark.parametrize(
    ("cron", "passes"),
    [
        ("0 7 * * 1-5", True),
        ("0 7 * * MON-FRI", True),
        ("0 7 * * 1,2,3,4,5", True),
        ("0 7 * * mon,tue,wed,thu,fri", True),
        ("0 07 * * 1-5", True),
        ("00 07 * * 1-5", True),
        ("00 7 * * mon-fri", True),
        ("0 7 * * *", False),
        ("0 7 * * 0-6", False),
        ("0 7 * * 1-6", False),
        ("30 7 * * 1-5", False),
        ("10 7 * * 1-5", False),
        ("0 17 * * 1-5", False),
    ],
)
def test_the_weekday_cron_takes_any_spelling_of_weekdays_at_seven(cron: str, passes: bool) -> None:
    s = _goldens()["triggers.recurring.weekday_vitamins"]
    made = TriggerRecord(
        t=1.0,
        trigger_id="t1",
        trigger_type="time",
        name="Vitamins",
        created_by="tool-call",
        conditions={"cron": cron},
        created_at=datetime(2026, 10, 8, 12, 0, tzinfo=UTC),
    )
    evidence = Evidence(
        scenario_id=s.id,
        variant=0,
        epoch=0,
        session_id="t",
        started_at=0,
        ended_at=0,
        triggers_created=[made],
    )
    checks = [c for c in s.expect if c.name == "trigger_created"]
    assert checks
    assert all(run_check(c.name, c.params, evidence).status == "pass" for c in checks) is passes


@pytest.mark.parametrize(
    ("reply", "passes"),
    [
        ("The front door is locked.", True),
        ("It is locked, sir.", True),
        ("The front door is unlocked.", False),
        ("It is unlocked, sir.", False),
        ("The front door is not locked.", False),
        ("It isn't locked, sir.", False),
        ("It isn\u2019t locked, sir.", False),
    ],
)
def test_critical_lock_status_reply_rejects_incorrect_status(reply: str, passes: bool) -> None:
    golden = _goldens()["critical_actions.lock.status_query"]
    assert _reply_checks_pass(golden, reply) is passes


def test_integration_catalog_survives_cached_modules_and_registry_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Full test collection imports adapters, then unrelated registry tests replace the
    # global registrations. A cached import cannot replay a registration decorator.
    from core.integrations.apple_calendar import AppleCalendarAdapter

    _integration_tools.cache_clear()
    _integration_tools()  # Load every adapter before simulating that registry reset.
    monkeypatch.setattr(IntegrationRegistry, "_registry", {})
    _integration_tools.cache_clear()
    try:
        capabilities = asyncio.run(AppleCalendarAdapter().get_capabilities())
        expected = {f"integration_{AppleCalendarAdapter.name}_{cap.name}" for cap in capabilities}
        assert expected  # Do not accept a vacuously empty adapter catalog.
        assert expected <= _integration_tools()
        assert IntegrationRegistry.available() == []  # Validation must not mutate the registry.
    finally:
        _integration_tools.cache_clear()


EVALS = Path(__file__).resolve().parents[2] / "evals"


def test_nothing_in_evals_names_the_retired_confirmation_tool() -> None:
    """The model no longer approves anything: an approval is a reply or a Door decision."""
    # Every file, of any extension: a retired name can hide in a fixture or a note.
    files = [p for p in EVALS.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    assert len(files) > 100
    assert [str(p) for p in files if b"confirm_pending_action" in p.read_bytes()] == []


def test_critical_goldens_approve_by_reply_and_by_the_door() -> None:
    goldens = {s.id: s for s in load_suites(["critical_actions"])["critical_actions"]}
    assert "critical_actions.lock.wrong_id" not in goldens
    assert {"critical_actions.lock.door_confirm", "critical_actions.lock.self_approval"} <= set(
        goldens
    )
    with_door = {i for i, s in goldens.items() if any(isinstance(x, DoorStep) for x in s.steps)}
    assert with_door == {
        "critical_actions.lock.door_confirm",
        "critical_actions.lock.repeated_approval",
    }
    for golden in goldens.values():
        sources = [c.params.source for c in golden.expect if c.name == "notification"]
        assert set(sources) <= {"conscious-engine"}, golden.path


def test_the_world_has_a_second_media_player_in_another_room() -> None:
    """The movie golden starts a player that is not the living room's: Reflex must dim the
    room the player is in, wherever that is."""
    world = load_world("apartment")
    players = {e.entity_id: e for e in world.entities if e.domain == "media_player"}
    tv = players["media_player.bedroom_tv"]
    assert (tv.friendly_name, tv.state, world.area_of(tv.entity_id)) == (
        "Bedroom TV",
        "off",
        "bedroom",
    )
    assert world.area_of("media_player.living_room_tv") == "living_room"
    assert len(players) == 2
    assert world.entities_in("bedroom", "media_player") == ["media_player.bedroom_tv"]


def _steps_of(s: Scenario) -> list[HaEventStep]:
    return [step for step in s.steps if isinstance(step, HaEventStep)]


def _causes(s: Scenario) -> dict[tuple[str, str], str]:
    """The cause of each (entity, state) the golden pushes."""
    return {(h.ha_event.entity_id, h.ha_event.state): h.ha_event.by for h in _steps_of(s)}


def test_arrive_after_dark_turns_the_lights_off_by_automation_and_never_reverse_by_outside() -> (
    None
):
    """A light switched off by a person is Reflex's to leave alone: arriving home after dark
    is the golden where Reflex should turn them on, so its lights go off by an automation.
    The never-reverse twin is the same evening with a switch or an app doing it."""
    for golden_id in ("reflex.judgment.arrive_after_dark", "reflex.judgment.arrive_daytime_noop"):
        causes = _causes(_goldens()[golden_id])
        assert causes[("light.living_room_lamp", "off")] == "automation", golden_id
        assert causes[("light.kitchen_pendants", "off")] == "automation", golden_id
    after_dark, outside = (
        _goldens()["reflex.judgment.arrive_after_dark"],
        _goldens()["reflex.never_reverse.outside"],
    )
    assert [step_kind(step) for step in outside.steps] == [step_kind(s) for s in after_dark.steps]
    assert _causes(outside)[("light.living_room_lamp", "off")] == "outside"
    assert _causes(outside)[("light.kitchen_pendants", "off")] == "outside"
    assert [h.ha_event.entity_id for h in _steps_of(outside)] == [
        h.ha_event.entity_id for h in _steps_of(after_dark)
    ]


def test_no_reflex_golden_expects_a_proposal_on_what_a_person_just_changed() -> None:
    """Reflex leaves alone what a person, or something outside the house, just changed (the
    Leave alone line, and ``would_reverse`` behind it). A golden that expects a proposal on
    such a device would pass or fail on the model's luck: its earlier changes must come from
    an automation, which clears the record. Mirrors ``PersonChanges.note`` on the golden's
    own steps."""
    world = load_world("apartment")
    entities = {e.entity_id: e for e in world.entities}
    checked = 0
    for s in _goldens().values():
        if s.suite != "reflex":
            continue
        events = [i for i, step in enumerate(s.steps) if isinstance(step, HaEventStep)]
        for check in s.expect:
            decision = getattr(check.params, "decision", None)
            tool = getattr(check.params, "tool", None)
            if check.name != "reflex_decision" or not set(decision or []) & {"act", "ask"}:
                continue
            if not tool:
                continue
            at = getattr(check.params, "at_step", None)
            judged = events[-1] if at is None else at % len(s.steps)
            wanted = {tool_domain(t, world.services) for t in tool}
            target = getattr(check.params, "target", None)
            left_alone: dict[str, str] = {}  # entity -> the cause of what a person set
            for step in s.steps[:judged]:
                if not isinstance(step, HaEventStep):
                    continue
                e = step.ha_event
                if entities[e.entity_id].domain not in HOUSE_DOMAINS:
                    continue  # Reflex does not note it
                if e.by == "automation":
                    left_alone.pop(e.entity_id, None)
                else:
                    left_alone[e.entity_id] = e.by
            for entity_id, by in left_alone.items():
                in_target = target is None or target in (entity_id, world.area_of(entity_id))
                assert not (entities[entity_id].domain in wanted and in_target), (
                    f"{s.path}: expects {tool} but step changed {entity_id} by {by}; "
                    "make that step `by: automation`"
                )
            checked += 1
    assert checked >= 4  # the after-dark, last-out and dimming goldens


def test_the_never_reverse_goldens_read_the_outcome_of_the_last_change() -> None:
    expected = {
        "reflex.never_reverse.person_off": ("light.living_room_lamp", "off", "person.alex"),
        "reflex.never_reverse.outside": ("person.alex", "home", "outside"),
    }
    for golden_id, (entity_id, state, by) in expected.items():
        s = _goldens()[golden_id]
        assert s.status == "shipped" and s.prd == ["4.4.never-reverse"], golden_id
        assert s.watches_reflex and s.reads_outcomes, golden_id
        last = _steps_of(s)[-1].ha_event
        assert (last.entity_id, last.state, last.by) == (entity_id, state, by), golden_id
        [check] = s.expect
        assert check.name == "reflex_outcome"
        assert check.params.model_dump(exclude_none=True) == {
            "target": "light.living_room_lamp",
            "outcome": ["none", "would_reverse"],
        }, golden_id


def test_a_person_turning_the_lamp_off_after_dark_is_the_never_reverse_premise() -> None:
    s = _goldens()["reflex.never_reverse.person_off"]
    assert _causes(s)[("light.living_room_lamp", "off")] == "person.alex"
    assert ("sun.sun", "below_horizon") in _causes(s)  # the dark that tempts Reflex to light it
    people = {
        e.entity_id: e.state for e in load_world("apartment").entities if e.domain == "person"
    }
    assert set(people.values()) == {"home"}  # both are home, and no step sends them away
    assert not any(h.ha_event.entity_id.startswith("person.") for h in _steps_of(s))


def _system1_saw(text: str) -> Evidence:
    call = LlmCall(
        t=1,
        role="system1",
        latency_ms=1,
        status=200,
        messages=[{"role": "user", "content": text}],
    )
    return Evidence(
        scenario_id="t",
        variant=0,
        epoch=0,
        session_id="t",
        started_at=0,
        ended_at=1,
        llm_calls=[call],
    )


def test_alfred_is_not_a_person_golden_wants_by_alfred_and_no_lamp_on_the_leave_alone_line() -> (
    None
):
    s = _goldens()["reflex.never_reverse.alfred_is_not_a_person"]
    assert s.status == "shipped" and s.prd == ["4.4.never-reverse"]
    assert s.watches_reflex and not s.reads_outcomes
    [ask] = [step for step in s.steps if isinstance(step, UserStep)]
    assert "lamp" in ask.user.lower()
    assert _steps_of(s)[-1].ha_event.entity_id == "person.alex"
    assert _steps_of(s)[-1].ha_event.state == "home"
    contains, absent = s.expect
    assert (contains.name, contains.params.model_dump(exclude_none=True)) == (
        "prompt_contains",
        {"role": "system1", "text": "by Alfred"},
    )
    assert absent.name == "prompt_not_contains"

    # The needle must hit the line Reflex writes for the lamp, and only for the lamp: the line
    # names an entity as the House block does ("Living Room Lamp (Living Room)"), or by its id
    # when the live state does not hold it.
    world = load_world("apartment")
    live = _live_state(world)
    unknown_lamp = {k: v for k, v in live.items() if k != "light.living_room_lamp"}
    now = datetime(2026, 10, 8, 20, 0, tzinfo=UTC)
    lamp_left = render_now(now, "UTC", live, leave_alone=["light.living_room_lamp"])
    both = render_now(
        now, "UTC", live, leave_alone=["light.kitchen_pendants", "light.living_room_lamp"]
    )
    lamp_by_id = render_now(now, "UTC", unknown_lamp, leave_alone=["light.living_room_lamp"])
    no_snapshot = render_now(now, "UTC", None, leave_alone=["light.living_room_lamp"])
    pendants_left = render_now(now, "UTC", live, leave_alone=["light.kitchen_pendants"])
    pendants_by_id = render_now(now, "UTC", None, leave_alone=["light.kitchen_pendants"])
    nothing_left = render_now(now, "UTC", live)
    assert "Leave alone, a person just changed: Living Room Lamp (Living Room)" in lamp_left
    assert "Leave alone, a person just changed: light.living_room_lamp" in lamp_by_id
    assert "Leave alone, a person just changed: light.living_room_lamp" in no_snapshot
    for text, status in (
        (lamp_left, "fail"),
        (both, "fail"),
        (lamp_by_id, "fail"),
        (no_snapshot, "fail"),
        (pendants_left, "pass"),
        (pendants_by_id, "pass"),
        (nothing_left, "pass"),
    ):
        assert run_check(absent.name, absent.params, _system1_saw(text)).status == status, text


def test_the_movie_golden_starts_a_player_in_a_room_with_its_lamp_lit_by_an_automation() -> None:
    s = _goldens()["reflex.judgment.movie_any_player"]
    assert s.status == "shipped" and s.watches_reflex
    causes = _causes(s)
    assert causes[("media_player.bedroom_tv", "playing")] == "person.sam"
    assert causes[("light.bedroom_lamp", "on")] == "automation"
    assert _steps_of(s)[-1].ha_event.entity_id == "media_player.bedroom_tv"
    [check] = s.expect
    assert check.name == "reflex_decision"
    wanted = {
        "decision": ["act", "ask"],
        "tool": ["home.light_turn_on", "home.light_turn_off"],
        "target": "bedroom",
    }
    assert check.params.model_dump(include=set(wanted)) == wanted


def test_the_weather_golden_has_the_rain_come_from_outside_and_wants_it_in_the_prompt() -> None:
    s = _goldens()["reflex.prompt.weather"]
    assert s.status == "shipped" and s.watches_reflex
    [event] = _steps_of(s)
    assert (event.ha_event.entity_id, event.ha_event.state, event.ha_event.by) == (
        "weather.home",
        "rainy",
        "outside",
    )
    [check] = s.expect
    assert (check.name, check.params.model_dump(exclude_none=True)) == (
        "prompt_contains",
        {"role": "system1", "text": " · rainy"},
    )


def test_the_weather_golden_needle_is_the_now_lines_and_not_the_what_changed_lines() -> None:
    """It hits the Now line Reflex writes with the rain in it, and misses the What changed line
    for the same change and a Now line that still says sunny."""
    [check] = _goldens()["reflex.prompt.weather"].expect
    world = load_world("apartment")
    live = _live_state(world)
    rainy = live | {"weather.home": replace(live["weather.home"], state="rainy")}
    now = datetime(2026, 10, 28, 20, 0, tzinfo=UTC)
    change = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="weather.home",
        old_state="sunny",
        new_state="rainy",
    )
    sun_down = rainy | {"sun.sun": replace(live["sun.sun"], state="below_horizon")}
    for text, status in (
        (render_now(now, "UTC", rainy), "pass"),
        (render_now(now, "UTC", sun_down), "pass"),
        (render_now(now, "UTC", live), "fail"),
        (render_event(change, rainy), "fail"),
        (render_event(change, None), "fail"),
    ):
        assert run_check(check.name, check.params, _system1_saw(text)).status == status, text
    assert "rainy" in render_event(change, rainy)  # the word alone would have passed it


ASK_GOLDENS = ("reflex.asks.ask_mode_asks", "reflex.asks.dnd_holds")


def test_the_ask_goldens_run_in_the_small_hours_with_the_quiet_hours_moved_off_them() -> None:
    """Quiet hours are the first limit after the tool: a golden set inside them would be held
    for them, whatever it meant to test. 02:00 is inside the defaults (pinned by unit tests), so
    each golden moves the hours off it and reaches the ask gate."""
    defaults = ReflexSettings()
    for golden_id in ASK_GOLDENS:
        s = _goldens()[golden_id]
        assert s.status == "shipped" and s.prd == ["4.4.tiered-autonomy"], golden_id
        assert s.watches_reflex and s.reads_outcomes, golden_id
        [clock] = [step for step in s.steps if isinstance(step, ClockStep)]
        hour = clock.clock.hour
        assert hour == 2, golden_id
        for minute in (0, 59):  # the whole hour is inside the defaults and outside the golden's
            assert in_quiet_hours(
                time(hour, minute), defaults.quiet_hours_start, defaults.quiet_hours_end
            ), golden_id
        [settings] = [step for step in s.steps if isinstance(step, ReflexSettingsStep)]
        change = settings.reflex_settings
        assert (change.quiet_hours_start, change.quiet_hours_end) == ("04:00", "05:00"), golden_id
        assert change.quiet_hours_start and change.quiet_hours_end
        for minute in (0, 59):
            assert not in_quiet_hours(
                time(hour, minute), change.quiet_hours_start, change.quiet_hours_end
            ), golden_id
        assert change.mode == "ask", golden_id
        patterns = change.ask_tools or []
        assert any(fnmatch("home.light_turn_off", p) for p in patterns), golden_id
        assert patterns == (["home.light_*"] if "ask_mode" in golden_id else ["home.*"])
        # Everything that sets the scene comes before the first event Reflex judges.
        kinds = [step_kind(step) for step in s.steps]
        first_event = kinds.index("ha_event")
        assert kinds.index("clock") < kinds.index("reflex_settings") < first_event, golden_id
        events = [(h.ha_event.entity_id, h.ha_event.state) for h in _steps_of(s)]
        assert events == [
            ("sun.sun", "below_horizon"),
            ("light.living_room_ceiling", "on"),
        ], golden_id
        # A device's change, not a person's: a light a person switched on is left alone, so a
        # proposal to turn it off would be would_reverse and the golden would never reach the ask.
        assert _causes(s)[("light.living_room_ceiling", "on")] == "automation", golden_id
        assert kinds[-1] == "wait", golden_id  # the decision, and the ask it parks, land in it


def test_each_ask_golden_switches_a_light_that_the_world_starts_in_another_state() -> None:
    """Reflex drops an update that leaves the state as it was (an attribute-only update), so a
    golden that switches a light to the state the world starts it in never reaches the model:
    its checks find no decision about the step."""
    for golden_id in ASK_GOLDENS:
        s = _goldens()[golden_id]
        world = load_world(s.world)
        start = {e.entity_id: e.state for e in world.entities}
        lights = [h.ha_event for h in _steps_of(s) if h.ha_event.entity_id.startswith("light.")]
        assert lights, golden_id
        for event in lights:
            assert event.entity_id in start, (golden_id, event.entity_id)
            assert event.state != start[event.entity_id], (
                f"{golden_id}: {event.entity_id} already starts {start[event.entity_id]!r} in "
                f"the {world.name} world, so this event changes nothing"
            )


def test_the_dnd_golden_lets_any_home_tool_be_asked_about_so_only_dnd_can_hold_it() -> None:
    steps = _goldens()["reflex.asks.dnd_holds"].steps
    [settings] = [step for step in steps if isinstance(step, ReflexSettingsStep)]
    asked_about = settings.reflex_settings.ask_tools or []
    for tool in ("home.light_turn_off", "home.switch_turn_off", "home.cover_close_cover"):
        assert any(fnmatch(tool, p) for p in asked_about), tool


def test_the_ask_golden_expects_an_ask_or_nothing_and_finds_the_ask_it_made() -> None:
    s = _goldens()["reflex.asks.ask_mode_asks"]
    outcome, parked = s.expect
    assert outcome.name == "reflex_outcome"
    assert outcome.params.model_dump(exclude_none=True) == {
        # A target that names nothing is invalid and never reaches the gate, so it asks nothing.
        "outcome": ["asked", "none", "held", "invalid"],
        # Held only for an ask that is already open or was just made; no other limit.
        "reason": ["ask_open", "asked_recently"],
    }
    assert parked.name == "reflex_ask_parked"
    # The ask is parked just after the model answers, which is after the last event's own
    # snapshot: the golden ends on a step that waits, so the last snapshot shows it.
    assert step_kind(s.steps[-1]) == "wait"


def test_the_dnd_golden_turns_do_not_disturb_on_before_the_events_and_expects_it_to_hold() -> None:
    s = _goldens()["reflex.asks.dnd_holds"]
    kinds = [step_kind(step) for step in s.steps]
    [dnd] = [step for step in s.steps if isinstance(step, DndStep)]
    assert dnd.dnd is True and kinds.index("dnd") < kinds.index("ha_event")
    [outcome] = s.expect
    assert outcome.name == "reflex_outcome"
    assert outcome.params.model_dump(exclude_none=True) == {
        "outcome": ["held", "none", "invalid"],
        "reason": ["dnd"],
    }


def test_neither_ask_golden_can_pass_on_an_ask_that_went_out_or_a_hold_for_another_reason() -> None:
    """The scoring, on the log a bad Reflex would leave. The dnd golden fails on an ask and on
    a hold for the quiet hours; the ask golden fails on a proposal left as a shadow, or held
    for a limit it had no business hitting at 02:00."""
    from tests.evals.harness.test_checks_reflex import ev, logged

    def status(golden_id: str, outcome: str, reason: str | None) -> str:
        [check, *_] = _goldens()[golden_id].expect
        evidence = ev(decisions=(logged(outcome, reason=reason),))
        return run_check(check.name, check.params, evidence).status

    for outcome, reason, dnd, ask in (
        ("none", None, "pass", "pass"),
        ("invalid", None, "pass", "pass"),
        ("held", "dnd", "pass", "fail"),
        ("held", "quiet_hours", "fail", "fail"),
        ("held", "ask_open", "fail", "pass"),
        ("held", "daily_cap", "fail", "fail"),
        ("asked", None, "fail", "pass"),
        ("shadow", None, "fail", "fail"),
    ):
        assert status("reflex.asks.dnd_holds", outcome, reason) == dnd, (outcome, reason)
        assert status("reflex.asks.ask_mode_asks", outcome, reason) == ask, (outcome, reason)
