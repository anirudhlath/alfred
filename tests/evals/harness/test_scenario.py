from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from evals.harness.scenario import (
    HaEventStep,
    ScenarioError,
    UserStep,
    expand_variants,
    load_suites,
    select,
)

if TYPE_CHECKING:
    from pathlib import Path

GOOD = """
id: demo.lights.on
prd: [4.4.lights-scenes]
status: shipped
tags: [lights]
as: {who: sir, channel: signal}
steps:
  - user: "Turn on the bedroom lamp."
    variants: ["Bedroom lamp on.", "Lamp in the bedroom, please."]
  - ha_event: {entity_id: light.x, state: "on"}
expect:
  - ha_called: {domain: light, service: turn_on, entity_id: light.bedroom_lamp}
  - judge: {category: answered, rubric: "Does the reply confirm the lamp is on?"}
"""


def write(root: Path, suite: str, name: str, body: str) -> Path:
    path = root / suite / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


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
        ("  - ha_called:", "  - ha_callled:", "unknown check"),
        ("  - ha_event:", "  - ha_evnt:", "needs one of user, ha_event, wait"),
        ("id: demo.lights.on", "id: other.lights.on", "must start with 'demo.'"),
        ('state: "on"', "state: on", "valid string"),
        ("service: turn_on,", "", "service"),
    ],
)
def test_bad_scenarios_name_the_file(
    tmp_path: Path, needle: str, replacement: str, message: str
) -> None:
    path = write(tmp_path, "demo", "bad.yaml", GOOD.replace(needle, replacement))
    with pytest.raises(ScenarioError) as err:
        load_suites(root=tmp_path)
    assert str(path) in str(err.value) and message in str(err.value)


def test_two_steps_with_variants_is_an_error(tmp_path: Path) -> None:
    body = GOOD.replace(
        '  - ha_event: {entity_id: light.x, state: "on"}',
        '  - user: "and again"\n    variants: ["again"]',
    )
    write(tmp_path, "demo", "bad.yaml", body)
    with pytest.raises(ScenarioError, match="only one step may have variants"):
        load_suites(root=tmp_path)


def test_duplicate_ids_across_files(tmp_path: Path) -> None:
    write(tmp_path, "demo", "a.yaml", GOOD)
    write(tmp_path, "demo", "b.yaml", GOOD)
    with pytest.raises(ScenarioError, match="duplicate id"):
        load_suites(root=tmp_path)


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
