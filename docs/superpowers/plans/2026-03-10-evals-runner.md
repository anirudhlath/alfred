# Evals Runner Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a CLI-driven scenario evaluation framework that tests the Reflex Engine's SLM output against predefined YAML scenarios, captures full inference traces, and supports run-over-run comparison.

**Architecture:** The eval pipeline reuses the Reflex Engine's real `build_prompt()` and `parse_response()` methods (extracted as public APIs) but controls execution flow to capture `TraceRecord` at each step. Scenarios are YAML files validated by Pydantic. Results are scored as pass/partial/fail and stored as JSON for comparison.

**Tech Stack:** Python 3.13, Pydantic v2, PyYAML (already in deps), argparse, httpx (via existing `ollama_client`)

**Spec:** `docs/superpowers/specs/2026-03-10-evals-runner-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `shared/tracing.py` | `TraceRecord` Pydantic model — reusable across evals, engine, SigNoz |
| `core/reflex/engine.py` | **Modify** — extract `build_prompt()` and `parse_response()` as public methods |
| `evals/__init__.py` | Package marker |
| `evals/models.py` | `Scenario`, `ExpectedAction`, `Verdict`, `ScenarioResult`, `EvalRun` |
| `evals/loader.py` | YAML discovery + Pydantic validation |
| `evals/pipeline.py` | Orchestrate: build_prompt → infer → parse_response → TraceRecord |
| `evals/scorer.py` | Compare trace output to expected → Verdict + reason |
| `evals/store.py` | Save/load `EvalRun` as JSON to `evals/runs/` |
| `evals/compare.py` | Diff two runs → verdict changes, latency deltas |
| `evals/report.py` | Terminal output formatting |
| `evals/__main__.py` | CLI entry point: `python -m evals` |
| `evals/scenarios/home/tv_on_dims_lights.yaml` | Canonical scenario: TV on → dim lights |
| `evals/scenarios/home/bedtime_lights_off.yaml` | Bedtime → all lights off |
| `evals/scenarios/home/irrelevant_temperature.yaml` | Negative: no action expected |
| `tests/evals/test_models.py` | Unit tests for models + scenario validation |
| `tests/evals/test_scorer.py` | Unit tests for scoring logic |
| `tests/evals/test_loader.py` | Unit tests for YAML loading |
| `tests/evals/test_pipeline.py` | Integration test for pipeline (mocked Ollama) |
| `tests/evals/test_store.py` | Unit tests for store save/load round-trip |
| `tests/evals/test_compare.py` | Unit tests for run comparison |

---

## Chunk 1: Foundation — TraceRecord + Engine Refactor

### Task 1: TraceRecord Model

**Files:**
- Create: `shared/tracing.py`
- Test: `tests/shared/test_tracing.py`

- [ ] **Step 1: Write test for TraceRecord construction and serialization**

```python
# tests/shared/test_tracing.py
"""Tests for shared.tracing.TraceRecord."""

from __future__ import annotations

from datetime import UTC, datetime

from bus.schemas.events import ActionRequest, StateChangedEvent
from shared.tracing import TraceRecord


def test_trace_record_construction() -> None:
    """TraceRecord can be built from all required fields."""
    event = StateChangedEvent(
        source="eval",
        domain="home",
        entity_id="light.living_room",
        new_state="on",
    )
    trace = TraceRecord(
        trace_id="test-001",
        timestamp=datetime.now(UTC),
        model="llama3:8b",
        event=event,
        preferences_text="- dim lights when TV on",
        tools=[{"name": "dim_lights", "target_service": "home-service"}],
        prompt="You are Alfred...",
        raw_response='{"action": "none"}',
        parsed_action=None,
        latency_ms=123.4,
        prompt_tokens=100,
        completion_tokens=10,
    )
    assert trace.trace_id == "test-001"
    assert trace.parsed_action is None
    assert trace.latency_ms == 123.4


def test_trace_record_with_action() -> None:
    """TraceRecord stores parsed ActionRequest."""
    event = StateChangedEvent(
        source="eval",
        domain="home",
        entity_id="media_player.tv",
        new_state="on",
    )
    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="lighting.dim_lights",
        parameters={"room": "living_room"},
    )
    trace = TraceRecord(
        trace_id="test-002",
        timestamp=datetime.now(UTC),
        model="gpt-oss:20b",
        event=event,
        preferences_text="- dim lights when TV on",
        tools=[],
        prompt="prompt text",
        raw_response='{"tool_name": "lighting.dim_lights"}',
        parsed_action=action,
        latency_ms=456.7,
        prompt_tokens=200,
        completion_tokens=30,
    )
    assert trace.parsed_action is not None
    assert trace.parsed_action.tool_name == "lighting.dim_lights"


def test_trace_record_json_round_trip() -> None:
    """TraceRecord serializes to JSON and back."""
    event = StateChangedEvent(
        source="eval",
        domain="home",
        entity_id="light.living_room",
        new_state="on",
    )
    trace = TraceRecord(
        trace_id="test-003",
        timestamp=datetime.now(UTC),
        model="llama3:8b",
        event=event,
        preferences_text="prefs",
        tools=[{"name": "t1"}],
        prompt="prompt",
        raw_response="{}",
        parsed_action=None,
        latency_ms=100.0,
        prompt_tokens=50,
        completion_tokens=5,
    )
    json_str = trace.model_dump_json()
    restored = TraceRecord.model_validate_json(json_str)
    assert restored.trace_id == trace.trace_id
    assert restored.model == trace.model
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/shared/test_tracing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.tracing'`

- [ ] **Step 3: Implement TraceRecord**

```python
# shared/tracing.py
"""TraceRecord — structured inference trace for evals, debugging, and observability.

Reusable across the evals runner, Reflex Engine debug mode, and future
SigNoz/OpenTelemetry export.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from bus.schemas.events import ActionRequest, StateChangedEvent


class TraceRecord(BaseModel):
    """Complete trace of a single SLM inference call."""

    trace_id: str
    timestamp: datetime
    model: str

    # Inputs
    event: StateChangedEvent
    preferences_text: str
    tools: list[dict[str, Any]]

    # Prompt
    prompt: str

    # Output
    raw_response: str
    parsed_action: ActionRequest | None

    # Metrics
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/shared/test_tracing.py -v`
Expected: 3 passed

- [ ] **Step 5: Run ruff + mypy**

Run: `uv run ruff check shared/tracing.py tests/shared/test_tracing.py && uv run ruff format shared/tracing.py tests/shared/test_tracing.py && uv run mypy shared/tracing.py`

- [ ] **Step 6: Commit**

```bash
git add shared/tracing.py tests/shared/__init__.py tests/shared/test_tracing.py
git commit -m "feat(shared): add TraceRecord model for inference tracing"
```

---

### Task 2: Refactor ReflexEngine — Extract Public Methods

**Files:**
- Modify: `core/reflex/engine.py` (lines 83–161)
- Test: `tests/integration/test_reflex_end_to_end.py` (existing — must still pass)
- Test: `tests/core/reflex/test_engine_public_api.py` (new)

The goal is to extract `build_prompt()` and `parse_response()` as public methods while keeping `process_event()` behavior identical.

- [ ] **Step 1: Verify existing tests pass before refactoring**

Run: `uv run pytest tests/integration/test_reflex_end_to_end.py -v`
Expected: 2 passed

- [ ] **Step 2: Write tests for the new public API**

