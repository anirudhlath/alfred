"""YAML scenario discovery and Pydantic validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from pathlib import Path
from pydantic import ValidationError

from evals.models import Scenario


def load_scenario(path: Path) -> Scenario:
    """Load and validate a single YAML scenario file."""
    with open(path) as f:
        raw = yaml.safe_load(f)

    try:
        return Scenario.model_validate(raw)
    except (ValidationError, TypeError) as e:
        msg = f"Invalid scenario {path}: {e}"
        raise ValueError(msg) from e


def load_scenarios(
    directory: Path,
    tags: list[str] | None = None,
) -> list[Scenario]:
    """Discover all .yaml files recursively, validate, and optionally filter by tags."""
    scenarios: list[Scenario] = []
    for yaml_path in sorted(directory.rglob("*.yaml")):
        scenario = load_scenario(yaml_path)
        if tags and not any(tag in scenario.tags for tag in tags):
            continue
        scenarios.append(scenario)
    return scenarios
