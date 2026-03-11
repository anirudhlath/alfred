---
id: EXP-002
title: Dynamic Tool Registry — Auto-Registration and Prompt Building
status: complete
start_date: 2026-03-10
end_date: 2026-03-10
---

# EXP-002: Dynamic Tool Registry

## Hypothesis

External microservices can register tool manifests at startup via the SDK, and the Reflex Engine can dynamically build its prompt from the tool registry at runtime, eliminating all hardcoded tool names and enabling zero-config extensibility.

## Method

1. Define `BaseFeature` ABC with `@tool` decorator in `sdk/alfred_sdk/feature.py`
2. `AlfredClient.discover_features()` scans packages for `BaseFeature` subclasses
3. `AlfredClient.register()` serializes tool manifests to Redis hash `alfred:tool_registry`
4. `ToolRegistry` in `core/reflex/tool_registry.py` reads manifests at inference time
5. Reflex Engine prompt includes dynamically generated tool descriptions and parameter schemas
6. Validate: add a new tool in home-service, restart, confirm it appears in the Reflex prompt without any code change in alfred/

### Variables
- **Independent:** Number and type of registered tools
- **Dependent:** Prompt correctness (tools appear with correct schemas), tool invocation accuracy
- **Controlled:** Single home-service instance, same Ollama model

## Results

| Criterion | Result |
|-----------|--------|
| Tools auto-discovered from BaseFeature subclasses | Pass |
| Manifests written to Redis on `register()` | Pass |
| Manifests removed on `unregister()` / shutdown | Pass |
| Reflex Engine prompt includes all registered tools | Pass |
| SLM correctly selects tools by name from dynamic prompt | Pass |
| Adding a new tool requires zero changes to alfred/ core | Pass |

## Analysis

The dynamic tool registry successfully decouples tool definition from tool consumption. Key design decisions validated:

1. **Decorator pattern works.** The `@tool` decorator on `BaseFeature` methods captures name, description, and parameter schema at class definition time. No separate manifest file is needed.

2. **Redis as registry.** Using a Redis hash (`alfred:tool_registry`) for tool manifests provides both persistence across restarts and real-time discoverability. Tools appear/disappear as services start/stop.

3. **Prompt size trade-off.** Including full tool schemas in the prompt adds 100-400 tokens depending on the number of registered tools. This contributes to the prompt inflation observed in EXP-001 but is architecturally necessary for the "no hardcoding" principle.

4. **Correctness confirmed.** The SLM correctly selects and parameterizes tools it has never seen in training, purely from the dynamic prompt description. This validates the approach of treating the tool registry as a runtime capability manifest rather than a compile-time dependency.

### Architectural Significance

This experiment proves Pillar 2 (Decoupled Domains) at the tool level. Any service that implements `BaseFeature` and calls `register()` becomes immediately available to the Reflex Engine. No code changes, no redeployment of core services, no configuration updates.
