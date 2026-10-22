from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from pydantic import ValidationError

from evals.harness.checks import JudgeSpec
from evals.harness.scenario import (
    AdvanceTriggerStep,
    CheckSpec,
    DndStep,
    DoorStep,
    HaEventStep,
    Scenario,
    ScenarioError,
    UserStep,
    available_suites,
    expand_variants,
    load_suites,
    select,
    step_kind,
)

if TYPE_CHECKING:
    from pathlib import Path

USER_STEP = """  - user: "Turn on the bedroom lamp."
    variants: ["Bedroom lamp on.", "Lamp in the bedroom, please."]"""
EVENT_STEP = '  - ha_event: {entity_id: light.x, state: "on"}'
HA_CALLED = "  - ha_called: {domain: light, service: turn_on, entity_id: light.bedroom_lamp}"

GOOD = f"""
id: demo.lights.on
prd: [4.4.lights-scenes]
status: shipped
tags: [lights]
as: {{who: sir, channel: signal}}
steps:
{USER_STEP}
{EVENT_STEP}
expect:
{HA_CALLED}
  - judge: {{category: answered, rubric: "Does the reply confirm the lamp is on?"}}
"""


def write(root: Path, suite: str, name: str, body: str) -> Path:
    path = root / suite / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def load_error(tmp_path: Path, body: str) -> tuple[Path, str]:
    path = write(tmp_path, "demo", "bad.yaml", body)
    with pytest.raises(ScenarioError) as err:
        load_suites(root=tmp_path)
    return path, str(err.value)


def test_loads_a_good_scenario(tmp_path: Path) -> None:
    write(tmp_path, "demo", "a.yaml", GOOD)
    [s] = load_suites(root=tmp_path)["demo"]
    assert s.suite == "demo" and s.actor.channel == "signal"
    assert isinstance(s.steps[0], UserStep) and isinstance(s.steps[1], HaEventStep)
    assert [c.name for c in s.expect] == ["ha_called", "judge"]


def test_variants_expand_to_separate_samples(tmp_path: Path) -> None:
    write(tmp_path, "demo", "a.yaml", GOOD)
    [s] = load_suites(root=tmp_path)["demo"]
    variants = expand_variants(s)
    assert [v.sample_id for v in variants] == [
        "demo.lights.on",
        "demo.lights.on~1",
        "demo.lights.on~2",
    ]
    first = variants[1].steps[0]
    assert isinstance(first, UserStep) and first.user == "Bedroom lamp on." and first.variants == []


