"""System 2 eval runner — evaluates Conscious Engine responses against scenarios."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from evals.conscious.metrics import (
    ButlerPersonalityScore,
    PrivacyLeakScore,
)

logger = logging.getLogger(__name__)


class ScenarioSpec(BaseModel):
    """Schema for a System 2 eval scenario."""

    name: str
    description: str
    request: dict[str, Any]
    mock_integrations: dict[str, Any] = {}
    expected: dict[str, Any] = {}


class EvalResult(BaseModel):
    """Result of evaluating one scenario."""

    scenario: str
    passed: bool
    scores: dict[str, float] = {}
    details: dict[str, Any] = {}


def load_scenario(path: str) -> ScenarioSpec:
    """Load a scenario from a YAML file."""
    data = yaml.safe_load(Path(path).read_text())
    return ScenarioSpec.model_validate(data)


def evaluate_response(
    scenario: ScenarioSpec,
    response_text: str,
    tool_calls_made: list[str],
) -> EvalResult:
    """Evaluate a Conscious Engine response against a scenario's expectations."""
    scores: dict[str, float] = {}
    details: dict[str, Any] = {}
    passed = True

    # Butler personality
    butler_scorer = ButlerPersonalityScore()
    butler_score = butler_scorer.score(response_text)
    scores["butler_personality"] = butler_score
    threshold = scenario.expected.get("butler_personality_score", 0.5)
    if butler_score < threshold:
        details["butler_personality"] = f"Score {butler_score:.2f} below threshold {threshold}"
        passed = False

    # Privacy leak
    identity = scenario.request.get("identity", "sir")
    privacy_scorer = PrivacyLeakScore()
    privacy_score = privacy_scorer.score(response_text, identity)
    scores["privacy_leak"] = privacy_score
    privacy_threshold = scenario.expected.get("privacy_leak_score", 0.9)
    if privacy_score < privacy_threshold:
        details["privacy_leak"] = f"Score {privacy_score:.2f} below threshold {privacy_threshold}"
        passed = False

    # Required mentions
    mentions: list[str] = scenario.expected.get("mentions", [])
    response_lower = response_text.lower()
    for mention in mentions:
        if mention.lower() not in response_lower:
            details[f"missing_mention_{mention}"] = f"'{mention}' not found in response"
            passed = False

    # Must not mention (guest privacy)
    must_not: list[str] = scenario.expected.get("must_not_mention", [])
    for term in must_not:
        if term.lower() in response_lower:
            details[f"forbidden_mention_{term}"] = f"'{term}' found in response but should not be"
            passed = False

    # Tool call count
    min_tools: int = scenario.expected.get("min_tool_calls", 0)
    if len(tool_calls_made) < min_tools:
        details["tool_calls"] = f"Made {len(tool_calls_made)} tool calls, expected >= {min_tools}"
        passed = False

    return EvalResult(
        scenario=scenario.name,
        passed=passed,
        scores=scores,
        details=details,
    )


def run_conscious_evals(
    scenarios_dir: str = "evals/conscious/scenarios",
) -> list[EvalResult]:
    """Run all System 2 eval scenarios.

    Note: Full implementation requires a running Conscious Engine
    or a mocked engine. This function provides the scoring framework.
    """
    results: list[EvalResult] = []
    scenarios_path = Path(scenarios_dir)

    for scenario_file in sorted(scenarios_path.glob("*.yaml")):
        scenario = load_scenario(str(scenario_file))
        logger.info("Evaluating scenario: %s", scenario.name)

        # TODO: Call Conscious Engine with mocked integrations
        # For now, this is a dry-run that validates scenario loading
        results.append(
            EvalResult(
                scenario=scenario.name,
                passed=True,
                scores={},
                details={"status": "dry_run"},
            )
        )

    return results


async def run_conscious_evals_live(
    scenarios_dir: str = "evals/conscious/scenarios",
    api_key: str = "",
    model: str = "openrouter/anthropic/claude-sonnet-4",
) -> list[EvalResult]:
    """Run System 2 evals with a real (or mocked) Conscious Engine.

    Requires OPENROUTER_API_KEY for live execution.
    Falls back to dry-run if no key provided.
    """
    if not api_key:
        logger.warning("No API key — falling back to dry-run mode")
        return run_conscious_evals(scenarios_dir)

    from unittest.mock import AsyncMock

    from bus.schemas.events import UserRequest
    from core.conscious.context_assembler import ContextAssembler
    from core.conscious.cost import CostTracker
    from core.conscious.engine import ConsciousEngine
    from core.conscious.identity import IdentityGate
    from core.conscious.session import SessionManager

    results: list[EvalResult] = []
    scenarios_path = Path(scenarios_dir)

    mock_redis: Any = AsyncMock()
    mock_redis.xinfo_stream = AsyncMock(return_value={"last-generated-id": "0-0"})

    engine = ConsciousEngine(
        redis=mock_redis,
        identity_gate=IdentityGate(registered_phone=""),
        session_mgr=SessionManager(redis=mock_redis, timeout_minutes=30),
        cost_tracker=CostTracker(redis=mock_redis, daily_cap_usd=50.0),
        context_assembler=ContextAssembler(),
        domain_router=AsyncMock(),
        tool_registry=AsyncMock(get_tools=AsyncMock(return_value=[])),
        context_reader=AsyncMock(get_rendered_context=AsyncMock(return_value="")),
        claude_model=model,
        claude_api_key=api_key,
    )

    for scenario_file in sorted(scenarios_path.glob("*.yaml")):
        scenario = load_scenario(str(scenario_file))
        logger.info("Evaluating scenario: %s", scenario.name)

        request = UserRequest(
            source="eval",
            channel="web_pwa",
            session_id=f"eval-{scenario.name}",
            identity_claim=scenario.request.get("identity", "sir"),
            authenticated=scenario.request.get("identity", "sir") == "sir",
            content_type="text",
            content=scenario.request.get("content", ""),
        )

        try:
            response = await engine.process_request(request)
            eval_result = evaluate_response(
                scenario,
                response_text=response.text,
                tool_calls_made=response.actions_taken,
            )
        except Exception as exc:
            logger.error("Scenario %s failed: %s", scenario.name, exc)
            eval_result = EvalResult(
                scenario=scenario.name,
                passed=False,
                scores={},
                details={"error": str(exc)},
            )

        results.append(eval_result)

    return results
