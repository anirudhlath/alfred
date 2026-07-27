# Attention Set Gating of Reflex SLM

**Feature:** Attention set (Tier 2 observation)
**Priority:** high
**Type:** functional

## Prerequisites
- Full stack running with live HA state flowing on `alfred:home:state_changed`
- Ollama up (Reflex SLM active)

## Test Steps
1. Toggle a seeded entity (e.g. a light) in HA; watch reflex logs
2. Cause a state change on a non-seeded entity (e.g. a power sensor)
3. In chat: "add sensor.<that_sensor> to your attention for home", then change its state again
4. Toggle the same light twice within 5 seconds
5. `redis-cli SMEMBERS alfred:attention:home` — verify membership matches

## Expected Result
- Step 1: reflex processes the event (SLM inference log)
- Step 2: log shows "Attention-gated: ..." at DEBUG; no SLM call; trigger engine still sees the event
- Step 3: entity now fires the SLM
- Step 4: second toggle suppressed by cooldown
- Step 5: membership set contains the light + the manually added sensor
