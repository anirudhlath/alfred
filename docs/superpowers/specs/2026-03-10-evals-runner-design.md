# Evals Runner — System Design Specification

**Date:** 2026-03-10
**Status:** Approved
**Author:** Anirudh Lath + Claude (Lead Engineer / Background Scientist)

---

## 1. Purpose

A scenario-based evaluation framework for the Reflex Engine's SLM inference. Tests whether the SLM reliably produces correct actions given structured inputs (events, preferences, tools) without requiring manual real-world testing.

### Goals

- **Reliability testing** — Run predefined scenarios against live Ollama, catch regressions and unexpected behavior
- **Full trace capture** — Record every step (prompt, raw response, parsed action, metrics) for debugging
- **Run comparison** — Diff two runs to spot improvements and regressions across model/preference changes
- **Research data** — Feed accuracy and latency metrics into the research vault pipeline

### Non-Goals (MVP)

- Mocked/deterministic regression mode (backlog)
- pytest integration / CI gating (backlog)
- Full pipeline Layer 3 evaluation (backlog)
- Intermediate step assertions (backlog)
- SigNoz/OpenTelemetry trace export (backlog)

---

## 2. Architecture

### Approach: Hybrid Engine Integration

The eval pipeline reuses the Reflex Engine's real prompt construction and response parsing methods, but controls the execution flow to capture trace data at every boundary. This prevents prompt drift while maintaining full observability.

### Engine Refactoring

`core/reflex/engine.py` extracts two public methods from the existing `process_event()` internals:

- **`build_prompt(event, preferences_text, tools) → str`** — Returns the complete prompt string (system prompt with tool section + preferences + event + decision header). Same logic currently inline in `process_event()` lines 110–119, combined with `_build_system_prompt()`. The `tools` parameter is `list[ToolInfo]` (the existing frozen dataclass from `tool_registry.py`).
- **`parse_response(response, event, valid_services) → ActionRequest | None`** — Parses the SLM's JSON output and validates `target_service` against `valid_services: set[str]` (the hallucination guard). Same logic currently in `_parse_response()`, made public. In eval context, `valid_services` is derived from the tools list via `ToolRegistry.get_registered_services(tools)`.

`process_event()` continues to call these internally — zero production behavior change.

### Pipeline Flow

```
loader → Scenario
              │
              ▼
      engine.build_prompt(scenario.event, preferences, tools: list[ToolInfo])
              │
              ▼  (prompt: str)
      ollama_client.infer(prompt, model)
              │
              ▼  (dict: response, prompt_tokens, completion_tokens, total_tokens)
      engine.parse_response(response, event, valid_services)
              │
              ▼  (ActionRequest | None)
      ── all captured into TraceRecord ──
              │
              ▼
      scorer.score(trace, scenario.expected) → ScenarioResult
              │
              ▼
      store.save(EvalRun) → evals/runs/{run_id}.json
```

Note: `ollama_client.infer()` returns a `dict[str, object]` with keys `response`, `prompt_tokens`, `completion_tokens`, `total_tokens`. Latency is measured externally by the pipeline (wall-clock around the `infer` call).

### File Layout

```
shared/
  tracing.py              # TraceRecord model (reusable across evals, engine, SigNoz)

core/reflex/
  engine.py               # Refactored — build_prompt() and parse_response() made public

evals/
  __init__.py
  __main__.py             # CLI entry: python -m evals
  models.py               # Scenario, ExpectedAction, Verdict, ScenarioResult, EvalRun
  loader.py               # Discover & validate YAML scenarios via Pydantic
  pipeline.py             # Orchestrates: engine.build_prompt → ollama.infer → engine.parse_response
  scorer.py               # Compare trace output to expected, produce verdict
  store.py                # Save/load EvalRun as JSON to evals/runs/
  compare.py              # Diff two runs, report changes
  report.py               # Human-readable terminal summary
  scenarios/
    home/
      tv_on_dims_lights.yaml
      bedtime_lights_off.yaml
      irrelevant_temperature.yaml
    ...
  runs/                   # .gitignored — generated JSON run files
```

