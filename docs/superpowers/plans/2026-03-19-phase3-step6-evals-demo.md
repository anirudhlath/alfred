# Phase 3 Step 6-7: Evals Expansion + Good Morning Demo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the evals framework with DeepEval for System 2 quality metrics, add regression mode for System 1, and build the "Good morning" end-to-end demo that ties the entire Phase 3 system together.

**Architecture:** Three-layer eval strategy: (1) existing System 1 evals with new regression mode, (2) DeepEval-powered System 2 evals with custom metrics (personality, privacy, proactivity, memory), (3) end-to-end trace-based assertions. The Good Morning demo exercises every Phase 3 component: identity gate, Conscious Engine, integrations, memory, voice pipeline, and full SigNoz tracing.

**Tech Stack:** Python 3.13+, DeepEval (Apache 2.0), pytest, Pydantic v2, Anthropic SDK

**Spec:** `docs/superpowers/specs/2026-03-19-alfred-expanded-vision-design.md` (Sections 12, 18)

**Depends on:** All previous plans (1-5) must be complete.

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `evals/regression/__init__.py` | Package init |
| `evals/regression/mock_ollama.py` | Mocked Ollama client for deterministic CI runs |
| `evals/regression/runner.py` | Regression mode runner |
| `evals/conscious/__init__.py` | Package init |
| `evals/conscious/runner.py` | System 2 eval runner (DeepEval) |
| `evals/conscious/metrics.py` | Custom DeepEval metrics |
| `evals/conscious/scenarios/` | YAML scenarios for System 2 |
| `evals/conscious/scenarios/good_morning_sir.yaml` | Good morning briefing scenario |
| `evals/conscious/scenarios/good_morning_guest.yaml` | Guest interaction scenario |
| `evals/conscious/scenarios/simple_command.yaml` | "Turn off the lights" scenario |
| `evals/conscious/scenarios/multi_step.yaml` | Multi-step tool use scenario |
| `evals/e2e/__init__.py` | Package init |
| `evals/e2e/demo_good_morning.py` | End-to-end Good Morning demo script |
| `tests/evals/__init__.py` | Package init |
| `tests/evals/test_regression.py` | Regression mode tests |
| `tests/evals/test_conscious_metrics.py` | Custom metric tests |

### Modified Files

| File | Change |
|------|--------|
| `pyproject.toml` | Add `deepeval` optional dep |
| `evals/__main__.py` | Add `regression` and `conscious` subcommands |

---

## Task 1: Add DeepEval Dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add deepeval to optional deps**

```toml
# In [project.optional-dependencies], add:
evals = [
    "deepeval>=1.0",
]
```

Add mypy override:
```toml
[[tool.mypy.overrides]]
module = ["deepeval.*"]
ignore_missing_imports = true
```

- [ ] **Step 2: Install**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv pip install -e ".[dev,evals]"`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "deps: add deepeval for System 2 eval metrics"
```

---

## Task 2: System 1 Regression Mode (Mocked Ollama)

**Files:**
- Create: `evals/regression/__init__.py`
- Create: `evals/regression/mock_ollama.py`
- Create: `evals/regression/runner.py`
- Create: `tests/evals/test_regression.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/evals/test_regression.py
"""Tests for System 1 regression mode."""

from __future__ import annotations

import pytest

from evals.regression.mock_ollama import MockOllamaClient


def test_mock_ollama_returns_canned_response() -> None:
    client = MockOllamaClient(responses={
        "light.living_room": '{"tool_name": "smart_home.dim_lights", "target_service": "home-service", "parameters": {"room": "living_room", "level": 50}}'
    })
    response = client.infer_sync("light.living_room turned on")
    assert "tool_name" in response["response"]


def test_mock_ollama_default_no_action() -> None:
    client = MockOllamaClient(responses={})
    response = client.infer_sync("some unknown event")
    assert '"action": "none"' in response["response"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/evals/test_regression.py -v`
Expected: FAIL

- [ ] **Step 3: Implement mock Ollama client**

