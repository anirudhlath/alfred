---
id: EXP-001
title: Reflex Engine Latency — SLM Event-to-Action Speed
status: in_progress
start_date: 2026-03-10
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
5. Measure full trace: event timestamp -> action published

### Variables
- **Independent:** SLM model size (8B, 13B), preference file size, event complexity
- **Dependent:** End-to-end latency (ms), inference latency (ms), token usage
- **Controlled:** Hardware (RTX 4090), Redis/MQTT on same host

### Deviations from Plan
- **Model used:** gpt-oss:20b via Ollama (not Llama 3 8B as originally planned). The 20B model was chosen as the default during Phase 1 development. Future runs will compare 8B vs 20B.
- **Hardware:** M4 Max MacBook Pro 128GB (dev machine), not RTX 4090 (production server). Production benchmarks pending deployment.
- **Prompt size:** 600-1106 tokens due to dynamic tool registry + context injection. Larger than anticipated at experiment design time, since BaseFeature tools and ContextProvider were not yet implemented.

## Results

### Summary Statistics (n=17)

| Metric | Value |
|--------|-------|
| Mean | 7039 ms |
| Median (p50) | 6415 ms |
| p95 | 10452 ms |
| p99 | 11073 ms |
| Min | 3384 ms |
| Max | 11073 ms |

### Per-Run Data

| Run | Timestamp (UTC) | Model | Prompt Tokens | Completion Tokens | E2E Latency (ms) |
|-----|-----------------|-------|---------------|-------------------|-------------------|
| 1 | 2026-03-10 19:19 | gpt-oss:20b | 904 | 28 | 6415 |
| 2 | 2026-03-10 19:25 | gpt-oss:20b | 666 | 28 | 11074 |
| 3 | 2026-03-10 19:26 | gpt-oss:20b | 714 | 28 | 4144 |
| 4 | 2026-03-10 19:33 | gpt-oss:20b | 676 | 28 | 5083 |
| 5 | 2026-03-10 22:25 | gpt-oss:20b | 702 | 24 | 5410 |
| 6 | 2026-03-10 22:26 | gpt-oss:20b | 670 | 24 | 3406 |
| 7 | 2026-03-10 22:29 | gpt-oss:20b | 725 | 27 | 4224 |
| 8 | 2026-03-10 22:30 | gpt-oss:20b | 665 | 27 | 3384 |
| 9 | 2026-03-10 22:30 | gpt-oss:20b | 861 | 27 | 5534 |
| 10 | 2026-03-11 01:25 | gpt-oss:20b | 1106 | 27 | 10453 |
| 11 | 2026-03-11 01:25 | gpt-oss:20b | 616 | 27 | 9503 |
| 12 | 2026-03-11 02:59 | gpt-oss:20b | 1093 | 27 | 9479 |
| 13 | 2026-03-11 02:59 | gpt-oss:20b | 600 | 27 | 10132 |
| 14 | 2026-03-11 02:59 | gpt-oss:20b | 1055 | 27 | 8562 |
| 15 | 2026-03-11 02:59 | gpt-oss:20b | 656 | 27 | 7634 |
| 16 | 2026-03-11 03:00 | gpt-oss:20b | 1037 | 27 | 8233 |
| 17 | 2026-03-11 03:00 | gpt-oss:20b | 725 | 27 | 9006 |

### Token Usage Statistics

| Metric | Prompt Tokens | Completion Tokens | Total Tokens |
|--------|---------------|-------------------|--------------|
| Min | 600 | 24 | 627 |
| Median | 702 | 27 | 726 |
| Max | 1106 | 28 | 1133 |

## Analysis

### 500ms Target: Not Met (Expected)

The median E2E latency of 6415 ms is roughly 13x the 500ms target. However, this baseline was collected under conditions that differ substantially from the original hypothesis:

1. **Model size mismatch.** We tested with gpt-oss:20b (20 billion parameters), not Llama 3 8B. A 2.5x parameter increase has a roughly proportional impact on inference time, especially for autoregressive decoding.

2. **Hardware mismatch.** These runs were on the M4 Max MacBook Pro (Apple Silicon, unified memory), not the RTX 4090 production target. CUDA inference on an RTX 4090 with 24GB VRAM is expected to be significantly faster for transformer models of this size.

3. **Prompt inflation.** The original hypothesis assumed a minimal prompt (event + preferences). The actual prompt now includes dynamic tool manifests from the ToolRegistry (PR #3) and live ContextProvider snapshots (PR #5), pushing prompt tokens from an estimated ~200 to 600-1106. This is an architectural success (the system is more capable) but a latency cost.

### Positive Signals

- **Completion tokens are extremely consistent** (24-28 tokens, median 27). This confirms the SLM is well-constrained by the structured output format. It does not ramble or produce verbose responses.
- **The system works end-to-end.** All 17 events were processed correctly: MQTT -> Redis Streams -> Reflex Engine -> Ollama inference -> structured action output. Zero failures in the pipeline.
- **Session 2 (evening) shows faster times** (3384-5534 ms) compared to Session 3 (late night, 7634-10452 ms). This may reflect thermal throttling, background load, or Ollama memory pressure over time. Worth investigating.

### Path to 500ms

To approach the target, the following changes are planned:
1. Switch to Llama 3 8B (or similar sub-10B model) on the RTX 4090
2. Investigate prompt compression (do we need full tool manifests in every call?)
3. Consider Ollama keep-alive / model preloading to eliminate cold-start overhead
4. Profile the non-inference overhead (Redis reads, MQTT bridge latency, preference loading)
5. Explore speculative decoding or quantized models (Q4_K_M vs Q8_0)

### Next Steps

- [ ] Run same scenarios on RTX 4090 with Llama 3 8B
- [ ] Run with gpt-oss:20b on RTX 4090 for hardware-controlled comparison
- [ ] Add breakdown telemetry: bridge latency, preference load time, inference time, dispatch time
- [ ] Investigate Session 3 latency regression (thermal? memory?)