```python
# tests/core/reflex/test_engine_public_api.py
"""Tests for ReflexEngine public API: build_prompt, parse_response."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from bus.schemas.events import ActionRequest, StateChangedEvent
from core.reflex.engine import ReflexEngine
from core.reflex.tool_registry import ToolInfo


def _make_tools() -> list[ToolInfo]:
    return [
        ToolInfo(
            name="lighting.dim_lights",
            description="Dim the lights in a room.",
            parameters={
                "room": {"type": "str", "description": "The room to dim."},
                "level": {"type": "int", "description": "Brightness level 0-100."},
            },
            feature_name="lighting",
            feature_description="Smart home lighting controls.",
            target_service="home-service",
        ),
    ]


def _make_event() -> StateChangedEvent:
    return StateChangedEvent(
        source="eval",
        domain="home",
        entity_id="media_player.living_room_tv",
        old_state="off",
        new_state="on",
        attributes={"friendly_name": "Living Room TV"},
    )


class TestBuildPrompt:
    def test_returns_string_with_event_details(self) -> None:
        """build_prompt includes event entity_id and state change."""
        registry = AsyncMock()
        engine = ReflexEngine(preferences_dir="/tmp/fake", tool_registry=registry)
        tools = _make_tools()

        prompt = engine.build_prompt(
            event=_make_event(),
            preferences_text="- dim lights when TV on",
            tools=tools,
        )

        assert isinstance(prompt, str)
        assert "media_player.living_room_tv" in prompt
        assert "off" in prompt
        assert "on" in prompt
        assert "dim lights when TV on" in prompt

    def test_includes_tool_names(self) -> None:
        """build_prompt includes discovered tool names."""
        registry = AsyncMock()
        engine = ReflexEngine(preferences_dir="/tmp/fake", tool_registry=registry)
        tools = _make_tools()

        prompt = engine.build_prompt(
            event=_make_event(),
            preferences_text="prefs",
            tools=tools,
        )

        assert "lighting.dim_lights" in prompt
        assert "home-service" in prompt

    def test_empty_tools(self) -> None:
        """build_prompt works with no tools."""
        registry = AsyncMock()
        engine = ReflexEngine(preferences_dir="/tmp/fake", tool_registry=registry)

        prompt = engine.build_prompt(
            event=_make_event(),
            preferences_text="prefs",
            tools=[],
        )

        assert "No tools available" in prompt


class TestParseResponse:
    def test_valid_action(self) -> None:
        """parse_response returns ActionRequest for valid tool response."""
        registry = AsyncMock()
        engine = ReflexEngine(preferences_dir="/tmp/fake", tool_registry=registry)
        event = _make_event()

        response: dict[str, object] = {
            "response": json.dumps({
                "tool_name": "lighting.dim_lights",
                "target_service": "home-service",
                "parameters": {"room": "living_room", "level": 20},
            }),
        }

        result = engine.parse_response(response, event, {"home-service"})

        assert result is not None
        assert isinstance(result, ActionRequest)
        assert result.tool_name == "lighting.dim_lights"
        assert result.target_service == "home-service"

    def test_no_action(self) -> None:
        """parse_response returns None for action=none."""
        registry = AsyncMock()
        engine = ReflexEngine(preferences_dir="/tmp/fake", tool_registry=registry)
        event = _make_event()

        response: dict[str, object] = {
            "response": json.dumps({"action": "none"}),
        }

        result = engine.parse_response(response, event, {"home-service"})
        assert result is None

    def test_invalid_service_returns_none(self) -> None:
        """parse_response rejects responses with unregistered target_service."""
        registry = AsyncMock()
        engine = ReflexEngine(preferences_dir="/tmp/fake", tool_registry=registry)
        event = _make_event()

        response: dict[str, object] = {
            "response": json.dumps({
                "tool_name": "lighting.dim_lights",
                "target_service": "fake-service",
                "parameters": {},
            }),
        }

        result = engine.parse_response(response, event, {"home-service"})
        assert result is None

    def test_malformed_json_returns_none(self) -> None:
        """parse_response returns None for unparseable response."""
        registry = AsyncMock()
        engine = ReflexEngine(preferences_dir="/tmp/fake", tool_registry=registry)
        event = _make_event()

        response: dict[str, object] = {"response": "not json at all"}

        result = engine.parse_response(response, event, {"home-service"})
        assert result is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/core/reflex/test_engine_public_api.py -v`
Expected: FAIL — `AttributeError: 'ReflexEngine' object has no attribute 'build_prompt'`

- [ ] **Step 4: Refactor engine.py — extract public methods**

In `core/reflex/engine.py`, make these changes:

1. Rename `_build_system_prompt` → keep as-is (private helper for system prompt only)
2. Add new public `build_prompt()` method that combines system prompt + preferences + event (extracting lines 110–119 from `process_event`)
3. Rename `_parse_response` → `parse_response` (make public, same signature)
4. Update `process_event()` to call the new public methods

The refactored `ReflexEngine` class should look like:

```python
class ReflexEngine:
    """The System 1 fast-path inference engine."""

    TOOL_CACHE_TTL = 300.0

    def __init__(self, preferences_dir: str, tool_registry: ToolRegistry) -> None:
        self.preferences_dir = preferences_dir
        self._registry = tool_registry
        self._cached_preferences: str | None = None
        self._cached_tools: list[ToolInfo] | None = None
        self._cached_system_prompt: str | None = None
        self._cache_time: float = 0.0

    def _get_preferences(self) -> str:
        if self._cached_preferences is None:
            self._cached_preferences = read_preferences(self.preferences_dir)
        return self._cached_preferences

    def _build_system_prompt(self, tools: list[ToolInfo]) -> str:
        tool_section = _build_tool_section(tools)
        return _SYSTEM_PROMPT_TEMPLATE.format(tool_section=tool_section)

    async def _get_tools_and_prompt(self) -> tuple[list[ToolInfo], str]:
        now = time.monotonic()
        if self._cached_tools is None or (now - self._cache_time) > self.TOOL_CACHE_TTL:
            self._cached_tools = await self._registry.get_tools()
            self._cached_system_prompt = self._build_system_prompt(self._cached_tools)
            self._cache_time = now
        assert self._cached_system_prompt is not None
        return self._cached_tools, self._cached_system_prompt

    async def reload_tools(self) -> None:
        self._cached_tools = None
        self._cached_system_prompt = None

    def build_prompt(
        self,
        event: StateChangedEvent,
        preferences_text: str,
        tools: list[ToolInfo],
    ) -> str:
        """Build the complete prompt for SLM inference.

        Public API for the evals pipeline. Returns the same prompt that
        process_event() sends to Ollama.
        """
        system_prompt = self._build_system_prompt(tools)
        return (
            f"{system_prompt}\n\n"
            f"## User Preferences\n{preferences_text}\n\n"
            f"## Event\n"
            f"Entity: {event.entity_id}\n"
            f"Domain: {event.domain}\n"
            f"Changed: {event.old_state} → {event.new_state}\n"
            f"Attributes: {json.dumps(event.attributes)}\n\n"
            f"## Your Decision (JSON only):"
        )

    def parse_response(
        self,
        response: dict[str, object],
        event: StateChangedEvent,
        valid_services: set[str],
    ) -> ActionRequest | None:
        """Parse the SLM's JSON response into an ActionRequest or None.

        Public API for the evals pipeline. Same logic as used by process_event().
        """
        try:
            raw = response.get("response", "")
            parsed = json.loads(str(raw))

            if parsed.get("action") == "none":
                logger.debug("No action for event %s", event.entity_id)
                return None

            tool_name = parsed.get("tool_name")
            if not tool_name:
                logger.warning("SLM response missing tool_name: %s", raw)
                return None

            target_service = str(parsed.get("target_service", ""))
            if target_service not in valid_services:
                logger.warning(
                    "SLM returned unregistered target_service: %s (valid: %s)",
                    target_service,
                    valid_services,
                )
                return None

            return ActionRequest(
                source="reflex-engine",
                target_service=target_service,
                tool_name=str(tool_name),
                parameters=dict(parsed.get("parameters", {})),
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Failed to parse SLM response: %s — %s", e, response)
            return None

    @track_latency(category="reflex")
    async def process_event(self, event: StateChangedEvent) -> ActionRequest | None:
        """Process a state change event and optionally produce an action."""
        preferences = self._get_preferences()
        tools, system_prompt = await self._get_tools_and_prompt()
        valid_services = ToolRegistry.get_registered_services(tools)

        # Use cached system_prompt directly (hot path — avoid rebuild)
        prompt = (
            f"{system_prompt}\n\n"
            f"## User Preferences\n{preferences}\n\n"
            f"## Event\n"
            f"Entity: {event.entity_id}\n"
            f"Domain: {event.domain}\n"
            f"Changed: {event.old_state} → {event.new_state}\n"
            f"Attributes: {json.dumps(event.attributes)}\n\n"
            f"## Your Decision (JSON only):"
        )

        response = await ollama_client.infer(prompt)
        return self.parse_response(response, event, valid_services)
```

