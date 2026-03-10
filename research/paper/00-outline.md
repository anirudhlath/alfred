# Paper Outline: Zero-Latency Event Routing via SLM Reflex Engines

## Abstract
_Draft after EXP-001 results._

## 1. Introduction
- Problem: reactive chatbots vs ambient intelligence
- Gap: no architecture for sub-second LLM-driven home automation without hardcoded rules
- Contribution: Reflex Engine + Librarian Pattern + decoupled MAS architecture

## 2. Related Work
- Home automation (Home Assistant, OpenHAB)
- LLM agents (AutoGPT, CrewAI, etc.)
- Dual-process theory (System 1/System 2)

## 3. Architecture
- Four Pillars
- Event Bus (MQTT + Redis Streams)
- Reflex Engine (System 1 SLM)
- Markdown Memory + Librarian Pattern

## 4. Experiments & Results
- EXP-001: Reflex latency benchmarks
- EXP-002+: Cross-domain orchestration, Librarian compaction (future)

## 5. Discussion
## 6. Conclusion
