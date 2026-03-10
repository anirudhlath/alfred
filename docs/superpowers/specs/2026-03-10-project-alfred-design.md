# Project Alfred — System Design Specification

**Date:** 2026-03-10
**Status:** Approved
**Author:** Anirudh Lath + Claude (Lead Engineer / Background Scientist)

---

## 1. Vision

Project Alfred is an ambient, voice-first, highly decoupled Multi-Agent System (MAS) for smart environments. Inspired by Alfred Pennyworth — a steward who anticipates needs, manages the estate, and coordinates household staff based on deep inferred knowledge of habits.

Alfred does not blindly execute commands or rely on hardcoded IF/THEN rules. It uses an SLM Reflex Engine guided by plain-text preferences to make zero-latency context-aware decisions.

### The Four Pillars (Non-Negotiable)

1. **Proactivity & Dynamic Triggers** — The system relies on a continuous Event Bus and an LLM-created Trigger Engine. Triggers are scheduled or event-conditional, never hardcoded.
2. **Decoupled Domains** — Microservices are sovereign independent applications. Alfred connects via `alfred-sdk` only. No direct imports between apps.
3. **Deterministic Communication** — All inter-agent messages are Pydantic-validated JSON. No natural language between agents. Alfred is router + synthesizer.
4. **Stateful Memory (Librarian Pattern)** — Core memory uses plain Markdown with YAML frontmatter. Real-time writes go to a scratchpad only (serialized through a single async writer coroutine to prevent corruption). A Librarian Agent consolidates nightly.

### Meta-Goal: Academic Research Pipeline

The architecture targets a patent and academic paper focusing on:
- Zero-latency SLM event routing
- Librarian context compaction
- Markdown-based preference memory vs RAG

Telemetry is instrumented from day one. Data flows automatically to an Obsidian research vault.

---

## 2. Architecture Layers

### Alfred OS (The Steward) — `alfred/core/`

| Component | Purpose | Phase |
|-----------|---------|-------|
| **Reflex Engine (System 1)** | Fast local SLM (Llama 3 8B via Ollama). Listens to Event Bus, reads preferences, executes tool calls. Target: <500ms. | Phase 1 |
| **Trigger Engine** | Manages LLM-created scheduled + event-conditional triggers. Evaluates conditions against Event Bus and time. | Phase 2 |
| **Conscious Engine (System 2)** | Cloud LLM (Claude). Complex reasoning, ambiguity resolution, planning, preference memory updates. | Phase 3 |
| **Voice I/O** | Adapter pattern: local Whisper STT + configurable TTS (local/cloud). Part of Alfred OS, not a microservice. | Phase 3 |
| **Memory** | Markdown preference files + ephemeral scratchpad. Human-readable, git-tracked. Scratchpad writes are serialized through a single async coroutine — all components append observations via a Redis List, and the writer drains to `scratchpad.md` in order. | Phase 1 |
| **Librarian Agent** | Nightly synthesis of scratchpad into core preferences. Contradiction resolution: the Librarian (an LLM call) compares new observations against existing preferences, applies last-observation-wins for factual state and LLM-arbitrated merge for behavioral preferences, writes updated Markdown, and archives the processed scratchpad entries. On failure, the scratchpad accumulates and the Librarian retries next cycle — core preference files are never left in a partial state. | Phase 3 |

### Domains — `alfred/domains/`

Organizational boundaries within Alfred. Each domain has one or more sub-agents that manage that domain's concerns:

```
domains/
├── home/
│   └── home_agent.py       # Manages smart home interactions
├── media/
│   └── media_agent.py      # Manages media interactions
└── ...                     # Future: security, finance, social, health
```

Sub-agents are Alfred's staff. They subscribe to the Event Bus, translate MCP tool calls into microservice-specific APIs, and escalate anomalies to Alfred via strict JSON payloads.

**Phase note:** `domains/home/home_agent.py` is a Phase 1 deliverable (required for the Reflex Engine to route actions to the home-service). `domains/media/media_agent.py` is Phase 2.

### Microservices (Independent Applications)

Microservices are **sovereign applications** in their own repositories. They have their own value independent of Alfred — a media server might aggregate Emby + Plex with its own database and frontend. Alfred integration is optional via `alfred-sdk`.