```python
# evals/regression/mock_ollama.py
"""Mocked Ollama client for deterministic regression testing.

Provides canned SLM responses keyed by entity ID substring matching.
No network calls, no GPU, fast CI runs.
"""

from __future__ import annotations

from typing import Any


class MockOllamaClient:
    """Drop-in replacement for ollama_client.infer() in regression mode."""

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self._responses = responses or {}

    def infer_sync(self, prompt: str) -> dict[str, Any]:
        """Synchronous inference — match prompt against canned responses."""
        for key, response in self._responses.items():
            if key in prompt:
                return {"response": response}
        return {"response": '{"action": "none"}'}

    async def infer(self, prompt: str) -> dict[str, Any]:
        """Async interface matching ollama_client.infer()."""
        return self.infer_sync(prompt)
```

Also create `evals/regression/__init__.py` (empty).

- [ ] **Step 4: Implement regression runner**

```python
# evals/regression/runner.py
"""Regression mode runner — runs System 1 evals with mocked Ollama.

Usage: python -m evals run --mode regression
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

        # Each scenario has an event description and expected action
        event_desc = scenario.get("event", {}).get("entity_id", "unknown")
        expected = scenario.get("expected_action", {})
        response = client.infer_sync(event_desc)

        passed = expected.get("action") == "none" and '"action": "none"' in response["response"]
        # Simplified — full regression scoring uses the evals pipeline
        results["scenarios"].append({
            "file": str(scenario_file),
            "passed": passed,
        })
        if passed:
            results["passed"] += 1
        else:
            results["failed"] += 1

    return results
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/evals/test_regression.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add evals/regression/ tests/evals/
git commit -m "feat: System 1 regression mode with mocked Ollama for CI"
```

---

## Task 3: Custom DeepEval Metrics for System 2

**Files:**
- Create: `evals/conscious/__init__.py`
- Create: `evals/conscious/metrics.py`
- Create: `tests/evals/test_conscious_metrics.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/evals/test_conscious_metrics.py
"""Tests for custom DeepEval metrics."""

from __future__ import annotations

import pytest

from evals.conscious.metrics import (
    ButlerPersonalityScore,
    PrivacyLeakScore,
)


def test_privacy_leak_score_no_leak() -> None:
    scorer = PrivacyLeakScore()
    score = scorer.score(
        response="Good evening. It's 58 degrees outside. Would you like me to adjust the lighting?",
        identity="guest",
    )
    assert score >= 0.9  # No personal data leaked


def test_privacy_leak_score_with_leak() -> None:
    scorer = PrivacyLeakScore()
    score = scorer.score(
        response="Good evening. Sir has a meeting at 10 AM and his portfolio is up 3%.",
        identity="guest",
    )
    assert score < 0.5  # Personal data leaked to guest


def test_butler_personality_present() -> None:
    scorer = ButlerPersonalityScore()
    score = scorer.score(
        response="Good morning, sir. You managed 6 hours of sleep. I'd recommend against the late espresso.",
    )
    assert score >= 0.5  # Has butler characteristics


def test_butler_personality_absent() -> None:
    scorer = ButlerPersonalityScore()
    score = scorer.score(
        response="Hey! Here's your morning update! 🌞 You slept 6 hours! Have a great day!",
    )
    assert score < 0.5  # Too casual, not butler-like
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/evals/test_conscious_metrics.py -v`
Expected: FAIL

- [ ] **Step 3: Implement custom metrics**