- [ ] **Step 5: Run all tests to verify no regressions**

Run: `uv run pytest tests/integration/test_reflex_end_to_end.py tests/core/reflex/test_engine_public_api.py -v`
Expected: All pass (2 existing + 7 new)

- [ ] **Step 6: Run ruff + mypy**

Run: `uv run ruff check core/reflex/engine.py && uv run ruff format core/reflex/engine.py && uv run mypy core/reflex/engine.py`

- [ ] **Step 7: Commit**

```bash
git add core/reflex/engine.py tests/core/reflex/test_engine_public_api.py
git commit -m "refactor(reflex): extract build_prompt and parse_response as public API"
```

---

## Chunk 2: Evals Models + Scorer + Loader

### Task 3: Evals Data Models

**Files:**
- Create: `evals/__init__.py`
- Create: `evals/models.py`
- Test: `tests/evals/__init__.py`
- Test: `tests/evals/test_models.py`

- [ ] **Step 1: Write tests for models**

```python
# tests/evals/test_models.py
"""Tests for evals.models."""

from __future__ import annotations

from datetime import UTC, datetime

from bus.schemas.events import StateChangedEvent
from evals.models import EvalRun, ExpectedAction, Scenario, ScenarioResult, Verdict
from shared.tracing import TraceRecord


def _make_event() -> StateChangedEvent:
    return StateChangedEvent(
        source="eval",
        domain="home",
        entity_id="media_player.tv",
        new_state="on",
    )


def test_scenario_with_expected_action() -> None:
    scenario = Scenario(
        name="test_scenario",
        event=_make_event(),
        expected=ExpectedAction(
            tool_name="lighting.dim_lights",
            parameters={"room": "living_room"},
        ),
    )
    assert scenario.expected is not None
    assert scenario.expected.tool_name == "lighting.dim_lights"
    assert scenario.tags == []


def test_scenario_no_action_expected() -> None:
    scenario = Scenario(
        name="negative_test",
        tags=["negative"],
        event=_make_event(),
        expected=None,
    )
    assert scenario.expected is None


def test_scenario_with_tags() -> None:
    scenario = Scenario(
        name="tagged",
        tags=["home", "lighting"],
        event=_make_event(),
        expected=None,
    )
    assert "home" in scenario.tags
    assert "lighting" in scenario.tags


def test_verdict_enum_values() -> None:
    assert Verdict.PASS == "pass"
    assert Verdict.PARTIAL == "partial"
    assert Verdict.FAIL == "fail"


def test_eval_run_summary() -> None:
    trace = TraceRecord(
        trace_id="t1",
        timestamp=datetime.now(UTC),
        model="test",
        event=_make_event(),
        preferences_text="",
        tools=[],
        prompt="",
        raw_response="{}",
        parsed_action=None,
        latency_ms=100.0,
        prompt_tokens=10,
        completion_tokens=5,
    )
    result = ScenarioResult(
        scenario=Scenario(name="s1", event=_make_event(), expected=None),
        verdict=Verdict.PASS,
        reason="correctly took no action",
        trace=trace,
    )
    run = EvalRun(
        run_id="run-001",
        timestamp=datetime.now(UTC),
        model="test",
        scenario_count=1,
        results=[result],
        summary={"pass": 1, "partial": 0, "fail": 0},
    )
    assert run.summary["pass"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evals'`

- [ ] **Step 3: Implement models**

```python
# evals/__init__.py
"""Alfred Evals Runner — scenario-based evaluation of Reflex Engine inference."""

# evals/models.py
"""Data models for the evals runner."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from bus.schemas.events import StateChangedEvent
from shared.tracing import TraceRecord


class ExpectedAction(BaseModel):
    """What the SLM should produce for a scenario."""

    tool_name: str
    target_service: str | None = None
    parameters: dict[str, Any] | None = None


class Scenario(BaseModel):
    """A single eval scenario loaded from YAML."""

    name: str
    description: str | None = None
    tags: list[str] = []
    event: StateChangedEvent
    preferences_dir: str | None = None
    expected: ExpectedAction | None


class Verdict(StrEnum):
    """Outcome of scoring a scenario."""

    PASS = "pass"
    PARTIAL = "partial"
    FAIL = "fail"


class ScenarioResult(BaseModel):
    """Result of running and scoring a single scenario."""

    scenario: Scenario
    verdict: Verdict
    reason: str
    trace: TraceRecord


class EvalRun(BaseModel):
    """A complete eval run with all scenario results."""

    run_id: str
    timestamp: datetime
    model: str
    scenario_count: int
    results: list[ScenarioResult]
    summary: dict[str, int]
```

- [ ] **Step 4: Add `evals` to setuptools packages**

In `pyproject.toml`, add `"evals*"` to the `include` list:

```toml
[tool.setuptools.packages.find]
include = ["bus*", "core*", "domains*", "evals*", "runner*", "sdk*", "shared*", "telemetry*", "tests*"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/evals/test_models.py -v`
Expected: 5 passed

- [ ] **Step 6: Run ruff + mypy**

Run: `uv run ruff check evals/ tests/evals/ && uv run ruff format evals/ tests/evals/ && uv run mypy evals/models.py`

- [ ] **Step 7: Commit**

```bash
git add evals/__init__.py evals/models.py tests/evals/__init__.py tests/evals/test_models.py pyproject.toml
git commit -m "feat(evals): add data models — Scenario, Verdict, EvalRun"
```

---

### Task 4: Scorer

**Files:**
- Create: `evals/scorer.py`
- Test: `tests/evals/test_scorer.py`

- [ ] **Step 1: Write tests for scoring logic**