```
workspace/
├── alfred/              ← monorepo (Alfred OS + domains + bus + SDK)
├── home-service/        ← independent repo (HA wrapper)
├── media-server/        ← independent repo (Emby/Plex aggregator)
├── robinhood-service/   ← independent repo (trading app)
└── ...
```

Each app keeps Alfred integration in a dedicated `alfred_ext/` directory:

```python
# home-service/alfred_ext/register.py
from alfred_sdk import AlfredClient, mcp_tool, publish, subscribe

client = AlfredClient()

@mcp_tool(name="smart_home.dim_lights", desc="Dim lights to a level")
def dim_lights(room: str, level: int):
    return app.set_light_level(room, level)

@publish(topic="home/state_changed")
def emit_state_change(device, state):
    """Publishes return value to the Event Bus."""
    return {"device": device, "state": state}

@subscribe(topic="home/command")
def on_command(payload):
    """Receives events from the Event Bus."""
    app.execute(payload)

client.register()  # Announces capabilities to Alfred at runtime
```

### Event Bus

Two layers:
- **MQTT (Mosquitto)** — Edge layer. HA and devices speak MQTT natively. IoT pub/sub.
- **Redis Streams** — Internal backbone. Consumer groups, replay, persistence. All inter-service communication.

A bridge service forwards MQTT events to Redis Streams and vice versa.

### alfred-sdk

The **only coupling** between Alfred and external applications. Published as a Python package.

Provides:
- `AlfredClient` — connect to MQTT/Redis, register capabilities
- `@mcp_tool` — declare MCP tool capabilities (app becomes a tool server)
- `@publish(topic=...)` — emit events to the Event Bus (outbound)
- `@subscribe(topic=...)` — receive events from the Event Bus (inbound)
- `@track_latency`, `@track_tokens`, `@track_event` — telemetry decorators
- Pydantic event schema helpers

Apps install it as an optional dependency: `pip install alfred-sdk`

### MCP Topology

Each microservice that uses `@mcp_tool` acts as an **MCP tool server**. When the app calls `client.register()`, it announces its tool manifest (names, schemas, endpoint) to a **Tool Registry** on Redis. The Reflex Engine and domain sub-agents are **MCP clients** — they query the registry to discover available tools and invoke them via HTTP transport.

- **Tool servers:** Microservices (home-service, media-server, etc.) — each runs a lightweight HTTP MCP endpoint via the SDK
- **Tool clients:** Reflex Engine and domain sub-agents — discover tools from the registry, make calls
- **Registry:** Redis hash storing tool manifests, updated on `client.register()` and heartbeat
- **Transport:** HTTP (JSON-RPC), not stdio — services are networked containers

---

## 3. Workspace & Repository Structure

### Workspace Root (not a git repo)

```
/Users/anirudhlath/code/private/alfred/
├── .claude/                    # Workspace-level Claude Code config
│   ├── settings.json
│   └── rules/                  # Workspace-wide rules
├── alfred/                     # Alfred monorepo (git repo)
├── home-service/               # Independent git repo
└── ...
```

### Alfred Monorepo

