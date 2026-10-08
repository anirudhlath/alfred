from __future__ import annotations

from functools import cache
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from evals.harness.checks import JudgeSpec, run_check
from evals.harness.checks.llm import normalize_tool
from evals.harness.evidence import Evidence, Reply
from evals.harness.scenario import CheckSpec, HaEventStep, Scenario, UserStep, load_suites
from evals.harness.world import World, load_world

SUITES = ["conversation", "home_control", "reflex"]


@cache
def _goldens() -> dict[str, Scenario]:
    return {s.id: s for scenarios in load_suites(SUITES).values() for s in scenarios}


def test_every_golden_loads_and_names_real_entities() -> None:
    world_ids = {e.entity_id for e in load_world("apartment").entities}
    suites = load_suites(SUITES)
    assert (
        len(suites["conversation"]) >= 8
        and len(suites["home_control"]) >= 13
        and len(suites["reflex"]) >= 13
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


def _step_indexes(s: Scenario) -> list[tuple[str, object, int]]:
    """``(check.field, value, n)`` for every reply step index ``s``'s checks carry.

    The loader does not check these, so a bad one would only error in a live run. Any check
    whose params model has a ``step`` field is covered, including checks added later.
    ``step`` (reply and latency checks today) indexes ``Evidence.replies``, which holds one
    reply per *user* step (``checks/reply.py``, ``checks/latency.py``), so n counts user
    steps; ``"any"`` means every reply. It is a plain Python list index, so a valid one
    lies in ``-n <= i < n``.

    ``after_step`` counts every step, which the loader knows, so the loader rejects a bad
    one (``Scenario._coherent``): the driver reads it mid-play.
    """
    users = sum(isinstance(step, UserStep) for step in s.steps)
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


def _unknown_services(s: Scenario, world: World) -> list[str]:
    """HA services and home tools that ``s``'s checks name but ``world`` does not offer.

    home-service offers each world service as the tool ``home.{domain}_{service}``, which
    System 2 sends as ``home_{domain}_{service}`` (``checks/llm.normalize_tool``), so a tool
    name starting ``home.`` or ``home_`` must be one of those. Like the
    step guard, this goes by field name, so a check added later is covered too. A check
    that leaves ``domain`` or ``service`` unset matches any, so only what it sets is checked.
    """
    offered = {(d, svc) for d, services in world.services.items() for svc in services}
    tools = {normalize_tool(f"home.{d}_{svc}") for d, svc in offered}
    bad: list[str] = []
    for check in s.expect:
        domain, service, tool = (
            getattr(check.params, f, None) for f in ("domain", "service", "tool")
        )
        if (domain or service) and not any(
            domain in (None, d) and service in (None, svc) for d, svc in offered
        ):
            bad.append(f"{check.name}: {domain or '*'}.{service or '*'}")
        name = None if tool is None else normalize_tool(tool)
        # Only home tools are checked here; memory, trigger etc. tools when their suites arrive.
        if name is not None and name.startswith("home_") and name not in tools:
            bad.append(f"{check.name}: tool {tool}")
    return bad


def test_every_golden_names_real_services_and_tools() -> None:
    world = load_world("apartment")
    seen: set[str] = set()
    home_tools = 0
    for s in _goldens().values():
        assert not _unknown_services(s, world), f"{s.path}: {_unknown_services(s, world)}"
        seen |= {f for c in s.expect for f in ("service", "tool") if getattr(c.params, f, None)}
        tools = [t for c in s.expect if isinstance(t := getattr(c.params, "tool", None), str)]
        home_tools += sum(normalize_tool(t).startswith("home_") for t in tools)
    # The lookup matched something, and at least one tool was a home tool the guard checks:
    # otherwise every tool could be skipped as non-home and the test would pass vacuously.
    assert seen == {"service", "tool"} and home_tools > 0


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
            {"tool_called": {"tool": "memory_recall_memories"}},  # not a home tool: not judged
        ],
    )
    assert _unknown_services(real, world) == []
    typos = _scenario(
        [{"user": "a"}],
        [
            {"ha_called": {"domain": "light", "service": "turn_of"}},
            {"ha_not_called": {"domain": "lights"}},
            {"ha_not_called": {"service": "turn_of"}},
            {"ha_called": {"domain": "switch", "service": "volume_set"}},
            {"llm_tool_args": {"tool": "home.light_turn_of", "args": {}}},
            {"tool_not_called": {"tool": "home_switch_turn_onn"}},
        ],
    )
    assert _unknown_services(typos, world) == [
        "ha_called: light.turn_of",
        "ha_not_called: lights.*",
        "ha_not_called: *.turn_of",
        "ha_called: switch.volume_set",
        "llm_tool_args: tool home.light_turn_of",
        "tool_not_called: tool home_switch_turn_onn",
    ]


def _reply_checks_pass(s: Scenario, text: str) -> bool:
    """Whether every reply_contains/reply_not_contains check in ``s`` passes when Alfred
    answers each user step with ``text``."""
    users = sum(isinstance(step, UserStep) for step in s.steps)
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