```python
# tests/evals/test_scorer.py
"""Tests for evals.scorer — the pass/partial/fail verdict engine."""

from __future__ import annotations

from datetime import UTC, datetime

from bus.schemas.events import ActionRequest, StateChangedEvent
from evals.models import ExpectedAction, Scenario, Verdict
from evals.scorer import score
from shared.tracing import TraceRecord


def _make_trace(
    parsed_action: ActionRequest | None = None,
    raw_response: str = "{}",
) -> TraceRecord:
    return TraceRecord(
        trace_id="t1",
        timestamp=datetime.now(UTC),
        model="test",
        event=StateChangedEvent(
            source="eval", domain="home", entity_id="light.lr", new_state="on"
        ),
        preferences_text="",
        tools=[],
        prompt="",
        raw_response=raw_response,
        parsed_action=parsed_action,
        latency_ms=100.0,
        prompt_tokens=10,
        completion_tokens=5,
    )


def _make_action(
    tool_name: str = "lighting.dim_lights",
    target_service: str = "home-service",
    **params: object,
) -> ActionRequest:
    return ActionRequest(
        source="reflex-engine",
        target_service=target_service,
        tool_name=tool_name,
        parameters=dict(params),
    )


def _make_scenario(
    expected: ExpectedAction | None = None,
) -> Scenario:
    return Scenario(
        name="test",
        event=StateChangedEvent(
            source="eval", domain="home", entity_id="light.lr", new_state="on"
        ),
        expected=expected,
    )


class TestNoActionExpected:
    def test_pass_when_no_action_returned(self) -> None:
        trace = _make_trace(parsed_action=None)
        result = score(trace, _make_scenario(expected=None))
        assert result.verdict == Verdict.PASS
        assert "no action" in result.reason

    def test_fail_when_action_returned(self) -> None:
        trace = _make_trace(parsed_action=_make_action())
        result = score(trace, _make_scenario(expected=None))
        assert result.verdict == Verdict.FAIL
        assert "lighting.dim_lights" in result.reason


class TestActionExpected:
    def test_fail_when_no_action_returned(self) -> None:
        expected = ExpectedAction(tool_name="lighting.dim_lights")
        trace = _make_trace(parsed_action=None)
        result = score(trace, _make_scenario(expected=expected))
        assert result.verdict == Verdict.FAIL
        assert "got no action" in result.reason

    def test_pass_exact_match(self) -> None:
        expected = ExpectedAction(
            tool_name="lighting.dim_lights",
            parameters={"room": "living_room"},
        )
        trace = _make_trace(
            parsed_action=_make_action(room="living_room", level=20),
        )
        result = score(trace, _make_scenario(expected=expected))
        assert result.verdict == Verdict.PASS

    def test_pass_no_params_specified(self) -> None:
        """When expected has no params, any params from SLM are accepted."""
        expected = ExpectedAction(tool_name="lighting.dim_lights")
        trace = _make_trace(parsed_action=_make_action(room="lr", level=20))
        result = score(trace, _make_scenario(expected=expected))
        assert result.verdict == Verdict.PASS

    def test_fail_wrong_tool(self) -> None:
        expected = ExpectedAction(tool_name="lighting.dim_lights")
        trace = _make_trace(parsed_action=_make_action(tool_name="media.play"))
        result = score(trace, _make_scenario(expected=expected))
        assert result.verdict == Verdict.FAIL
        assert "expected lighting.dim_lights" in result.reason

    def test_partial_wrong_params(self) -> None:
        expected = ExpectedAction(
            tool_name="lighting.dim_lights",
            parameters={"room": "living_room"},
        )
        trace = _make_trace(
            parsed_action=_make_action(room="bedroom"),
        )
        result = score(trace, _make_scenario(expected=expected))
        assert result.verdict == Verdict.PARTIAL
        assert "room" in result.reason

    def test_partial_missing_params(self) -> None:
        expected = ExpectedAction(
            tool_name="lighting.dim_lights",
            parameters={"room": "living_room", "level": 20},
        )
        trace = _make_trace(
            parsed_action=_make_action(room="living_room"),
        )
        result = score(trace, _make_scenario(expected=expected))
        assert result.verdict == Verdict.PARTIAL
        assert "level" in result.reason

    def test_type_coercion_string_to_int(self) -> None:
        """SLMs sometimes return '40' instead of 40."""
        expected = ExpectedAction(
            tool_name="lighting.dim_lights",
            parameters={"level": 40},
        )
        trace = _make_trace(
            parsed_action=_make_action(level="40"),
        )
        result = score(trace, _make_scenario(expected=expected))
        assert result.verdict == Verdict.PASS

    def test_target_service_match(self) -> None:
        expected = ExpectedAction(
            tool_name="lighting.dim_lights",
            target_service="home-service",
        )
        trace = _make_trace(
            parsed_action=_make_action(target_service="home-service"),
        )
        result = score(trace, _make_scenario(expected=expected))
        assert result.verdict == Verdict.PASS

    def test_target_service_mismatch(self) -> None:
        expected = ExpectedAction(
            tool_name="lighting.dim_lights",
            target_service="home-service",
        )
        trace = _make_trace(
            parsed_action=_make_action(target_service="wrong-service"),
        )
        result = score(trace, _make_scenario(expected=expected))
        assert result.verdict == Verdict.FAIL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/test_scorer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evals.scorer'`

- [ ] **Step 3: Implement scorer**

```python
# evals/scorer.py
"""Scoring logic — compare TraceRecord output to expected scenario outcome."""

from __future__ import annotations

from evals.models import ExpectedAction, Scenario, ScenarioResult, Verdict
from shared.tracing import TraceRecord


def _coerce_equal(actual: object, expected: object) -> bool:
    """Type-coerced comparison. Handles SLM returning '40' for 40."""
    if actual == expected:
        return True
    try:
        return str(actual) == str(expected)
    except (TypeError, ValueError):
        return False


def _check_parameters(
    actual_params: dict[str, object],
    expected_params: dict[str, object],
) -> tuple[bool, list[str]]:
    """Check expected params against actual. Returns (all_match, mismatch_details)."""
    mismatches: list[str] = []
    for key, expected_val in expected_params.items():
        if key not in actual_params:
            mismatches.append(f"missing {key}")
        elif not _coerce_equal(actual_params[key], expected_val):
            mismatches.append(
                f"{key}: expected {expected_val!r}, got {actual_params[key]!r}"
            )
    return len(mismatches) == 0, mismatches


def score(trace: TraceRecord, scenario: Scenario) -> ScenarioResult:
    """Score a trace against a scenario's expected outcome."""
    expected = scenario.expected
    actual = trace.parsed_action

    if expected is None:
        if actual is None:
            verdict = Verdict.PASS
            reason = "correctly took no action"
        else:
            verdict = Verdict.FAIL
            reason = f"expected no action, got {actual.tool_name}"
    elif actual is None:
        verdict = Verdict.FAIL
        reason = f"expected {expected.tool_name}, got no action"
    elif actual.tool_name != expected.tool_name:
        verdict = Verdict.FAIL
        reason = f"expected {expected.tool_name}, got {actual.tool_name}"
    elif expected.target_service and actual.target_service != expected.target_service:
        verdict = Verdict.FAIL
        reason = (
            f"expected service {expected.target_service}, "
            f"got {actual.target_service}"
        )
    elif expected.parameters:
        all_match, mismatches = _check_parameters(
            actual.parameters, expected.parameters
        )
        if all_match:
            verdict = Verdict.PASS
            reason = "exact match"
        else:
            verdict = Verdict.PARTIAL
            reason = f"correct tool, wrong params: {', '.join(mismatches)}"
    else:
        verdict = Verdict.PASS
        reason = "exact match"

    return ScenarioResult(
        scenario=scenario,
        verdict=verdict,
        reason=reason,
        trace=trace,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evals/test_scorer.py -v`
Expected: 11 passed

- [ ] **Step 5: Run ruff + mypy**

Run: `uv run ruff check evals/scorer.py tests/evals/test_scorer.py && uv run ruff format evals/scorer.py tests/evals/test_scorer.py && uv run mypy evals/scorer.py`

- [ ] **Step 6: Commit**

```bash
git add evals/scorer.py tests/evals/test_scorer.py
git commit -m "feat(evals): add scorer — pass/partial/fail verdict logic"
```

---

### Task 5: YAML Scenario Loader

**Files:**
- Create: `evals/loader.py`
- Create: `evals/scenarios/home/tv_on_dims_lights.yaml`
- Create: `evals/scenarios/home/bedtime_lights_off.yaml`
- Create: `evals/scenarios/home/irrelevant_temperature.yaml`
- Test: `tests/evals/test_loader.py`

- [ ] **Step 1: Write tests for the loader**

```python
# tests/evals/test_loader.py
"""Tests for evals.loader — YAML scenario discovery and validation."""

from __future__ import annotations

import pathlib

import pytest

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/test_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evals.loader'`

- [ ] **Step 3: Implement loader**

```python
# evals/loader.py
"""YAML scenario discovery and Pydantic validation."""

from __future__ import annotations

from pathlib import Path

import yaml
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evals/test_loader.py -v`
Expected: 5 passed

- [ ] **Step 5: Create the seed scenario files**

Create `evals/scenarios/home/tv_on_dims_lights.yaml`:
```yaml
name: tv_on_dims_lights
description: "When TV turns on, dim living room lights"
tags: [home, lighting, canonical]
event:
  domain: home
  entity_id: media_player.living_room_tv
  old_state: "off"
  new_state: "on"
  source: eval
  attributes:
    friendly_name: "Living Room TV"
expected:
  tool_name: lighting.dim_lights
  parameters:
    room: living_room
```

Create `evals/scenarios/home/bedtime_lights_off.yaml`:
```yaml
name: bedtime_lights_off
description: "When user goes to bed, all lights should turn off"
tags: [home, lighting]
event:
  domain: home
  entity_id: binary_sensor.bed_occupancy
  old_state: "off"
  new_state: "on"
  source: eval
  attributes:
    friendly_name: "Bed Occupancy"
expected:
  tool_name: lighting.turn_off_lights
```