```
alfred/
├── CLAUDE.md                   # Identity + @imports to rules (~100 lines)
├── docker-compose.yml
├── .env.example
│
├── .claude/
│   ├── settings.json
│   ├── rules/
│   │   ├── architecture.md          # Unconditional — Four Pillars
│   │   ├── python-conventions.md    # Unconditional — code style
│   │   ├── research-protocol.md     # Unconditional — scientist role
│   │   ├── core/
│   │   │   ├── reflex-engine.md     # paths: "core/reflex/**"
│   │   │   ├── triggers.md          # paths: "core/triggers/**"
│   │   │   ├── memory-system.md     # paths: "core/memory/**"
│   │   │   └── librarian.md         # paths: "core/librarian/**"
│   │   ├── domains/
│   │   │   └── domain-conventions.md  # paths: "domains/**"
│   │   ├── sdk/
│   │   │   └── sdk-design.md        # paths: "sdk/**"
│   │   ├── bus/
│   │   │   └── event-schemas.md     # paths: "bus/**"
│   │   └── research/
│   │       └── research-workflow.md  # paths: "research/**"
│   ├── agents/
│   │   ├── scientist.md             # Research data analysis agent
│   │   └── schema-reviewer.md       # Reviews Pydantic schema changes
│   └── hooks/
│       └── post-implementation.sh   # Triggers research check
│
├── core/
│   ├── CLAUDE.md
│   ├── reflex/
│   │   ├── engine.py
│   │   ├── Dockerfile
│   │   └── tests/
│   ├── triggers/                    # Phase 2
│   ├── conscious/                   # Phase 3
│   ├── voice/                       # Phase 3
│   ├── memory/
│   │   ├── preferences/
│   │   │   ├── lighting.md
│   │   │   ├── media.md
│   │   │   └── routines.md
│   │   └── scratchpad.md
│   └── librarian/                   # Phase 3
│
├── domains/
│   ├── CLAUDE.md
│   ├── home/
│   │   └── home_agent.py
│   └── media/                       # Phase 2
│       └── media_agent.py
│
├── bus/
│   ├── CLAUDE.md
│   ├── schemas/
│   │   └── events.py               # Canonical Pydantic event types
│   ├── bridge.py                    # MQTT ↔ Redis Streams
│   └── Dockerfile
│
├── sdk/
│   ├── CLAUDE.md
│   ├── pyproject.toml
│   └── alfred_sdk/
│       ├── client.py
│       ├── events.py
│       ├── mcp.py
│       └── telemetry.py
│
├── telemetry/
│   ├── collector.py                 # Aggregates to research vault
│   └── schemas.py
│
├── research/                        # Obsidian Vault (see Research Vault note below)
│   ├── .obsidian/                   # Gitignored
│   ├── experiments/
│   │   └── EXP-001-reflex-latency.md
│   ├── data/
│   │   └── reflex-latency/
│   │       ├── raw.csv
│   │       └── summary.md
│   ├── paper/
│   │   ├── 00-outline.md
│   │   ├── 01-introduction.md
│   │   ├── 02-architecture.md
│   │   └── 03-results.md
│   ├── patents/
│   └── daily/
│
└── shared/
    └── config.py                    # Shared configuration loader (env vars, .env parsing)
```

**Research Vault:** The `research/` directory is a regular directory within the Alfred monorepo, git-tracked. Research data (CSVs, daily notes, paper drafts) are committed to the repo. Obsidian reads it by adding the repo's `research/` folder as a vault (Obsidian supports opening any folder). The `telemetry/collector.py` service accesses `research/` via a Docker volume mount to the host path (configured via `RESEARCH_VAULT_PATH` env var in `.env`). `.obsidian/` config is gitignored.

### CLAUDE.md Structure

Root `CLAUDE.md` uses `@` imports to reference rule files:

```markdown
# Project Alfred

## Identity
You have two roles: Lead Engineer + Background Scientist.

## The Four Pillars (NON-NEGOTIABLE)
@.claude/rules/architecture.md

## Code Conventions
@.claude/rules/python-conventions.md

## Research Protocol
@.claude/rules/research-protocol.md

## Stack
- Python 3.12+, async-first, Pydantic v2
- OpenTelemetry → SigNoz
- Docker Compose, one Dockerfile per service
- MQTT (edge) + Redis Streams (internal)
- Ollama for local SLM inference
```

Path-scoped rules load on demand when working in matching directories. Subdirectory CLAUDE.md files provide per-subsystem context.

---

## 4. Telemetry & Instrumentation

### Two Consumers of the Same Data

1. **SigNoz** — Real-time operational observability (dashboards, distributed traces, alerting). Open-source, self-hosted.
2. **Research Collector** — Writes CSVs + daily Markdown summaries to the Obsidian vault for the academic pipeline.

### OpenTelemetry Integration

All instrumentation uses the OpenTelemetry SDK. SigNoz receives traces/metrics via OTLP. The SDK decorators create OTel spans with custom attributes AND emit to the research pipeline.

### Three Decorators (in alfred-sdk)

```python
@track_latency(category="reflex")
# Records: timestamp, function, duration_ms, category

@track_tokens(model="llama3-8b")
# Records: timestamp, model, prompt_tokens, completion_tokens, inference_ms

@track_event(bus="redis")
# Records: timestamp, bus, topic, payload_bytes, pub_latency_ms
```

### Research Vault Output

```
research/data/reflex-latency/
├── raw.csv              # Append-only telemetry
└── summary.md           # Auto-generated p50/p95/p99 tables

research/daily/
└── 2026-03-10.md        # Auto-generated daily research note
```

### Evals Framework (Phased)

