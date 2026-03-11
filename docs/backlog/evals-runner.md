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