Create `evals/scenarios/home/irrelevant_temperature.yaml`:
```yaml
name: irrelevant_temperature
description: "Temperature sensor change should not trigger any action"
tags: [home, negative]
event:
  domain: home
  entity_id: sensor.outdoor_temperature
  old_state: "72"
  new_state: "73"
  source: eval
  attributes:
    friendly_name: "Outdoor Temperature"
    unit_of_measurement: "°F"
expected: null
```

- [ ] **Step 6: Run ruff + mypy**

Run: `uv run ruff check evals/loader.py tests/evals/test_loader.py && uv run ruff format evals/loader.py tests/evals/test_loader.py && uv run mypy evals/loader.py`

- [ ] **Step 7: Commit**

```bash
git add evals/loader.py evals/scenarios/ tests/evals/test_loader.py
git commit -m "feat(evals): add YAML scenario loader with seed scenarios"
```

---

## Chunk 3: Pipeline + Store + Compare

### Task 6: Eval Pipeline

**Files:**
- Create: `evals/pipeline.py`
- Test: `tests/evals/test_pipeline.py`

The pipeline orchestrates: load preferences → build prompt → infer → parse response → build TraceRecord. It calls the engine's real public methods and measures wall-clock latency around the Ollama call.

- [ ] **Step 1: Write tests for pipeline (mocked Ollama)**

```python
# tests/evals/test_pipeline.py
"""Tests for evals.pipeline — inference orchestration with trace capture."""

from __future__ import annotations

import json
import pathlib
from unittest.mock import AsyncMock, patch

import pytest

from bus.schemas.events import StateChangedEvent
from core.reflex.tool_registry import ToolInfo
from evals.models import Scenario
from evals.pipeline import run_scenario


def _make_tools() -> list[ToolInfo]:
    return [
        ToolInfo(
            name="lighting.dim_lights",
            description="Dim the lights.",
            parameters={"room": {"type": "str"}, "level": {"type": "int"}},
            feature_name="lighting",
            feature_description="Lighting controls.",
            target_service="home-service",
        ),
    ]


def _make_scenario() -> Scenario:
    return Scenario(
        name="test_pipeline",
        event=StateChangedEvent(
            source="eval",
            domain="home",
            entity_id="media_player.tv",
            old_state="off",
            new_state="on",
            attributes={"friendly_name": "TV"},
        ),
        expected=None,
    )


@pytest.mark.asyncio
async def test_run_scenario_captures_trace(tmp_path: pathlib.Path) -> None:
    """Pipeline captures full trace from prompt through response."""
    prefs_dir = str(tmp_path)
    # No preferences files — empty prefs is fine for this test

    ollama_response = {
        "response": json.dumps({"action": "none"}),
        "prompt_tokens": 100,
        "completion_tokens": 8,
        "total_tokens": 108,
    }

    with patch(
        "core.reflex.ollama_client.infer",
        new_callable=AsyncMock,
        return_value=ollama_response,
    ):
        trace = await run_scenario(
            scenario=_make_scenario(),
            tools=_make_tools(),
            preferences_dir=prefs_dir,
            model="test-model",
        )

    assert trace.model == "test-model"
    assert "media_player.tv" in trace.prompt
    assert trace.raw_response == json.dumps({"action": "none"})
    assert trace.parsed_action is None
    assert trace.prompt_tokens == 100
    assert trace.completion_tokens == 8
    assert trace.latency_ms >= 0


@pytest.mark.asyncio
async def test_run_scenario_with_action(tmp_path: pathlib.Path) -> None:
    """Pipeline captures parsed ActionRequest."""
    prefs_dir = str(tmp_path)

    ollama_response = {
        "response": json.dumps({
            "tool_name": "lighting.dim_lights",
            "target_service": "home-service",
            "parameters": {"room": "living_room", "level": 20},
        }),
        "prompt_tokens": 200,
        "completion_tokens": 30,
        "total_tokens": 230,
    }

    with patch(
        "core.reflex.ollama_client.infer",
        new_callable=AsyncMock,
        return_value=ollama_response,
    ):
        trace = await run_scenario(
            scenario=_make_scenario(),
            tools=_make_tools(),
            preferences_dir=prefs_dir,
        )

    assert trace.parsed_action is not None
    assert trace.parsed_action.tool_name == "lighting.dim_lights"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evals.pipeline'`

- [ ] **Step 3: Implement pipeline**

```python
# evals/pipeline.py
"""Eval pipeline — orchestrates prompt building, inference, and trace capture."""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

from core.reflex import ollama_client
from core.reflex.engine import ReflexEngine
from core.reflex.memory_reader import read_preferences
from core.reflex.tool_registry import ToolInfo, ToolRegistry
from evals.models import Scenario
from shared.config import AlfredConfig
from shared.tracing import TraceRecord

_config = AlfredConfig.from_env()


async def run_scenario(
    scenario: Scenario,
    tools: list[ToolInfo],
    preferences_dir: str,
    model: str | None = None,
) -> TraceRecord:
    """Run a single scenario through the inference pipeline, capturing a full trace."""
    model = model or _config.ollama_model

    # Use a throwaway engine instance for prompt building / response parsing
    # (no Redis needed — we pass tools directly)
    engine = ReflexEngine(
        preferences_dir=preferences_dir,
        tool_registry=ToolRegistry(redis=None),  # type: ignore[arg-type]
    )

    # Build prompt using the engine's real logic
    prefs_dir = scenario.preferences_dir or preferences_dir
    preferences_text = read_preferences(prefs_dir)
    prompt = engine.build_prompt(scenario.event, preferences_text, tools)

    # Call Ollama and measure latency
    start = time.perf_counter()
    response = await ollama_client.infer(prompt, model=model)
    latency_ms = (time.perf_counter() - start) * 1000

    # Parse using the engine's real logic
    valid_services = ToolRegistry.get_registered_services(tools)
    parsed_action = engine.parse_response(response, scenario.event, valid_services)

    return TraceRecord(
        trace_id=str(uuid4()),
        timestamp=datetime.now(UTC),
        model=model,
        event=scenario.event,
        preferences_text=preferences_text,
        tools=[asdict(t) for t in tools],
        prompt=prompt,
        raw_response=str(response.get("response", "")),
        parsed_action=parsed_action,
        latency_ms=latency_ms,
        prompt_tokens=int(response.get("prompt_tokens", 0)),
        completion_tokens=int(response.get("completion_tokens", 0)),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evals/test_pipeline.py -v`
Expected: 2 passed

- [ ] **Step 5: Run ruff + mypy**

Run: `uv run ruff check evals/pipeline.py tests/evals/test_pipeline.py && uv run ruff format evals/pipeline.py tests/evals/test_pipeline.py && uv run mypy evals/pipeline.py`

- [ ] **Step 6: Commit**

```bash
git add evals/pipeline.py tests/evals/test_pipeline.py
git commit -m "feat(evals): add inference pipeline with trace capture"
```

---

### Task 7: Run Store

**Files:**
- Create: `evals/store.py`
- Test: `tests/evals/test_store.py`

- [ ] **Step 1: Write tests for store**