---

## 3. Data Models

### TraceRecord (`shared/tracing.py`)

Pydantic model capturing a complete inference trace. Reusable by evals, the Reflex Engine (future debug mode), and SigNoz export.

```python
class TraceRecord(BaseModel):
    trace_id: str                          # unique per trace
    timestamp: datetime
    model: str                             # e.g. "gpt-oss:20b"

    # Inputs
    event: StateChangedEvent
    preferences_text: str                  # resolved preferences sent to SLM
    tools: list[dict[str, Any]]            # serialized ToolInfo dicts for JSON storage

    # Prompt
    prompt: str                            # full combined prompt sent to Ollama

    # Output
    raw_response: str                      # raw SLM response string (from Ollama dict["response"])
    parsed_action: ActionRequest | None    # structured result

    # Metrics
    latency_ms: float                      # wall-clock around ollama_client.infer()
    prompt_tokens: int                     # from Ollama response dict
    completion_tokens: int                 # from Ollama response dict
```

Note: `tools` is stored as `list[dict[str, Any]]` (serialized from `list[ToolInfo]` via `asdict()`) for JSON portability. The pipeline works with `list[ToolInfo]` internally and serializes when building the `TraceRecord`.

### Scenario (`evals/models.py`)

```python
class ExpectedAction(BaseModel):
    tool_name: str
    target_service: str | None = None      # optional, stricter match
    parameters: dict[str, Any] | None = None  # optional param assertion

class Scenario(BaseModel):
    name: str
    description: str | None = None
    tags: list[str] = []                   # e.g. ["home", "lighting", "regression"]
    event: StateChangedEvent
    preferences_dir: str | None = None     # override default preferences
    expected: ExpectedAction | None        # None = expect no action
```

### Verdict & Run Results (`evals/models.py`)

```python
class Verdict(str, Enum):
    PASS = "pass"
    PARTIAL = "partial"
    FAIL = "fail"

class ScenarioResult(BaseModel):
    scenario: Scenario
    verdict: Verdict
    reason: str                            # e.g. "correct tool, wrong params: room"
    trace: TraceRecord

class EvalRun(BaseModel):
    run_id: str
    timestamp: datetime
    model: str
    scenario_count: int
    results: list[ScenarioResult]
    summary: dict[Verdict, int]            # {"pass": 8, "partial": 1, "fail": 1}
```

---

## 4. Scenario Format (YAML)

Scenarios are YAML files in `evals/scenarios/`, organized by domain. Validated via Pydantic on load.

```yaml
# evals/scenarios/home/tv_on_dims_lights.yaml
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

### No-Action Scenario

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

---

## 5. Scoring Logic

Tiered categorical verdicts — no numeric weights.

### Rules

| Condition | Verdict | Reason |
|-----------|---------|--------|
| Expected `null`, SLM returns `None` | **PASS** | "correctly took no action" |
| Expected `null`, SLM returns action | **FAIL** | "expected no action, got {tool_name}" |
| Expected action, SLM returns `None` | **FAIL** | "expected {tool_name}, got no action" |
| tool_name matches, params match (or unspecified) | **PASS** | "exact match" |
| tool_name matches, params mismatch | **PARTIAL** | "correct tool, wrong params: {diff}" |
| tool_name doesn't match | **FAIL** | "expected {expected}, got {actual}" |

### Parameter Matching

- Only asserts on parameters **specified in the expected block** — extra SLM params are ignored
- Type-coerced comparison (`"40"` matches `40`) — SLMs sometimes return strings for numbers
- Missing expected params → PARTIAL with details on which params are absent

---

## 6. CLI Interface

Entry point: `python -m evals`

### Commands

```bash
# Run all scenarios against live Ollama
python -m evals run

# Filter by tag
python -m evals run --tag home --tag lighting

# Run specific scenario file
python -m evals run --scenario evals/scenarios/home/tv_on_dims_lights.yaml

