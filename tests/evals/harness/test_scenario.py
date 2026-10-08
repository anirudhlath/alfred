from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from evals.harness.checks import JudgeSpec
from evals.harness.scenario import (
    CheckSpec,
    HaEventStep,
    Scenario,
    ScenarioError,
    UserStep,
    available_suites,
    expand_variants,
    load_suites,
    select,
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