```python
# tests/evals/test_store.py
"""Tests for evals.store — save/load EvalRun as JSON."""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime

from bus.schemas.events import StateChangedEvent
from evals.models import EvalRun, Scenario, ScenarioResult, Verdict
from evals.store import build_run_id, load_run, list_runs, save_run
from shared.tracing import TraceRecord


def _make_run() -> EvalRun:
    event = StateChangedEvent(
        source="eval", domain="home", entity_id="light.lr", new_state="on"
    )
    trace = TraceRecord(
        trace_id="t1",
        timestamp=datetime.now(UTC),
        model="test-model",
        event=event,
        preferences_text="prefs",
        tools=[],
        prompt="prompt",
        raw_response="{}",
        parsed_action=None,
        latency_ms=100.0,
        prompt_tokens=10,
        completion_tokens=5,
    )
    result = ScenarioResult(
        scenario=Scenario(name="s1", event=event, expected=None),
        verdict=Verdict.PASS,
        reason="correctly took no action",
        trace=trace,
    )
    return EvalRun(
        run_id="2026-03-10T143000_test-model",
        timestamp=datetime.now(UTC),
        model="test-model",
        scenario_count=1,
        results=[result],
        summary={"pass": 1, "partial": 0, "fail": 0},
    )


def test_save_and_load_round_trip(tmp_path: pathlib.Path) -> None:
    run = _make_run()
    save_run(run, tmp_path)
    loaded = load_run(run.run_id, tmp_path)
    assert loaded.run_id == run.run_id
    assert loaded.summary[Verdict.PASS] == 1
    assert len(loaded.results) == 1


def test_list_runs(tmp_path: pathlib.Path) -> None:
    run = _make_run()
    save_run(run, tmp_path)
    runs = list_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0] == run.run_id


def test_build_run_id() -> None:
    ts = datetime(2026, 3, 10, 14, 30, 0, tzinfo=UTC)
    run_id = build_run_id(ts, "gpt-oss:20b")
    assert run_id == "2026-03-10T143000_gpt-oss-20b"


def test_build_run_id_sanitizes_colons() -> None:
    ts = datetime(2026, 3, 10, 14, 30, 0, tzinfo=UTC)
    run_id = build_run_id(ts, "model:with:colons")
    assert ":" not in run_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evals.store'`

- [ ] **Step 3: Implement store**

```python
# evals/store.py
"""Save and load EvalRun results as JSON files."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from evals.models import EvalRun


def build_run_id(timestamp: datetime, model: str) -> str:
    """Build a filesystem-safe run ID from timestamp and model name."""
    ts_str = timestamp.strftime("%Y-%m-%dT%H%M%S")
    safe_model = model.replace(":", "-")
    return f"{ts_str}_{safe_model}"


def save_run(run: EvalRun, runs_dir: Path) -> Path:
    """Save an EvalRun as a JSON file. Returns the file path."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"{run.run_id}.json"
    path.write_text(run.model_dump_json(indent=2))
    return path


def load_run(run_id: str, runs_dir: Path) -> EvalRun:
    """Load an EvalRun from a JSON file by run_id."""
    path = runs_dir / f"{run_id}.json"
    return EvalRun.model_validate_json(path.read_text())


def list_runs(runs_dir: Path) -> list[str]:
    """List all run IDs in the runs directory, sorted by name."""
    if not runs_dir.exists():
        return []
    return sorted(p.stem for p in runs_dir.glob("*.json"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evals/test_store.py -v`
Expected: 4 passed

- [ ] **Step 5: Run ruff + mypy**

Run: `uv run ruff check evals/store.py tests/evals/test_store.py && uv run ruff format evals/store.py tests/evals/test_store.py && uv run mypy evals/store.py`

- [ ] **Step 6: Commit**

```bash
git add evals/store.py tests/evals/test_store.py
git commit -m "feat(evals): add run store — save/load EvalRun as JSON"
```

---

### Task 8: Run Comparison

**Files:**
- Create: `evals/compare.py`
- Test: `tests/evals/test_compare.py`

- [ ] **Step 1: Write tests for comparison**

```python
# tests/evals/test_compare.py
"""Tests for evals.compare — diff two EvalRuns."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bus.schemas.events import StateChangedEvent
from evals.compare import compare_runs, VerdictChange
from evals.models import EvalRun, Scenario, ScenarioResult, Verdict
from shared.tracing import TraceRecord


def _make_result(
    name: str,
    verdict: Verdict,
    latency_ms: float = 100.0,
) -> ScenarioResult:
    event = StateChangedEvent(
        source="eval", domain="home", entity_id="light.lr", new_state="on"
    )
    trace = TraceRecord(
        trace_id="t1",
        timestamp=datetime.now(UTC),
        model="test",
        event=event,
        preferences_text="",
        tools=[],
        prompt="",
        raw_response="{}",
        parsed_action=None,
        latency_ms=latency_ms,
        prompt_tokens=10,
        completion_tokens=5,
    )
    return ScenarioResult(
        scenario=Scenario(name=name, event=event, expected=None),
        verdict=verdict,
        reason="test",
        trace=trace,
    )


def _make_run(
    run_id: str,
    results: list[ScenarioResult],
) -> EvalRun:
    summary: dict[str, int] = {v.value: 0 for v in Verdict}
    for r in results:
        summary[r.verdict.value] += 1
    return EvalRun(
        run_id=run_id,
        timestamp=datetime.now(UTC),
        model="test",
        scenario_count=len(results),
        results=results,
        summary=summary,
    )


def test_unchanged_verdicts() -> None:
    old = _make_run("old", [_make_result("s1", Verdict.PASS)])
    new = _make_run("new", [_make_result("s1", Verdict.PASS)])
    diff = compare_runs(old, new)
    assert len(diff.comparisons) == 1
    assert diff.comparisons[0].change == VerdictChange.UNCHANGED


def test_improved_verdict() -> None:
    old = _make_run("old", [_make_result("s1", Verdict.FAIL)])
    new = _make_run("new", [_make_result("s1", Verdict.PASS)])
    diff = compare_runs(old, new)
    assert diff.comparisons[0].change == VerdictChange.IMPROVED
    assert diff.improved == 1


def test_regressed_verdict() -> None:
    old = _make_run("old", [_make_result("s1", Verdict.PASS)])
    new = _make_run("new", [_make_result("s1", Verdict.FAIL)])
    diff = compare_runs(old, new)
    assert diff.comparisons[0].change == VerdictChange.REGRESSED
    assert diff.regressed == 1


def test_latency_delta() -> None:
    old = _make_run("old", [_make_result("s1", Verdict.PASS, latency_ms=400.0)])
    new = _make_run("new", [_make_result("s1", Verdict.PASS, latency_ms=350.0)])
    diff = compare_runs(old, new)
    assert diff.comparisons[0].latency_delta_ms == pytest.approx(-50.0)


def test_new_scenario_flagged() -> None:
    old = _make_run("old", [_make_result("s1", Verdict.PASS)])
    new = _make_run("new", [
        _make_result("s1", Verdict.PASS),
        _make_result("s2", Verdict.FAIL),
    ])
    diff = compare_runs(old, new)
    assert "s2" in diff.added_scenarios


def test_removed_scenario_flagged() -> None:
    old = _make_run("old", [
        _make_result("s1", Verdict.PASS),
        _make_result("s2", Verdict.PASS),
    ])
    new = _make_run("new", [_make_result("s1", Verdict.PASS)])
    diff = compare_runs(old, new)
    assert "s2" in diff.removed_scenarios
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/test_compare.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evals.compare'`

- [ ] **Step 3: Implement compare**

```python
# evals/compare.py
"""Diff two EvalRuns — verdict changes, latency deltas, added/removed scenarios."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from evals.models import EvalRun, Verdict


class VerdictChange(StrEnum):
    IMPROVED = "improved"
    REGRESSED = "regressed"
    UNCHANGED = "unchanged"


_VERDICT_RANK = {Verdict.FAIL: 0, Verdict.PARTIAL: 1, Verdict.PASS: 2}


class ScenarioComparison(BaseModel):
    """Comparison of a single scenario across two runs."""

    name: str
    old_verdict: Verdict
    new_verdict: Verdict
    change: VerdictChange
    old_latency_ms: float
    new_latency_ms: float
    latency_delta_ms: float


class RunComparison(BaseModel):
    """Full comparison of two EvalRuns."""

    old_run_id: str
    new_run_id: str
    comparisons: list[ScenarioComparison]
    added_scenarios: list[str]
    removed_scenarios: list[str]
    improved: int
    regressed: int
    unchanged: int


def compare_runs(old: EvalRun, new: EvalRun) -> RunComparison:
    """Compare two runs and produce a structured diff."""
    old_by_name = {r.scenario.name: r for r in old.results}
    new_by_name = {r.scenario.name: r for r in new.results}

    shared = set(old_by_name) & set(new_by_name)
    added = sorted(set(new_by_name) - set(old_by_name))
    removed = sorted(set(old_by_name) - set(new_by_name))

    comparisons: list[ScenarioComparison] = []
    improved = 0
    regressed = 0
    unchanged = 0

    for name in sorted(shared):
        old_r = old_by_name[name]
        new_r = new_by_name[name]

        old_rank = _VERDICT_RANK[old_r.verdict]
        new_rank = _VERDICT_RANK[new_r.verdict]

        if new_rank > old_rank:
            change = VerdictChange.IMPROVED
            improved += 1
        elif new_rank < old_rank:
            change = VerdictChange.REGRESSED
            regressed += 1
        else:
            change = VerdictChange.UNCHANGED
            unchanged += 1

        comparisons.append(
            ScenarioComparison(
                name=name,
                old_verdict=old_r.verdict,
                new_verdict=new_r.verdict,
                change=change,
                old_latency_ms=old_r.trace.latency_ms,
                new_latency_ms=new_r.trace.latency_ms,
                latency_delta_ms=new_r.trace.latency_ms - old_r.trace.latency_ms,
            )
        )

    return RunComparison(
        old_run_id=old.run_id,
        new_run_id=new.run_id,
        comparisons=comparisons,
        added_scenarios=added,
        removed_scenarios=removed,
        improved=improved,
        regressed=regressed,
        unchanged=unchanged,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evals/test_compare.py -v`