# Override Ollama model
python -m evals run --model gpt-oss:20b

# List available scenarios
python -m evals list

# Compare two runs
python -m evals compare <run_id_1> <run_id_2>
```

### `run` Output

```
Eval Run: 2026-03-10T14:30:00  |  Model: gpt-oss:20b  |  3 scenarios

  ✓ tv_on_dims_lights .............. PASS   (423ms)
  ~ bedtime_lights_off ............. PARTIAL (512ms)
    → correct tool, wrong params: missing "room"
  ✗ irrelevant_temperature ......... FAIL   (389ms)
    → expected no action, got lighting.dim_lights

Summary: 1 pass | 1 partial | 1 fail
Run saved: evals/runs/2026-03-10T143000_gpt-oss-20b.json
```

### `compare` Output

```
Comparing: 2026-03-10T143000 → 2026-03-10T160500

  tv_on_dims_lights .............. PASS → PASS   (423ms → 401ms)
  bedtime_lights_off ............. PARTIAL → PASS  (512ms → 488ms)  improved
  irrelevant_temperature ......... FAIL → FAIL   (389ms → 395ms)

Verdicts: +1 improved | 0 regressed | 1 unchanged
Avg latency: 441ms → 428ms (-3%)
```

---

## 7. Run Storage & Comparison

### Storage

Runs saved as JSON to `evals/runs/{timestamp}_{model}.json` (gitignored).

```json
{
  "run_id": "2026-03-10T143000_gpt-oss-20b",
  "timestamp": "2026-03-10T14:30:00Z",
  "model": "gpt-oss:20b",
  "scenario_count": 3,
  "summary": {"pass": 1, "partial": 1, "fail": 1},
  "results": [
    {
      "scenario": { "name": "tv_on_dims_lights" },
      "verdict": "pass",
      "reason": "exact match",
      "trace": {
        "prompt": "...",
        "raw_response": "...",
        "parsed_action": { "tool_name": "lighting.dim_lights" },
        "latency_ms": 423.1,
        "prompt_tokens": 512,
        "completion_tokens": 34
      }
    }
  ]
}
```

### Comparison Logic

For each scenario present in both runs:
- **Verdict change**: improved (fail→partial, fail→pass, partial→pass), regressed (reverse), unchanged
- **Latency delta**: absolute ms and percentage change
- **Response diff**: if verdict changed, show what the SLM returned differently

Scenarios added or removed between runs flagged separately.

### Research Vault Integration

After each run, append summary to `research/data/evals.csv`:

```csv
timestamp,model,scenarios,pass,partial,fail,avg_latency_ms,p95_latency_ms
2026-03-10T14:30:00,gpt-oss:20b,3,1,1,1,441.3,512.0
```

Feeds existing research pipeline (daily notes, experiment logs, paper data).

---

## 8. Backlog

Deferred from MVP — tracked in `docs/backlog/evals-runner.md`:

1. **Regression mode (mocked Ollama)** — Canned SLM responses for fast, deterministic CI runs. Same scenario format, mock Ollama client.
2. **pytest integration** — Scenarios auto-discovered as pytest test cases. `pytest evals/` for CI gating.
3. **Layer 3 full pipeline eval** — End-to-end: event → bus → Reflex → agent → microservice → ActionResult. Assert on intermediate steps and final outcome.
4. **Intermediate step assertions** — Assert what tools were considered, what preferences influenced the decision, chain-of-thought inspection.
5. **SigNoz trace export** — Emit `TraceRecord` as OpenTelemetry spans for production observability.
6. **Reflex Engine trace mode** — Production engine optionally emits `TraceRecord` behind a debug flag.

---

## 9. Dependencies

### New

- `pyyaml` — YAML scenario loading (already in base dependencies: `pyyaml>=6.0`)

### Existing (no changes)

- `pydantic` — model validation
- `httpx` — Ollama HTTP calls (via existing `ollama_client`)
- `redis` — tool registry access (via existing `ToolRegistry`)
