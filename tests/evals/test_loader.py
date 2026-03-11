"""Tests for evals.loader — YAML scenario discovery and validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import pathlib

from evals.loader import load_scenario, load_scenarios


@pytest.fixture
def scenarios_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a temp directory with test scenario YAML files."""
    home = tmp_path / "home"
    home.mkdir()

    valid = home / "test_valid.yaml"
    valid.write_text(
        "name: test_valid\n"
        "tags: [home, test]\n"
        "event:\n"
        "  domain: home\n"
        "  entity_id: light.lr\n"
        "  new_state: 'on'\n"
        "  source: eval\n"
        "expected:\n"
        "  tool_name: lighting.dim_lights\n"
    )

    no_action = home / "test_no_action.yaml"
    no_action.write_text(
        "name: test_no_action\n"
        "tags: [negative]\n"
        "event:\n"
        "  domain: home\n"
        "  entity_id: sensor.temp\n"
        "  new_state: '22'\n"
        "  source: eval\n"
        "expected: null\n"
    )

    return tmp_path


def test_load_single_scenario(scenarios_dir: pathlib.Path) -> None:
    scenario = load_scenario(scenarios_dir / "home" / "test_valid.yaml")
    assert scenario.name == "test_valid"
    assert scenario.expected is not None
    assert scenario.expected.tool_name == "lighting.dim_lights"


def test_load_no_action_scenario(scenarios_dir: pathlib.Path) -> None:
    scenario = load_scenario(scenarios_dir / "home" / "test_no_action.yaml")
    assert scenario.expected is None


def test_load_all_scenarios(scenarios_dir: pathlib.Path) -> None:
    scenarios = load_scenarios(scenarios_dir)
    assert len(scenarios) == 2
    names = {s.name for s in scenarios}
    assert "test_valid" in names
    assert "test_no_action" in names


def test_load_scenarios_filter_by_tag(scenarios_dir: pathlib.Path) -> None:
    scenarios = load_scenarios(scenarios_dir, tags=["negative"])
    assert len(scenarios) == 1
    assert scenarios[0].name == "test_no_action"


def test_load_invalid_yaml_raises(tmp_path: pathlib.Path) -> None:
    bad_file = tmp_path / "bad.yaml"
    bad_file.write_text("name: bad\nevent: not_a_dict\n")
    with pytest.raises(ValueError, match="Invalid scenario"):
        load_scenario(bad_file)