@pytest.mark.parametrize(
    ("needle", "replacement", "message"),
    [
        ("  - ha_called:", "  - ha_callled:", "unknown check 'ha_callled'"),
        ("  - ha_event:", "  - ha_evnt:", "needs one of user, ha_event, wait"),
        (EVENT_STEP, '  - {user: "hi", wait: 1}', "needs one of user, ha_event, wait"),
        ("id: demo.lights.on", "id: other.lights.on", "must start with 'demo.'"),
        ('state: "on"', "state: on", "valid string"),
        ("service: turn_on,", "", "expect.0.service\n  Field required"),
        (EVENT_STEP, f"{EVENT_STEP}\n    setle: 1", "steps.1.ha_event.setle\n"),
        ('variants: ["Bedroom lamp on.",', 'variants: ["",', "String should have at least 1"),
        (
            HA_CALLED,
            "  - {name: ha_callled, params: {anything: 1}}",
            "unknown check 'ha_callled'",
        ),
        (
            HA_CALLED,
            "  - {name: ha_called, params: {domain: light, anything: 1}}",
            "expect.0.service\n  Field required",
        ),
        (
            HA_CALLED,
            "  - {name: ha_called, params: {domain: light, anything: 1}}",
            "expect.0.anything\n  Extra inputs are not permitted",
        ),
        (USER_STEP + "\n", "", "reply and judge checks need at least one user step"),
        ("status: shipped", "status: shipped\nsuite: other", "set by the loader"),
        ("status: shipped", "status: shipped\npath: elsewhere.yaml", "set by the loader"),
        # Falsy params are not "no params": `false` is a mistake, not ha_not_called's {}.
        (HA_CALLED, "  - ha_not_called: false", "expect.0\n  Input should be a valid dictionary"),
        (HA_CALLED, "  - ha_not_called: []", "expect.0\n  Input should be a valid dictionary"),
        ('user: "Turn on the bedroom lamp."', 'user: "   "', "must not be blank"),
        ('variants: ["Bedroom lamp on.",', 'variants: [" \\t",', "must not be blank"),
        # after_step indexes every step (2 here), as Python reads a list: -2 to 1.
        (
            "entity_id: light.bedroom_lamp}",
            "entity_id: light.bedroom_lamp, after_step: 2}",
            "expect.0.after_step is 2, but the golden has 2 steps (-2 to 1)",
        ),
        (
            "entity_id: light.bedroom_lamp}",
            "entity_id: light.bedroom_lamp, after_step: -3}",
            "expect.0.after_step is -3, but the golden has 2 steps (-2 to 1)",
        ),
    ],
    ids=[
        "unknown-check",
        "unknown-step-kind",
        "two-step-kinds",
        "id-outside-suite",
        "unquoted-on",
        "missing-param",
        "unknown-key-in-step",
        "empty-variant",
        "name-params-unknown-check",
        "name-params-missing-param",
        "name-params-extra-param",
        "reply-check-without-user-step",
        "golden-sets-suite",
        "golden-sets-path",
        "falsy-params",
        "empty-list-params",
        "blank-user",
        "blank-variant",
        "after-step-past-the-end",
        "after-step-before-the-start",
    ],
)
def test_bad_scenarios_name_the_file(
    tmp_path: Path, needle: str, replacement: str, message: str
) -> None:
    assert needle in GOOD
    path, error = load_error(tmp_path, GOOD.replace(needle, replacement))
    assert error.startswith(f"{path}: ") and message in error


def test_step_errors_carry_the_step_index(tmp_path: Path) -> None:
    _, error = load_error(tmp_path, GOOD.replace('state: "on"', "state: on"))
    assert "steps.1." in error and ".state\n" in error


def test_two_steps_with_variants_is_an_error(tmp_path: Path) -> None:
    body = GOOD.replace(EVENT_STEP, '  - user: "and again"\n    variants: ["again"]')
    path, error = load_error(tmp_path, body)
    assert error.startswith(f"{path}: ") and "only one step may have variants" in error


def test_undecodable_file_names_the_file(tmp_path: Path) -> None:
    path = write(tmp_path, "demo", "bad.yaml", "")
    path.write_bytes(b"id: \xff\xfe\n")
    with pytest.raises(ScenarioError, match="not UTF-8") as err:
        load_suites(root=tmp_path)
    assert str(err.value).startswith(f"{path}: ")


def test_a_null_params_mapping_means_the_defaults(tmp_path: Path) -> None:
    write(tmp_path, "demo", "a.yaml", GOOD.replace(HA_CALLED, "  - ha_not_called:"))
    [s] = load_suites(root=tmp_path)["demo"]
    assert s.expect[0].name == "ha_not_called"


def test_an_unreadable_file_names_the_file(tmp_path: Path) -> None:
    path = write(tmp_path, "demo", "bad.yaml", GOOD)
    path.chmod(0)
    try:
        if os.access(path, os.R_OK):
            pytest.skip("running as a user that can read any file")
        with pytest.raises(ScenarioError, match="cannot read") as err:
            load_suites(root=tmp_path)
    finally:
        path.chmod(0o644)
    assert str(err.value).startswith(f"{path}: ")


@pytest.mark.parametrize("stray", ["b.yml", "README.md", "nested"])
def test_a_stray_entry_in_a_suite_is_an_error(tmp_path: Path, stray: str) -> None:
    write(tmp_path, "demo", "a.yaml", GOOD)
    entry = tmp_path / "demo" / stray
    if stray == "nested":
        entry.mkdir()
    else:
        entry.write_text(GOOD)
    with pytest.raises(ScenarioError) as err:
        load_suites(root=tmp_path)
    assert str(err.value).startswith(f"{entry}: ") and "*.yaml" in str(err.value)


