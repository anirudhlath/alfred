# Paper Outline: Zero-Latency Event Routing via SLM Reflex Engines

## Abstract

We present Project Alfred, an ambient multi-agent system for smart home automation that replaces hardcoded rules with small language model (SLM) inference at the edge. The system uses a dual-process architecture inspired by cognitive science: a Reflex Engine (System 1) provides sub-second event-to-action routing via a local SLM, while a Conscious Engine (System 2) handles complex reasoning via cloud LLMs. Early results with a 20B-parameter model on Apple Silicon show median end-to-end latency of 6.4 seconds with consistent 27-token structured outputs, establishing a baseline for optimization toward the 500ms target on production hardware (RTX 4090 + 8B model). We further demonstrate dynamic tool registration, proactive trigger creation, live context injection, and a repeatable evaluation framework -- collectively proving that LLM-driven home automation can be extensible, context-aware, and measurable without hardcoded rules.

_Status: Draft. Revise after RTX 4090 benchmarks and Llama 3 8B comparison._

## 1. Introduction
- Problem: reactive chatbots vs ambient intelligence
- Gap: no architecture for sub-second LLM-driven home automation without hardcoded rules
- Contribution: Reflex Engine + Librarian Pattern + decoupled MAS architecture
- _Data available:_ 17 live inference runs demonstrating end-to-end pipeline (EXP-001)

## 2. Related Work
- Home automation (Home Assistant, OpenHAB)
- LLM agents (AutoGPT, CrewAI, etc.)
- Dual-process theory (System 1/System 2)
- Edge inference and SLM deployment (Ollama, llama.cpp, vLLM)
- Dynamic tool use in LLMs (function calling, tool-use benchmarks)

## 3. Architecture
- Four Pillars (Proactivity, Decoupling, Deterministic Comms, Librarian Pattern)
- Event Bus (MQTT + Redis Streams) -- implemented and validated
- Reflex Engine (System 1 SLM) -- implemented, 17 runs of telemetry (EXP-001)
- Dynamic Tool Registry -- proven via BaseFeature + @tool pattern (EXP-002)
- Trigger Engine -- proactive triggers without hardcoded rules (EXP-003)
- Context Injection -- live entity state in SLM prompt (EXP-004)
- Markdown Memory + Librarian Pattern -- preferences implemented, Librarian deferred to Phase 3
- _Section status: Can be drafted now. Concrete implementations exist for all subsystems except Librarian and System 2._

## 4. Experiments and Results

### 4.1 EXP-001: Reflex Engine Latency
- Baseline: n=17, median 6415 ms, p95 10452 ms on M4 Max with gpt-oss:20b
- Completion tokens: 24-28 (well-constrained structured output)
- 500ms target not met but expected given model size and hardware mismatch
- _Status: Data collected, analysis complete. Pending production hardware runs._

### 4.2 EXP-002: Dynamic Tool Registry
- Proved zero-config tool extensibility via BaseFeature + Redis registry
- SLM correctly selects dynamically registered tools from prompt
- _Status: Complete. Can be written up._

### 4.3 EXP-003: Trigger Engine
- Proved proactive automation without hardcoded rules
- Time, sensor, and composite triggers created by SLM at runtime
- _Status: Complete. Can be written up._

### 4.4 EXP-004: Context Injection
- Proved live HA entity state grounds SLM reasoning
- Token cost: +200-400 prompt tokens per inference call
- _Status: Complete. Can be written up._

### 4.5 EXP-005: Evals Framework
- Scenario-based evaluation with structured scoring
- Multi-backend (Ollama, LM Studio), parallel runs, aggregate statistics
- _Status: Complete. Methodology section can be written._

### 4.6 Future Experiments
- EXP-006: Latency on RTX 4090 with Llama 3 8B (production target)
- EXP-007: Cross-domain orchestration (multiple services, tool routing accuracy)
- EXP-008: Librarian Pattern (scratchpad consolidation quality)
- EXP-009: System 2 reasoning (Claude for complex multi-step tasks)

## 5. Discussion
- Latency vs capability trade-off (more context = better decisions = slower inference)
- SLM output constraint effectiveness (27-token median with structured format)
- Dynamic registration as a design principle for MAS extensibility
- Limitations: single model, single hardware platform, small sample size
- _Section status: Can begin drafting based on EXP-001 through EXP-005._

## 6. Conclusion
- Alfred demonstrates that SLM-driven home automation is feasible, extensible, and measurable
- The 500ms target remains aspirational but the architecture is validated
- Next: production hardware benchmarks, System 2 integration, Librarian consolidation
- _Section status: Defer until production benchmarks complete._

## Appendices
- A. System prompt template (Reflex Engine)
- B. Tool manifest schema
- C. Evaluation scenario examples
- D. Full telemetry data tables
