"""Regression mode runner — runs System 1 evals with mocked Ollama.

Usage: python -m evals regression
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from evals.regression.mock_ollama import MockOllamaClient

logger = logging.getLogger(__name__)


def load_canned_responses(responses_file: str = "evals/regression/responses.yaml") -> dict[str, str]:
    """Load canned responses from YAML file."""
    path = Path(responses_file)
    if not path.exists():
        logger.warning("No canned responses file at %s", responses_file)
        return {}
    data: dict[str, str] = yaml.safe_load(path.read_text()) or {}
    return data


def run_regression(
    scenarios_dir: str = "evals/scenarios",
    responses_file: str = "evals/regression/responses.yaml",
) -> dict[str, Any]:
    """Run all scenarios in regression mode with mocked Ollama."""
    responses = load_canned_responses(responses_file)
    client = MockOllamaClient(responses=responses)

    scenarios_path = Path(scenarios_dir)
    results: dict[str, Any] = {"passed": 0, "failed": 0, "scenarios": []}

    for scenario_file in sorted(scenarios_path.rglob("*.yaml")):
        scenario = yaml.safe_load(scenario_file.read_text())
        if scenario is None:
            continue

        event_desc = scenario.get("event", {}).get("entity_id", "unknown")
        expected = scenario.get("expected_action", {})
        response = client.infer_sync(event_desc)

        passed = expected.get("action") == "none" and '"action": "none"' in response["response"]
        results["scenarios"].append({
            "file": str(scenario_file),
            "passed": passed,
        })
        if passed:
            results["passed"] += 1
        else:
            results["failed"] += 1

    return results