def test_hidden_entries_in_a_suite_are_ignored(tmp_path: Path) -> None:
    write(tmp_path, "demo", "a.yaml", GOOD)
    write(tmp_path, "demo", ".gitkeep", "")
    write(tmp_path, "demo", ".x.yaml.swp", "\x00 not yaml")
    write(tmp_path, "empty", ".gitkeep", "")
    assert available_suites(tmp_path) == ["demo"]
    assert [s.id for s in load_suites(root=tmp_path)["demo"]] == ["demo.lights.on"]


def test_a_suite_of_only_yml_files_is_not_skipped(tmp_path: Path) -> None:
    entry = write(tmp_path, "demo", "a.yml", GOOD)
    with pytest.raises(ScenarioError) as err:
        load_suites(root=tmp_path)
    assert str(err.value).startswith(f"{entry}: ")


def test_duplicate_ids_across_files(tmp_path: Path) -> None:
    write(tmp_path, "demo", "a.yaml", GOOD)
    write(tmp_path, "demo", "b.yaml", GOOD)
    with pytest.raises(ScenarioError, match="duplicate id"):
        load_suites(root=tmp_path)


def test_a_suite_named_twice_loads_once(tmp_path: Path) -> None:
    write(tmp_path, "demo", "a.yaml", GOOD)
    loaded = load_suites(["demo", "demo"], root=tmp_path)
    assert list(loaded) == ["demo"] and len(loaded["demo"]) == 1


def test_unknown_suite_lists_the_available_ones(tmp_path: Path) -> None:
    write(tmp_path, "demo", "a.yaml", GOOD)
    with pytest.raises(ScenarioError, match="available: demo"):
        load_suites(["nope"], root=tmp_path)


def test_select_filters_pending_and_tags(tmp_path: Path) -> None:
    write(tmp_path, "demo", "a.yaml", GOOD)
    write(
        tmp_path,
        "demo",
        "b.yaml",
        GOOD.replace("demo.lights.on", "demo.lights.off")
        .replace("status: shipped", "status: pending")
        .replace("[lights]", "[other]"),
    )
    scenarios = load_suites(root=tmp_path)["demo"]
    assert [s.id for s in select(scenarios)] == ["demo.lights.on"]
    assert len(select(scenarios, include_pending=True)) == 2
    assert [s.id for s in select(scenarios, tags=["other"], include_pending=True)] == [
        "demo.lights.off"
    ]


def test_a_loaded_scenario_round_trips(tmp_path: Path) -> None:
    write(tmp_path, "demo", "a.yaml", GOOD)
    [s] = load_suites(root=tmp_path)["demo"]
    assert Scenario.model_validate(s.model_dump()) == s
    assert Scenario.model_validate_json(s.model_dump_json()) == s
    judge = s.expect[1]
    assert isinstance(judge.params, JudgeSpec)
    again = CheckSpec(name=judge.name, params=judge.params)
    assert again == judge and isinstance(again.params, JudgeSpec)


@pytest.mark.parametrize(
    ("after_step", "expected"),
    [
        (None, [True, True, True]),  # any call in the window counts, so every event waits
        (1, [False, True, True]),
        (2, [False, False, True]),
        (-1, [False, False, True]),  # the last step, as Evidence.step_started[-1] reads it
    ],
)
def test_ha_called_counting_names_the_checks_a_call_from_that_step_could_count_for(
    after_step: int | None, expected: list[bool]
) -> None:
    called: dict[str, object] = {"domain": "light", "service": "turn_on"}
    if after_step is not None:
        called["after_step"] = after_step
    s = Scenario.model_validate(
        {
            "id": "demo.lights.on",
            "prd": ["x"],
            "status": "shipped",
            "steps": [
                {"user": "Hello."},
                {"ha_event": {"entity_id": "light.x", "state": "on"}},
                {"ha_event": {"entity_id": "light.x", "state": "off"}},
            ],
            "expect": [{"ha_not_called": {"domain": "switch"}}, {"ha_called": called}],
        }
    )
    counting = [s.ha_called_counting(i) for i in range(3)]
    assert [bool(c) for c in counting] == expected
    # Each comes back with its after_step made non-negative, ready to index step_started.
    after = None if after_step is None else after_step % 3
    assert all(p.after_step == after and p.service == "turn_on" for c in counting for p in c)