Expected: 6 passed

- [ ] **Step 5: Run ruff + mypy**

Run: `uv run ruff check evals/compare.py tests/evals/test_compare.py && uv run ruff format evals/compare.py tests/evals/test_compare.py && uv run mypy evals/compare.py`

- [ ] **Step 6: Commit**

```bash
git add evals/compare.py tests/evals/test_compare.py
git commit -m "feat(evals): add run comparison — verdict changes and latency deltas"
```

---

## Chunk 4: Report + CLI + Finalization

### Task 9: Terminal Report

**Files:**
- Create: `evals/report.py`

No dedicated tests — this is pure formatting. Verified by the CLI integration test in Task 10.

- [ ] **Step 1: Implement report.py**

```python
# evals/report.py
"""Human-readable terminal output for eval runs and comparisons."""

from __future__ import annotations

from evals.compare import RunComparison, VerdictChange
from evals.models import EvalRun, Verdict


_VERDICT_SYMBOLS = {
    Verdict.PASS: "\u2713",    # ✓
    Verdict.PARTIAL: "~",
    Verdict.FAIL: "\u2717",    # ✗
}


def format_run(run: EvalRun) -> str:
    """Format an EvalRun as a human-readable report."""
    lines: list[str] = []
    lines.append(
        f"Eval Run: {run.timestamp.isoformat(timespec='seconds')}  "
        f"|  Model: {run.model}  |  {run.scenario_count} scenarios"
    )
    lines.append("")

    for result in run.results:
        sym = _VERDICT_SYMBOLS[result.verdict]
        name = result.scenario.name
        verdict_str = result.verdict.value.upper()
        latency = result.trace.latency_ms
        line = f"  {sym} {name} {'.' * max(1, 40 - len(name))} {verdict_str:>7}   ({latency:.0f}ms)"
        lines.append(line)
        if result.verdict != Verdict.PASS:
            lines.append(f"    -> {result.reason}")

    lines.append("")
    p = run.summary.get("pass", 0)
    pt = run.summary.get("partial", 0)
    f = run.summary.get("fail", 0)
    lines.append(f"Summary: {p} pass | {pt} partial | {f} fail")

    return "\n".join(lines)


def format_comparison(comp: RunComparison) -> str:
    """Format a RunComparison as a human-readable diff."""
    lines: list[str] = []
    lines.append(f"Comparing: {comp.old_run_id} -> {comp.new_run_id}")
    lines.append("")

    for c in comp.comparisons:
        old_v = c.old_verdict.value.upper()
        new_v = c.new_verdict.value.upper()
        name = c.name
        suffix = ""
        if c.change == VerdictChange.IMPROVED:
            suffix = "  improved"
        elif c.change == VerdictChange.REGRESSED:
            suffix = "  REGRESSED"
        line = (
            f"  {name} {'.' * max(1, 40 - len(name))} "
            f"{old_v} -> {new_v}   "
            f"({c.old_latency_ms:.0f}ms -> {c.new_latency_ms:.0f}ms)"
            f"{suffix}"
        )
        lines.append(line)

    if comp.added_scenarios:
        lines.append("")
        for name in comp.added_scenarios:
            lines.append(f"  + {name} (new scenario)")
    if comp.removed_scenarios:
        lines.append("")
        for name in comp.removed_scenarios:
            lines.append(f"  - {name} (removed scenario)")

    lines.append("")
    lines.append(
        f"Verdicts: +{comp.improved} improved | "
        f"{comp.regressed} regressed | "
        f"{comp.unchanged} unchanged"
    )

    # Avg latency
    if comp.comparisons:
        old_avg = sum(c.old_latency_ms for c in comp.comparisons) / len(comp.comparisons)
        new_avg = sum(c.new_latency_ms for c in comp.comparisons) / len(comp.comparisons)
        pct = ((new_avg - old_avg) / old_avg * 100) if old_avg > 0 else 0
        sign = "+" if pct > 0 else ""
        lines.append(f"Avg latency: {old_avg:.0f}ms -> {new_avg:.0f}ms ({sign}{pct:.0f}%)")

    return "\n".join(lines)
```

- [ ] **Step 2: Run ruff + mypy**

Run: `uv run ruff check evals/report.py && uv run ruff format evals/report.py && uv run mypy evals/report.py`

- [ ] **Step 3: Commit**

```bash
git add evals/report.py
git commit -m "feat(evals): add terminal report formatting"
```

---

### Task 10: CLI Entry Point

**Files:**
- Create: `evals/__main__.py`

- [ ] **Step 1: Implement CLI**

