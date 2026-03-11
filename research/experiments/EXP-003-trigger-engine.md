---
id: EXP-003
title: Trigger Engine — Proactive Dynamic Triggers
status: complete
start_date: 2026-03-10
end_date: 2026-03-10
---

# EXP-003: Trigger Engine

## Hypothesis

An LLM-driven system can dynamically create, evaluate, and fire proactive triggers at runtime (time-based, sensor-based, and composite), without any hardcoded automation rules, enabling Pillar 1 (Proactivity).

## Method

1. Implement `BaseTrigger` ABC with `TimeTrigger` (cron), `SensorTrigger` (threshold), and `CompositeTrigger` (N-of-M) concrete types
2. `TriggerRegistry` allows runtime registration of new trigger types via `@TriggerRegistry.register_type()` decorator
3. `TriggerStore` persists triggers to Redis hash + YAML snapshot for durability
4. `TriggerEngine` runs two loops: a fire loop (checks time triggers every second) and an eval loop (evaluates sensor triggers against incoming events)
5. `TriggerFeature` exposes CRUD tools (`create_trigger`, `list_triggers`, `delete_trigger`, `get_trigger`) via BaseFeature
6. SLM can create triggers by calling these tools through the Reflex Engine
7. Validate with end-to-end smoke test: create a trigger via tool call, wait for it to fire, confirm TriggerFired event on the bus

### Variables
- **Independent:** Trigger type (time, sensor, composite), trigger parameters
- **Dependent:** Correct firing behavior, evaluation latency, persistence across restarts
- **Controlled:** Single TriggerEngine instance, Redis on localhost

## Results

| Criterion | Result |
|-----------|--------|
| TimeTrigger fires at correct cron schedule | Pass |
| SensorTrigger evaluates against event stream | Pass |
| CompositeTrigger fires when N-of-M children met | Pass |
| TriggerStore persists to Redis and YAML | Pass |
| Triggers survive service restart (reload from store) | Pass |
| CRUD tools registered via BaseFeature pattern | Pass |
| SLM creates triggers via tool calls | Pass |
| TriggerFired events published to event bus | Pass |
| New trigger types registerable via decorator | Pass |

## Analysis

The Trigger Engine validates Pillar 1 (Proactivity) end-to-end. Key findings:

1. **LLM-created triggers work.** The SLM can interpret a user request like "remind me to check the oven in 20 minutes" and produce a correct `create_trigger` tool call with appropriate cron expression. This is the core innovation -- proactive behavior emerges from LLM reasoning, not from hardcoded rules.

2. **Composite triggers enable complex scenarios.** The N-of-M pattern allows triggers like "if temperature > 80 AND humidity > 60, then alert" without any custom logic. The `CompositeTrigger` simply aggregates child trigger evaluations.

3. **Type registry is extensible.** The `@TriggerRegistry.register_type()` decorator pattern mirrors the tool registry approach. New trigger types (e.g., `LocationTrigger`, `CalendarTrigger`) can be added as separate modules without modifying the engine.

4. **JSON-RPC server provides external access.** The HTTP JSON-RPC interface at the TriggerEngine allows non-Python services to interact with triggers, maintaining Pillar 3 (Deterministic Communication).

### Architectural Significance

This is the first proof that Alfred can be proactive, not just reactive. The Reflex Engine (System 1) handles immediate event-response, while the Trigger Engine enables deferred and conditional actions. Together, they cover both reactive and proactive automation without any hardcoded rules.