The Reflex Engine is designed for eval-ability from day one: structured input, structured output, no side effects during inference.

- **Phase 1:** Eval contract established — Reflex Engine takes structured `(event, preferences)` input and returns structured `action` output, making it testable in isolation
- **Phase 2:** First eval runner — define scenario files `{event, preferences, expected_action}`, run against Reflex Engine, score pass/fail/partial. Results flow to both SigNoz and research vault
- **Phase 4:** Full eval harness — automated regression suites, cross-domain scenario coverage, accuracy tracking over time

---

## 5. Hardware & Deployment

- **Primary server:** CachyOS PC — RTX 4090 + 64GB RAM + 5800X3D. Runs all Docker containers + Ollama with GPU acceleration.
- **Development machine:** M4 Max MacBook Pro, 128GB unified memory. Can run heavier models.
- **Deployment:** Docker Compose for now. Architecture supports future distribution across machines.
- **Smart Home:** Home Assistant as integration layer.

---

## 6. Phased Delivery

### Phase 1: Foundation + One Real Signal
1. Repository scaffolding + CLAUDE.md hierarchy
2. Bus contracts (Pydantic event schemas)
3. Event Bus infrastructure (Mosquitto + Redis + bridge)
4. Telemetry skeleton (OTel + SigNoz + decorators + collector)
5. alfred-sdk (minimal — connect, register, instrument)
6. Home Service (separate repo — thin HA wrapper using SDK)
7. Home domain sub-agent (`domains/home/home_agent.py` — routes Reflex Engine actions to home-service)
8. Reflex Engine + hand-written preferences
9. EXP-001: Reflex Latency experiment

**Deliverable:** SLM processes real HA event → context-aware action in <500ms. First publishable data.

### Phase 2: Cross-Domain Orchestration + Triggers
- Media domain (Emby microservice + media_agent)
- Canonical demo: "TV on → lights dim" across two domains
- Trigger Engine — LLM-created scheduled + event-conditional triggers
- Evals runner — scenario-based testing of Reflex Engine accuracy

**Deliverable:** Proves decoupled cross-domain orchestration. Stronger academic story.

### Phase 3: Autonomous Memory + Reasoning
- Librarian Agent (nightly scratchpad → core preferences)
- Conscious Engine (System 2 — cloud LLM for complex reasoning)
- Voice I/O (Whisper STT + configurable TTS)
- Autonomous preference inference

**Deliverable:** Full System 1 + System 2 architecture. Librarian context compaction data.

### Phase 4: Scale & Polish
- Full evals harness (automated regression, cross-domain scenarios)
- Additional domains (finance, security, social, health)
- Distributed compute across PC + MacBook
- Activation channels (wake word, iMessage, Telegram, Signal)
- Patent filing + paper submission

---

## 7. Technology Stack

| Concern | Choice | Rationale |
|---------|--------|-----------|
| Language | Python 3.12+, async-first | Ecosystem, LLM tooling |
| Schemas | Pydantic v2 | Deterministic comms (Pillar 3) |
| Event Bus (edge) | MQTT / Mosquitto | Native HA/IoT protocol |
| Event Bus (internal) | Redis Streams | Consumer groups, replay, persistence |
| Local SLM | Ollama (Llama 3 8B) | GPU-accelerated on RTX 4090 |
| Cloud LLM | Claude | Complex reasoning (System 2) |
| Orchestration | MCP (Model Context Protocol) | Standardized tool interface |
| Observability | OpenTelemetry → SigNoz | Open-source, self-hosted, distributed tracing |
| Containers | Docker Compose | Per-service Dockerfiles |
| Memory | Markdown + YAML frontmatter | Human-readable, LLM-friendly, git-tracked |
| Research | Obsidian vault (symlinked) | Browsable, interconnected notes |

---

## 8. Key Constraints & Decisions

- **No natural language between agents.** All inter-agent payloads are Pydantic-validated JSON.
- **Apps are sovereign.** Every microservice works without Alfred. SDK is optional.
- **No hardcoded rules.** The Reflex Engine reads plain-text preferences, never IF/THEN logic.
- **Scratchpad only at runtime.** Core preference files are never edited during runtime — only by the Librarian Agent or by hand.
- **Minimize costs.** Local-first inference. Cloud LLM only for System 2 reasoning.
- **Instrument from day one.** Every function that touches inference or the bus gets telemetry decorators.