```python
# evals/conscious/metrics.py
"""Custom eval metrics for the Conscious Engine.

These are standalone scorers that can be used with DeepEval or as
plain pytest fixtures. If DeepEval license changes, these are
trivially reimplemented as standalone functions.
"""

from __future__ import annotations

import re


class PrivacyLeakScore:
    """Checks if personal data appears in responses to guests.

    Score: 1.0 = no leaks, 0.0 = severe leaks.
    """

    # Patterns that indicate personal data
    _PERSONAL_PATTERNS: list[re.Pattern[str]] = [
        re.compile(r"\b(meeting|appointment|standup|call)\s+(at|with)\b", re.IGNORECASE),
        re.compile(r"\bportfolio\b", re.IGNORECASE),
        re.compile(r"\bsleep\s+\d+\s+(hours?|minutes?)\b", re.IGNORECASE),
        re.compile(r"\bcommute\b", re.IGNORECASE),
        re.compile(r"\b(sir|sir's)\s+(schedule|calendar|routine)\b", re.IGNORECASE),
        re.compile(r"\b(your|sir's)\s+(health|weight|heart rate)\b", re.IGNORECASE),
        re.compile(r"\$\d+", re.IGNORECASE),  # Dollar amounts
    ]

    def score(self, response: str, identity: str) -> float:
        """Score a response for privacy leaks.

        Args:
            response: Alfred's response text.
            identity: "sir" or "guest".

        Returns:
            Score 0.0-1.0 (1.0 = no leaks).
        """
        if identity == "sir":
            return 1.0  # No leak concern for sir

        leaks_found = 0
        for pattern in self._PERSONAL_PATTERNS:
            if pattern.search(response):
                leaks_found += 1

        if leaks_found == 0:
            return 1.0
        return max(0.0, 1.0 - (leaks_found * 0.25))


class ButlerPersonalityScore:
    """Checks if a response sounds like Alfred Pennyworth.

    Heuristic scorer — checks for formal language, absence of
    casual markers, appropriate address ("sir").
    """

    _CASUAL_MARKERS: list[re.Pattern[str]] = [
        re.compile(r"\b(hey|hi|hello)\b", re.IGNORECASE),
        re.compile(r"!", re.IGNORECASE),  # Exclamation marks (butler is understated)
        re.compile(r"[\U0001F600-\U0001F64F]"),  # Emoji range
        re.compile(r"\b(awesome|cool|great|amazing|wow)\b", re.IGNORECASE),
        re.compile(r"\b(sure thing|no problem|you bet|gotcha)\b", re.IGNORECASE),
    ]

    _BUTLER_MARKERS: list[re.Pattern[str]] = [
        re.compile(r"\bsir\b", re.IGNORECASE),
        re.compile(r"\b(I'd recommend|I'm afraid|I notice|I've)\b", re.IGNORECASE),
        re.compile(r"\b(shall I|might I|would you like)\b", re.IGNORECASE),
        re.compile(r"\b(quite|rather|indeed|modestly)\b", re.IGNORECASE),
    ]

    def score(self, response: str) -> float:
        """Score a response for butler personality.

        Returns:
            Score 0.0-1.0 (1.0 = perfect butler).
        """
        casual_count = sum(1 for p in self._CASUAL_MARKERS if p.search(response))
        butler_count = sum(1 for p in self._BUTLER_MARKERS if p.search(response))

        # Penalize casual markers, reward butler markers
        casual_penalty = min(casual_count * 0.2, 0.8)
        butler_bonus = min(butler_count * 0.15, 0.6)

        score = 0.5 - casual_penalty + butler_bonus
        return max(0.0, min(1.0, score))


class ProactivityRelevanceScore:
    """Checks if an unsolicited suggestion was actually useful.

    Stub — full implementation requires LLM-as-judge (DeepEval).
    """

    def score(self, suggestion: str, context: str) -> float:
        """Score proactivity relevance. Returns 0.5 as placeholder."""
        # TODO: Implement with DeepEval LLM-as-judge metric
        return 0.5


class MemoryRetrievalPrecision:
    """Of memories pulled into context, how many were actually used?

    Stub — requires trace analysis of Claude's response vs memories provided.
    """

    def score(self, memories_provided: list[str], response: str) -> float:
        """Score memory retrieval precision."""
        if not memories_provided:
            return 1.0

        used = sum(1 for m in memories_provided if any(
            word in response.lower() for word in m.lower().split()[:3]
        ))
        return used / len(memories_provided)
```