def test_a_golden_without_ha_called_expects_no_call() -> None:
    s = Scenario.model_validate(
        {
            "id": "demo.lights.quiet",
            "prd": ["x"],
            "status": "shipped",
            "steps": [{"ha_event": {"entity_id": "light.x", "state": "on"}}],
            "expect": [{"ha_not_called": {}}],
        }
    )
    assert s.ha_called_counting(0) == []


EVENT: dict[str, Any] = {"ha_event": {"entity_id": "light.bedroom_lamp", "state": "on"}}


def golden(steps: list[dict[str, Any]], expect: list[dict[str, Any]]) -> Scenario:
    return Scenario.model_validate(
        {"id": "reflex.t.t", "prd": ["x"], "status": "shipped", "steps": steps, "expect": expect}
    )


def test_the_new_steps_load_from_yaml() -> None:
    raw = yaml.safe_load(
        """
id: reflex.t.t
prd: [x]
status: shipped
steps:
  - clock: {hour: 22}
  - dnd: on
  - advance_trigger:
  - advance_trigger: {name: laundry}
    settle: 5
  - ha_event: {entity_id: light.bedroom_lamp, state: "on"}
expect:
  - reflex_decision: {decision: none}
"""
    )
    s = Scenario.model_validate(raw)
    kinds = [step_kind(step) for step in s.steps]
    assert kinds == ["clock", "dnd", "advance_trigger", "advance_trigger", "ha_event"]
    assert isinstance(s.steps[1], DndStep) and s.steps[1].dnd is True
    assert isinstance(s.steps[2], AdvanceTriggerStep) and s.steps[2].advance_trigger.name is None
    assert s.watches_reflex


