---
id: EXP-005
title: Evals Framework — Repeatable SLM Reasoning Quality Measurement
status: complete
start_date: 2026-03-10
end_date: 2026-03-10
---

# EXP-005: Evals Framework

## Hypothesis

SLM reasoning quality for home automation tasks can be measured repeatably using scenario-based evaluation with structured scoring (pass/partial/fail), enabling controlled comparison across models, prompt strategies, and system configurations.

## Method

1. Define evaluation scenarios as YAML files in `evals/scenarios/home/` with input events, expected tool calls, and scoring criteria
2. Implement `Scorer` that compares SLM output against expected results (exact match on tool name, fuzzy match on parameters)
3. Define `InferFn` protocol abstracting the inference backend (Ollama, LM Studio)
4. `run_scenario()` pipeline: load scenario -> build prompt -> run inference -> score -> store result
5. `EvalRun` store persists results to Redis for comparison across runs
6. `compare` tool shows delta between two runs (score changes, latency changes)
7. Support parallel runs with `-n` flag for statistical significance (aggregate mean/stdev)
8. Support captured context fixtures (`evals/contexts/`) to ground scenarios with real HA entity IDs
9. Validate: run same scenarios 5x, confirm score stability; compare Ollama vs LM Studio backends

### Variables
- **Independent:** Model (gpt-oss:20b, gpt-oss:120b), backend (Ollama, LM Studio), number of repetitions
- **Dependent:** Pass/partial/fail rates, score variance, inference latency
- **Controlled:** Same scenarios, same context fixtures, same hardware

## Results

| Criterion | Result |
|-----------|--------|
| YAML scenario loading works | Pass |
| Scorer correctly identifies pass/partial/fail | Pass |
| Ollama backend produces valid inference | Pass |
| LM Studio backend produces valid inference | Pass |
| Results persist to Redis EvalRun store | Pass |
| Comparison tool shows meaningful deltas | Pass |
| Parallel runs (-n 5) produce aggregate statistics | Pass |
| Context fixtures ground scenarios with real entity IDs | Pass |
| CLI entry point (`python -m evals`) fully functional | Pass |

## Analysis

The evals framework provides the measurement infrastructure needed to make principled decisions about model selection, prompt engineering, and system architecture. Key findings:

1. **Scenario-based evaluation is practical.** YAML scenarios are easy to author, version-controlled, and domain-specific. The home automation domain has clear expected behaviors (event X should trigger tool Y with parameters Z), making structured scoring feasible.

2. **Scorer granularity is sufficient.** The three-tier scoring (pass = correct tool + correct params, partial = correct tool + wrong params, fail = wrong tool or no action) captures the important distinctions. More granular scoring (e.g., parameter similarity metrics) can be added later without changing the framework.

3. **Multi-backend support enables fair comparison.** The `InferFn` protocol abstracts inference completely. Comparing Ollama (direct model loading) vs LM Studio (OpenAI-compatible API) on the same scenarios isolates the impact of the serving infrastructure.

4. **Captured context fixtures solve the grounding problem.** Without real HA entity IDs in the context, the SLM might hallucinate entity names. The `capture-context` command snapshots live HA state, and scenarios reference these fixtures to ensure the SLM reasons about real entities.

5. **Aggregate runs are essential.** SLM output is non-deterministic even at temperature 0 (due to floating-point non-determinism in GPU inference). Running scenarios 5-10x and computing mean/stdev reveals the true reliability of a model-prompt combination.

### Architectural Significance

The evals framework closes the development feedback loop. Without it, changes to prompts, models, or context injection would require manual testing against live HA events. With it, any change can be validated against a suite of scenarios before deployment. This is the foundation for continuous improvement of SLM reasoning quality -- the key differentiator for Project Alfred.

### Connection to Other Experiments

- **EXP-001 (Latency):** Evals measure inference latency as a side effect, enabling latency comparisons across models.
- **EXP-002 (Tools):** Eval scenarios validate that dynamically registered tools are correctly selected.
- **EXP-004 (Context):** Context fixtures ensure eval scenarios test context-aware reasoning, not just pattern matching.