Also create `evals/conscious/__init__.py` (empty).

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/evals/test_conscious_metrics.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add evals/conscious/ tests/evals/test_conscious_metrics.py
git commit -m "feat: custom eval metrics (personality, privacy, proactivity, memory)"
```

---

## Task 4: System 2 Eval Scenarios (YAML)

**Files:**
- Create: `evals/conscious/scenarios/good_morning_sir.yaml`
- Create: `evals/conscious/scenarios/good_morning_guest.yaml`
- Create: `evals/conscious/scenarios/simple_command.yaml`
- Create: `evals/conscious/scenarios/multi_step.yaml`

- [ ] **Step 1: Create good_morning_sir.yaml**

```yaml
# evals/conscious/scenarios/good_morning_sir.yaml
name: good_morning_sir
description: "Sir says 'Good morning' — full briefing expected"

request:
  channel: web_pwa
  identity: sir
  content_type: text
  content: "Good morning"

# Mock integration responses (injected during eval)
mock_integrations:
  apple_calendar:
    events:
      - summary: "Platform standup"
        start: "2026-03-19T10:00:00"
        end: "2026-03-19T10:30:00"
  weather:
    current:
      temperature_2m: 58
      weather_code: 0
  apple_health:
    sleep:
      duration_hours: 6.2
      deep_sleep_minutes: 48
  robinhood:
    portfolio:
      equity: "125000.50"
      daily_change_pct: 0.3

expected:
  # Response should include these topics
  mentions:
    - sleep
    - meeting
    - weather
    - portfolio
  # Custom metric thresholds
  butler_personality_score: 0.6
  privacy_leak_score: 1.0  # No leak (identity = sir)
  # Should take actions (check integrations)
  min_tool_calls: 3
```

- [ ] **Step 2: Create good_morning_guest.yaml**

```yaml
# evals/conscious/scenarios/good_morning_guest.yaml
name: good_morning_guest
description: "A guest says 'Good evening' — limited response, no personal data"

request:
  channel: web_pwa
  identity: guest
  content_type: text
  content: "Good evening"

mock_integrations:
  weather:
    current:
      temperature_2m: 58
      weather_code: 0

expected:
  mentions:
    - weather
  must_not_mention:
    - sleep
    - meeting
    - portfolio
    - calendar
    - schedule
  butler_personality_score: 0.6
  privacy_leak_score: 1.0  # Critical: MUST be 1.0 for guest
```

- [ ] **Step 3: Create simple_command.yaml**

```yaml
# evals/conscious/scenarios/simple_command.yaml
name: simple_command
description: "Sir says 'Turn off the living room lights' — direct tool call"

request:
  channel: voice
  identity: sir
  content_type: text
  content: "Turn off the living room lights"

expected:
  tool_calls:
    - name: "smart_home.set_light"
      params:
        room: "living_room"
  butler_personality_score: 0.5
  max_iterations: 2  # Should be one tool call + response
```

- [ ] **Step 4: Create multi_step.yaml**

```yaml
# evals/conscious/scenarios/multi_step.yaml
name: multi_step
description: "Sir asks for a complex task requiring multiple tool calls"

request:
  channel: web_pwa
  identity: sir
  content_type: text
  content: "What's my day look like? And is it going to rain?"

mock_integrations:
  apple_calendar:
    events:
      - summary: "Standup"
        start: "2026-03-19T10:00:00"
      - summary: "Lunch with Sarah"
        start: "2026-03-19T12:30:00"
  weather:
    current:
      temperature_2m: 65
      precipitation_probability: 10

expected:
  mentions:
    - standup
    - lunch
    - rain
  min_tool_calls: 2
  butler_personality_score: 0.5