@pytest.mark.parametrize(
    "step", [{"dnd": "on"}, {"clock": {"hour": 24}}, {"clock": {}}, {"advance_trigger": {"x": 1}}]
)
def test_malformed_new_steps_fail_to_load(step: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        golden([step], [{"ha_not_called": {}}])


def test_at_step_must_name_a_step_of_the_kind_the_check_reads() -> None:
    steps = [{"user": "a"}, EVENT]
    golden(steps, [{"reflex_decision": {"decision": "none", "at_step": 1}}])
    with pytest.raises(ValidationError, match="at_step 0 is a user step, not ha_event"):
        golden(steps, [{"reflex_decision": {"decision": "none", "at_step": 0}}])
    with pytest.raises(ValidationError, match="at_step is 2, but the golden has 2 steps"):
        golden(steps, [{"reflex_not_proposed": {"tool": "home.light_turn_on", "at_step": 2}}])
    with pytest.raises(ValidationError, match="needs a ha_event step"):
        golden([{"user": "a"}], [{"reflex_decision": {"decision": "none"}}])
    fire_ms = {"latency": {"metric": "reminder_fire_ms", "max": 5000}}
    with pytest.raises(ValidationError, match="needs an advance_trigger step"):
        golden([{"user": "a"}], [fire_ms])
    golden([{"user": "a"}, {"advance_trigger": {}}], [fire_ms])


def test_a_reflex_latency_golden_needs_no_user_step() -> None:
    s = golden([EVENT], [{"latency": {"metric": "reflex_ms", "max": 500}}])
    assert s.watches_reflex
    with pytest.raises(ValidationError, match="need at least one user step"):
        golden([EVENT], [{"latency": {"metric": "reply_ms", "max": 500}}])


def test_a_plain_golden_does_not_watch_reflex() -> None:
    assert not golden([{"user": "a"}], [{"reply_contains": {"text": "x"}}]).watches_reflex


# Every check step_kind_needed names, with the kind of step its at_step must name.
AT_STEP_CHECKS = [
    ("reflex_decision", {"decision": "none"}, "ha_event"),
    ("reflex_not_proposed", {"tool": "home.light_turn_on"}, "ha_event"),
    ("latency", {"metric": "reflex_ms", "max": 500}, "ha_event"),
    ("latency", {"metric": "reminder_fire_ms", "max": 5000}, "advance_trigger"),
]
# One step of each kind the checks read, between two user steps: -4 to 3.
MIXED: list[dict[str, Any]] = [{"user": "a"}, EVENT, {"advance_trigger": {}}, {"user": "b"}]
INDEX_OF = {"ha_event": 1, "advance_trigger": 2}
A_STEP = {"ha_event": "a ha_event step", "advance_trigger": "an advance_trigger step"}


@pytest.mark.parametrize(("name", "params", "kind"), AT_STEP_CHECKS)
def test_every_step_reading_check_takes_an_at_step_of_its_kind_counted_either_way(
    name: str, params: dict[str, Any], kind: str
) -> None:
    for at in (INDEX_OF[kind], INDEX_OF[kind] - len(MIXED)):
        golden(MIXED, [{name: {**params, "at_step": at}}])
    for at in (0, 3, -4, -1):  # user steps, from either end
        with pytest.raises(ValidationError, match=f"at_step {at} is a user step, not {kind}"):
            golden(MIXED, [{name: {**params, "at_step": at}}])
    other_kind, other = next((k, i) for k, i in INDEX_OF.items() if k != kind)
    for at in (other, other - len(MIXED)):
        with pytest.raises(
            ValidationError, match=f"at_step {at} is {A_STEP[other_kind]}, not {kind}"
        ):
            golden(MIXED, [{name: {**params, "at_step": at}}])
    for at in (4, -5):
        with pytest.raises(ValidationError, match=f"at_step is {at}, but the golden has 4 steps"):
            golden(MIXED, [{name: {**params, "at_step": at}}])
    with pytest.raises(ValidationError, match=f"needs {A_STEP[kind]}"):
        golden([{"user": "a"}], [{name: params}])


@pytest.mark.parametrize(
    ("at", "named"), [(0, "a wait step"), (1, "a clock step"), (2, "a dnd step")]
)
def test_the_wrong_kind_message_names_every_kind_with_its_article(at: int, named: str) -> None:
    steps: list[dict[str, Any]] = [{"wait": 1}, {"clock": {"hour": 7}}, {"dnd": True}, EVENT]
    with pytest.raises(ValidationError, match=f"at_step {at} is {named}, not ha_event"):
        golden(steps, [{"reflex_decision": {"decision": "none", "at_step": at}}])


def test_pending_id_fixture_loads_and_variants_preserve_it() -> None:
    steps = [
        {"user": "Unlock the door."},
        {
            "user": "Confirm {pending_action_id}.",
            "variants": ["Yes, action {pending_action_id}."],
            "pending_action": {"at_step": 0, "tool": "home_lock_unlock"},
        },
    ]
    s = golden(steps, [{"pending_action": {"at_step": 0, "tool": "home.lock_unlock"}}])
    for variant in expand_variants(s):
        assert variant.steps[1].pending_action.at_step == 0


@pytest.mark.parametrize(
    "bad",
    [
        {"user": "Confirm it.", "pending_action": {"at_step": 0, "tool": "home.lock_unlock"}},
        {
            "user": "Confirm {pending_action_id}.",
            "pending_action": {"at_step": 1, "tool": "home.lock_unlock"},
        },
        {
            "user": "Confirm {pending_action_id}.",
            "pending_action": {"at_step": 2, "tool": "home.lock_unlock"},
        },
        {
            "user": "Confirm {pending_action_id}.",
            "pending_action": {"at_step": -1, "tool": "home.lock_unlock"},
        },
        {
            "user": "Confirm {pending_action_id}.",
            "pending_action": {"at_step": 0, "tool": "home.lock_unlock", "parameters": {}},
        },
        {
            "user": "Confirm {pending_action_id}.",
            "pending_action": {"at_step": 0, "tool": "home.lock_unlock"},
            "variants": ["Yes."],
        },
        {"user": "Confirm {pending_action_id}."},
        {
            "user": "Confirm {other_id}.",
            "pending_action": {"at_step": 0, "tool": "home.lock_unlock"},
        },
        {
            "user": "Confirm {pending_action_id} {other_id}.",
            "pending_action": {"at_step": 0, "tool": "home.lock_unlock"},
        },
        {
            "user": "Confirm {{pending_action_id}}.",
            "pending_action": {"at_step": 0, "tool": "home.lock_unlock"},
        },
    ],
)
def test_invalid_pending_fixtures_fail_before_play(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        golden([{"user": "Unlock it."}, bad], [{"ha_not_called": {}}])


@pytest.mark.parametrize("at", [0, 1, -1, -2])
def test_ha_counting_scopes_to_one_step_and_normalizes_negative_index(at: int) -> None:
    s = golden(
        [{"user": "a"}, {"user": "b"}],
        [{"ha_called": {"domain": "lock", "service": "unlock", "at_step": at}}],
    )
    normalized = at % 2
    assert [bool(s.ha_called_counting(i)) for i in range(2)] == [i == normalized for i in range(2)]
    assert s.ha_called_counting(normalized)[0].at_step == normalized


EXPECT: dict[str, Any] = {"ha_not_called": {}}
DOOR: dict[str, Any] = {
    "door": "confirm",
    "pending_action": {"at_step": 0, "tool": "home.lock_unlock"},
}


def test_a_door_step_loads_with_its_selector_and_a_default_settle() -> None:
    s = golden([{"user": "Unlock it."}, DOOR, {**DOOR, "door": "deny", "settle": 0}], [EXPECT])
    confirm, deny = s.steps[1], s.steps[2]
    assert isinstance(confirm, DoorStep) and isinstance(deny, DoorStep)
    assert [step_kind(step) for step in s.steps] == ["user", "door", "door"]
    assert (confirm.door, confirm.pending_action.tool, confirm.settle) == (
        "confirm",
        "home.lock_unlock",
        3.0,
    )
    assert (deny.door, deny.settle) == ("deny", 0)
    assert [type(v.steps[1]) for v in expand_variants(s)] == [DoorStep]


def test_a_door_step_needs_a_selector() -> None:
    with pytest.raises(ValidationError, match="pending_action"):
        golden([{"user": "Unlock it."}, {"door": "confirm"}], [EXPECT])


@pytest.mark.parametrize("decision", ["approve", "yes", "Confirm", "", None, True])
def test_a_door_steps_decision_is_confirm_or_deny(decision: object) -> None:
    with pytest.raises(ValidationError):
        golden([{"user": "Unlock it."}, {**DOOR, "door": decision}], [EXPECT])


UNLOCK: dict[str, Any] = {"user": "Unlock it."}
WAIT: dict[str, Any] = {"wait": 1}


def door_at(**bad: Any) -> list[dict[str, Any]]:
    return [UNLOCK, {**DOOR, **bad}, WAIT]


@pytest.mark.parametrize(
    "steps",
    [
        door_at(settle=-1),
        door_at(extra=1),
        door_at(pending_action={"at_step": 0, "tool": "home.lock_unlock", "parameters": {}}),
        door_at(pending_action={"at_step": -1, "tool": "home.lock_unlock"}),
        door_at(pending_action={"at_step": 1, "tool": "home.lock_unlock"}),  # itself
        door_at(pending_action={"at_step": 2, "tool": "home.lock_unlock"}),  # a later step
        door_at(user="Yes."),  # two kinds in one step
        # An earlier step that is not a user step has no request to wait a follow-up of.
        [WAIT, UNLOCK, DOOR],
        [{"dnd": True}, UNLOCK, {**DOOR, "pending_action": {"at_step": 0, "tool": "t"}}],
        [UNLOCK, DOOR, {**DOOR, "pending_action": {"at_step": 1, "tool": "t"}}],  # a door step
    ],
)
def test_an_invalid_door_step_fails_before_play(steps: list[dict[str, Any]]) -> None:
    with pytest.raises(ValidationError):
        golden(steps, [EXPECT])


def test_a_door_step_may_name_a_user_step_with_others_in_between() -> None:
    s = golden([UNLOCK, WAIT, {"user": "Yes."}, DOOR], [EXPECT])
    assert [step_kind(step) for step in s.steps] == ["user", "wait", "user", "door"]