```python
# evals/__main__.py
"""CLI entry point: python -m evals."""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from datetime import UTC, datetime
from pathlib import Path

from core.reflex.memory_reader import read_preferences
from core.reflex.tool_registry import ToolInfo, ToolRegistry
from evals.compare import compare_runs
from evals.loader import load_scenario, load_scenarios
from evals.models import EvalRun, Scenario, Verdict
from evals.pipeline import run_scenario
from evals.report import format_comparison, format_run
from evals.scorer import score
from evals.store import build_run_id, list_runs, load_run, save_run
from shared.config import AlfredConfig

_SCENARIOS_DIR = Path(__file__).parent / "scenarios"
_RUNS_DIR = Path(__file__).parent / "runs"
_PREFERENCES_DIR = str(Path(__file__).parent.parent / "core" / "memory" / "preferences")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="evals",
        description="Alfred Evals Runner — scenario-based SLM evaluation",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    run_parser = sub.add_parser("run", help="Run eval scenarios against Ollama")
    run_parser.add_argument("--tag", action="append", dest="tags", help="Filter by tag")
    run_parser.add_argument("--scenario", type=Path, help="Run a single scenario file")
    run_parser.add_argument("--model", help="Override Ollama model")
    run_parser.add_argument(
        "--preferences-dir", type=str, default=_PREFERENCES_DIR, help="Preferences directory"
    )

    # list
    sub.add_parser("list", help="List available scenarios")

    # compare
    cmp_parser = sub.add_parser("compare", help="Compare two runs")
    cmp_parser.add_argument("run_id_1", help="First (older) run ID")
    cmp_parser.add_argument("run_id_2", help="Second (newer) run ID")

    # runs
    sub.add_parser("runs", help="List saved runs")

    return parser.parse_args()


async def _load_tools() -> list[ToolInfo]:
    """Load tools from Redis registry."""
    import redis.asyncio as aioredis

    config = AlfredConfig.from_env()
    r = aioredis.from_url(config.redis_url)
    try:
        registry = ToolRegistry(r)
        return await registry.get_tools()
    finally:
        await r.aclose()


async def _cmd_run(args: argparse.Namespace) -> None:
    """Execute scenarios and produce an eval run."""
    config = AlfredConfig.from_env()
    model = args.model or config.ollama_model

    # Load scenarios
    if args.scenario:
        scenarios = [load_scenario(args.scenario)]
    else:
        scenarios = load_scenarios(_SCENARIOS_DIR, tags=args.tags)

    if not scenarios:
        print("No scenarios found.")
        sys.exit(1)

    # Load tools from Redis
    tools = await _load_tools()
    if not tools:
        print("No tools registered in Redis. Is home-service running?")
        sys.exit(1)

    print(f"Running {len(scenarios)} scenarios with model {model}...\n")

    # Run each scenario
    results = []
    for scenario in scenarios:
        trace = await run_scenario(
            scenario=scenario,
            tools=tools,
            preferences_dir=args.preferences_dir,
            model=model,
        )
        result = score(trace, scenario)
        results.append(result)

    # Build summary
    summary: dict[str, int] = {v.value: 0 for v in Verdict}
    for r in results:
        summary[r.verdict.value] += 1

    timestamp = datetime.now(UTC)
    run = EvalRun(
        run_id=build_run_id(timestamp, model),
        timestamp=timestamp,
        model=model,
        scenario_count=len(results),
        results=results,
        summary=summary,
    )

    # Save and report
    path = save_run(run, _RUNS_DIR)
    print(format_run(run))
    print(f"Run saved: {path}")

    # Append to research CSV
    _append_research_csv(run)


def _append_research_csv(run: EvalRun) -> None:
    """Append summary stats to research/data/evals.csv."""
    config = AlfredConfig.from_env()
    csv_path = Path(config.research_vault_path) / "data" / "evals.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    write_header = not csv_path.exists()
    latencies = [r.trace.latency_ms for r in run.results]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    sorted_lat = sorted(latencies)
    p95_idx = max(0, int(len(sorted_lat) * 0.95) - 1)
    p95_latency = sorted_lat[p95_idx] if sorted_lat else 0

    with open(csv_path, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow([
                "timestamp", "model", "scenarios", "pass", "partial", "fail",
                "avg_latency_ms", "p95_latency_ms",
            ])
        writer.writerow([
            run.timestamp.isoformat(),
            run.model,
            run.scenario_count,
            run.summary.get("pass", 0),
            run.summary.get("partial", 0),
            run.summary.get("fail", 0),
            f"{avg_latency:.1f}",
            f"{p95_latency:.1f}",
        ])


def _cmd_list() -> None:
    """List available scenarios."""
    scenarios = load_scenarios(_SCENARIOS_DIR)
    if not scenarios:
        print("No scenarios found.")
        return
    for s in scenarios:
        tags = f"  [{', '.join(s.tags)}]" if s.tags else ""
        desc = f"  — {s.description}" if s.description else ""
        print(f"  {s.name}{tags}{desc}")


def _cmd_runs() -> None:
    """List saved runs."""
    runs = list_runs(_RUNS_DIR)
    if not runs:
        print("No saved runs.")
        return
    for run_id in runs:
        print(f"  {run_id}")


def _cmd_compare(args: argparse.Namespace) -> None:
    """Compare two runs."""
    old = load_run(args.run_id_1, _RUNS_DIR)
    new = load_run(args.run_id_2, _RUNS_DIR)
    diff = compare_runs(old, new)
    print(format_comparison(diff))


def main() -> None:
    args = _parse_args()
    match args.command:
        case "run":
            asyncio.run(_cmd_run(args))
        case "list":
            _cmd_list()
        case "runs":
            _cmd_runs()
        case "compare":
            _cmd_compare(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run ruff + mypy**

Run: `uv run ruff check evals/__main__.py && uv run ruff format evals/__main__.py && uv run mypy evals/__main__.py`

- [ ] **Step 3: Verify CLI loads**

Run: `uv run python -m evals --help`
Expected: Shows usage with `run`, `list`, `compare`, `runs` subcommands.

Run: `uv run python -m evals list`
Expected: Lists the 3 seed scenarios.

- [ ] **Step 4: Commit**

```bash
git add evals/__main__.py
git commit -m "feat(evals): add CLI entry point — run, list, compare, runs"
```

---

### Task 11: Gitignore + Backlog + Documentation

**Files:**
- Modify: `.gitignore`
- Create: `docs/backlog/evals-runner.md`
- Modify: `CLAUDE.md` (add evals to key paths and running section)

- [ ] **Step 1: Add evals/runs/ to .gitignore**

Append to `.gitignore`:
```
# Eval run artifacts (generated JSON)
evals/runs/
```

- [ ] **Step 2: Create backlog file**

```markdown
# Evals Runner — Backlog

**Source:** Design spec (2026-03-10) — items deferred from MVP.

## Deferred Items

### 1. Regression mode (mocked Ollama)
**Priority:** High

Canned SLM responses for fast, deterministic CI runs. Same scenario format, mock Ollama client injected into pipeline. Enables `python -m evals run --mode regression`.

### 2. pytest integration
**Priority:** Medium

Scenarios auto-discovered as pytest test cases. `pytest evals/` for CI gating. Thin wrapper that loads scenarios, runs pipeline in mock mode, asserts verdicts.

### 3. Layer 3 full pipeline eval
**Priority:** Medium

End-to-end: event enters Redis → Reflex infers → domain agent dispatches → microservice executes → ActionResult. Assert on intermediate steps and final outcome. Requires running Redis + home-service.

### 4. Intermediate step assertions
**Priority:** Low

Assert what tools were considered, what preferences influenced the decision, chain-of-thought inspection. Requires richer trace data from the engine.

### 5. SigNoz trace export
**Priority:** Low

Emit `TraceRecord` as OpenTelemetry spans. Map trace fields to span attributes. Requires OTEL SDK integration in `shared/tracing.py`.

### 6. Reflex Engine trace mode
**Priority:** Low

Production engine optionally emits `TraceRecord` behind a `ALFRED_TRACE=1` env flag. Uses the same `shared/tracing.py` model.
```

- [ ] **Step 3: Update CLAUDE.md key paths**

Add under `## Key Paths`:
```
- `evals/__main__.py` — Evals Runner entry point (`python -m evals`)
- `evals/scenarios/` — YAML eval scenarios organized by domain
- `shared/tracing.py` — `TraceRecord` model for inference tracing
```

Add under `## Running`:
```bash
# 5. Run evals (requires Ollama + tools registered in Redis)
uv run python -m evals run
uv run python -m evals list
uv run python -m evals compare <run1> <run2>
```

- [ ] **Step 4: Update pyproject.toml testpaths**

Add `"evals"` is NOT needed in testpaths since eval tests live in `tests/evals/` which is under `tests`. No change needed.

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests pass (existing + new evals tests).

- [ ] **Step 6: Run full lint + type check**

Run: `uv run ruff check . --fix && uv run ruff format . && uv run mypy bus/ core/ domains/ runner/ sdk/ shared/ evals/ telemetry/`
Expected: Clean.

- [ ] **Step 7: Commit**

```bash
git add .gitignore docs/backlog/evals-runner.md CLAUDE.md
git commit -m "docs: add evals runner backlog, update CLAUDE.md, gitignore runs/"
```

---

## Summary

| Task | Component | Tests |
|------|-----------|-------|
| 1 | `shared/tracing.py` — TraceRecord model | 3 tests |
| 2 | `core/reflex/engine.py` — extract public API | 7 tests + 2 existing |
| 3 | `evals/models.py` — data models | 5 tests |
| 4 | `evals/scorer.py` — verdict logic | 11 tests |
| 5 | `evals/loader.py` — YAML discovery | 5 tests |
| 6 | `evals/pipeline.py` — inference orchestration | 2 tests |
| 7 | `evals/store.py` — JSON run storage | 4 tests |
| 8 | `evals/compare.py` — run diffs | 6 tests |
| 9 | `evals/report.py` — terminal formatting | (verified via CLI) |
| 10 | `evals/__main__.py` — CLI | (manual verify) |
| 11 | Gitignore + backlog + docs | (full suite run) |

Total: 11 tasks, ~43 tests, 11 commits.
