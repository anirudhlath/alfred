---
id: EXP-001
title: Reflex Engine Latency — SLM Event-to-Action Speed
status: planned
start_date: null
end_date: null
---

# EXP-001: Reflex Engine Latency

## Hypothesis

A local SLM (Llama 3 8B on RTX 4090) can process a Home Assistant state change event
and produce a context-aware action in under 500ms, using only plain-text Markdown
preferences (no hardcoded rules, no RAG retrieval).

## Method

1. HA emits a state_changed event (TV turns on) via MQTT
2. Bridge forwards to Redis Streams
3. Reflex Engine reads event + preferences, prompts Ollama
4. SLM returns structured action (dim lights)
5. Measure full trace: event timestamp → action published

### Variables
- **Independent:** SLM model size (8B, 13B), preference file size, event complexity
- **Dependent:** End-to-end latency (ms), inference latency (ms), token usage
- **Controlled:** Hardware (RTX 4090), Redis/MQTT on same host

## Results

| Run | Model | Prefs Size | E2E Latency (ms) | Inference (ms) | Tokens | Action Correct |
|-----|-------|-----------|-------------------|----------------|--------|----------------|
| _pending_ | | | | | | |

## Analysis

_Pending first experimental run._