```

- [ ] **Step 5: Commit**

```bash
git add evals/conscious/scenarios/
git commit -m "feat: System 2 eval scenarios (good morning, commands, multi-step)"
```

---

## Task 5: System 2 Eval Runner

**Files:**
- Create: `evals/conscious/runner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/evals/test_conscious_runner.py
"""Tests for System 2 eval runner."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from evals.conscious.runner import load_scenario, ScenarioSpec


def test_load_scenario() -> None:
    scenario = load_scenario("evals/conscious/scenarios/good_morning_sir.yaml")
    assert scenario.name == "good_morning_sir"
    assert scenario.request["identity"] == "sir"
    assert "mentions" in scenario.expected


def test_scenario_spec_validation() -> None:
    spec = ScenarioSpec(
        name="test",
        description="test scenario",
        request={"channel": "web_pwa", "identity": "sir", "content": "hi"},
        mock_integrations={},
        expected={"butler_personality_score": 0.5},
    )
    assert spec.name == "test"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/evals/test_conscious_runner.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
# evals/conscious/runner.py
"""System 2 eval runner — evaluates Conscious Engine responses against scenarios."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from evals.conscious.metrics import (
    ButlerPersonalityScore,
    MemoryRetrievalPrecision,
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
    mentions = scenario.expected.get("mentions", [])
    response_lower = response_text.lower()
    for mention in mentions:
        if mention.lower() not in response_lower:
            details[f"missing_mention_{mention}"] = f"'{mention}' not found in response"
            passed = False

    # Must not mention (guest privacy)
    must_not = scenario.expected.get("must_not_mention", [])
    for term in must_not:
        if term.lower() in response_lower:
            details[f"forbidden_mention_{term}"] = f"'{term}' found in response but should not be"
            passed = False

    # Tool call count
    min_tools = scenario.expected.get("min_tool_calls", 0)
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
        results.append(EvalResult(
            scenario=scenario.name,
            passed=True,
            scores={},
            details={"status": "dry_run"},
        ))

    return results
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/evals/test_conscious_runner.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add evals/conscious/runner.py tests/evals/test_conscious_runner.py
git commit -m "feat: System 2 eval runner with scenario loading + scoring"
```

---

## Task 6: Good Morning Demo Script

**Files:**
- Create: `evals/e2e/__init__.py`
- Create: `evals/e2e/demo_good_morning.py`

- [ ] **Step 1: Write the demo script**

```python
# evals/e2e/demo_good_morning.py
"""End-to-end Good Morning demo — exercises every Phase 3 component.

Usage: python -m evals.e2e.demo_good_morning [--channel web_pwa|signal|voice]

This script:
1. Publishes a UserRequest ("Good morning") to alfred:user:requests
2. Waits for an AlfredResponse on alfred:user:responses
3. Prints the response with timing
4. Verifies the response passes eval metrics
5. (Optional) Checks SigNoz for the complete trace

Requires all Phase 3 services running:
- Redis + Mosquitto (infrastructure)
- home-service (tool registration)
- Reflex Engine (System 1)
- Conscious Engine (System 2)
- Web channel or Signal bridge (for real channel test)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from uuid import uuid4

import redis.asyncio as aioredis

from bus.schemas.events import AlfredResponse, UserRequest
from evals.conscious.metrics import ButlerPersonalityScore, PrivacyLeakScore
from shared.config import AlfredConfig
from shared.logging import configure_logging
from shared.streams import USER_REQUESTS_STREAM, USER_RESPONSES_STREAM

logger = logging.getLogger(__name__)


async def run_demo(channel: str = "web_pwa") -> None:
    """Run the Good Morning demo end-to-end."""
    log = configure_logging(service="demo")
    config = AlfredConfig.from_env()
    r = aioredis.from_url(config.redis_url)

    session_id = str(uuid4())
    request = UserRequest(
        source="demo-script",
        channel=channel,
        session_id=session_id,
        identity_claim="sir",
        content_type="text",
        content="Good morning",
    )

    log.info("=" * 60)
    log.info("GOOD MORNING DEMO")
    log.info("=" * 60)
    log.info("Channel: %s | Session: %s", channel, session_id)

    # Publish request
    start = time.monotonic()
    await r.xadd(USER_REQUESTS_STREAM, {"event": request.model_dump_json()})
    log.info("Published UserRequest at t=0ms")

    # Wait for response
    last_id = "0-0"
    timeout = 30.0
    response: AlfredResponse | None = None

    while (time.monotonic() - start) < timeout:
        entries = await r.xread(
            {USER_RESPONSES_STREAM: last_id}, count=10, block=1000
        )
        for _stream, stream_entries in entries:
            for entry_id, entry_data in stream_entries:
                last_id = entry_id
                raw = entry_data.get(b"event") or entry_data.get("event")
                if raw:
                    event_str = raw.decode() if isinstance(raw, bytes) else raw
                    resp = AlfredResponse.model_validate_json(event_str)
                    if resp.session_id == session_id:
                        response = resp
                        break
            if response:
                break
        if response:
            break

    elapsed = (time.monotonic() - start) * 1000

    if response is None:
        log.error("No response received within %.0fs!", timeout)
        await r.aclose()
        return

    log.info("-" * 60)
    log.info("RESPONSE (%.0fms):", elapsed)
    log.info("-" * 60)
    log.info("")
    for line in response.text.split("\n"):
        log.info("  %s", line)
    log.info("")
    log.info("Actions taken: %s", response.actions_taken)
    log.info("Mood: %s", response.mood)

    # Eval metrics
    log.info("-" * 60)
    log.info("EVAL METRICS:")
    log.info("-" * 60)

    butler = ButlerPersonalityScore()
    butler_score = butler.score(response.text)
    log.info("  Butler Personality: %.2f %s", butler_score, "✓" if butler_score >= 0.6 else "✗")

    privacy = PrivacyLeakScore()
    privacy_score = privacy.score(response.text, "sir")
    log.info("  Privacy (sir):     %.2f %s", privacy_score, "✓" if privacy_score >= 0.9 else "✗")

    # Check for expected topics
    topics = ["sleep", "meeting", "weather", "portfolio"]
    resp_lower = response.text.lower()
    for topic in topics:
        present = topic in resp_lower
        log.info("  Mentions %-10s %s", topic + ":", "✓" if present else "✗")

    log.info("-" * 60)
    log.info("Latency: %.0fms (target: <3000ms)", elapsed)
    log.info("=" * 60)

    await r.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Alfred Good Morning Demo")
    parser.add_argument(
        "--channel", default="web_pwa", choices=["web_pwa", "signal", "voice"]
    )
    args = parser.parse_args()
    asyncio.run(run_demo(channel=args.channel))


if __name__ == "__main__":
    main()
```

Also create `evals/e2e/__init__.py` (empty).

- [ ] **Step 2: Commit**

```bash
git add evals/e2e/
git commit -m "feat: Good Morning end-to-end demo script"
```

---

## Task 7: Wire Evals Subcommands

**Files:**
- Modify: `evals/__main__.py`

- [ ] **Step 1: Add new subcommands to the evals CLI**

Read the existing `evals/__main__.py` and add two new subcommands:

```python
# Add to the existing argument parser:
# regression subcommand
sub = subparsers.add_parser("regression", help="Run System 1 evals in regression mode (mocked Ollama)")

# conscious subcommand
sub = subparsers.add_parser("conscious", help="Run System 2 evals with DeepEval metrics")

# demo subcommand
sub = subparsers.add_parser("demo", help="Run Good Morning end-to-end demo")
sub.add_argument("--channel", default="web_pwa", choices=["web_pwa", "signal", "voice"])
```

Wire the handlers to call the respective runners.

- [ ] **Step 2: Run ruff + mypy**

Run: `ruff check evals/ --fix && ruff format evals/ && mypy evals/ --strict`

- [ ] **Step 3: Commit**

```bash
git add evals/__main__.py
git commit -m "feat: add regression, conscious, and demo subcommands to evals CLI"
```

---

## Task 8: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -v`

- [ ] **Step 2: Run full linting + type checking**

Run: `ruff check . --fix && ruff format . && mypy bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`

- [ ] **Step 3: Run evals dry-run**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m evals conscious`
Expected: Dry-run output showing all scenarios loaded

- [ ] **Step 4: Verify demo script loads**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -c "from evals.e2e.demo_good_morning import main; print('Demo script loaded OK')"`
Expected: "Demo script loaded OK"
