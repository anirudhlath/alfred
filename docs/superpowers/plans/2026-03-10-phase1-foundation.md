# Phase 1: Foundation + One Real Signal — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove an SLM can process a real Home Assistant event and execute a context-aware action in <500ms, with zero hardcoded rules, guided only by plain-text Markdown preferences.

**Architecture:** Alfred monorepo with Event Bus (MQTT + Redis Streams), a Python SDK for microservice integration, a separate Home Service repo wrapping HA, and a Reflex Engine powered by Ollama. Telemetry via OpenTelemetry → SigNoz + research vault.

**Tech Stack:** Python 3.12+, Pydantic v2, Redis Streams, Mosquitto MQTT, Ollama, OpenTelemetry, SigNoz, Docker Compose

**Spec:** `docs/superpowers/specs/2026-03-10-project-alfred-design.md`

---

## Chunk 1: Workspace Restructure + Scaffolding + CLAUDE.md

### Task 1: Restructure Workspace

The current git repo is at the workspace root. Per the spec, the workspace root should NOT be a git repo — `alfred/` should be a subfolder with its own git init.

**Files:**
- Move: `docs/` → `alfred/docs/`
- Move: `.gitignore` → `alfred/.gitignore`
- Remove: `.git/` from workspace root
- Create: `alfred/.git` (new repo)

- [ ] **Step 1: Create alfred/ subdirectory and move existing files**

```bash
mkdir -p alfred
mv docs alfred/
mv .gitignore alfred/
```

- [ ] **Step 2: Remove workspace-level git repo**

```bash
rm -rf .git
```

- [ ] **Step 3: Initialize git in alfred/**

```bash
cd alfred && git init && git add -A && git commit -m "Initial commit: design spec and plans"
```

- [ ] **Step 4: Move .claude/ settings into workspace root (not the repo)**

The workspace root keeps `.claude/` for Claude Code config. The `alfred/` repo gets its own `.claude/` for project-specific rules.

```bash
cd .. # back to workspace root
# .claude/ already exists at workspace root — keep it
```

---

### Task 2: Scaffold Alfred Monorepo Directory Structure

**Files:**
- Create: `alfred/core/__init__.py`
- Create: `alfred/core/reflex/__init__.py`
- Create: `alfred/core/memory/preferences/.gitkeep`
- Create: `alfred/core/memory/scratchpad.md`
- Create: `alfred/domains/__init__.py`
- Create: `alfred/domains/home/__init__.py`
- Create: `alfred/bus/__init__.py`
- Create: `alfred/bus/schemas/__init__.py`
- Create: `alfred/sdk/alfred_sdk/__init__.py`
- Create: `alfred/sdk/pyproject.toml`
- Create: `alfred/telemetry/__init__.py`
- Create: `alfred/shared/__init__.py`
- Create: `alfred/shared/config.py`
- Create: `alfred/research/experiments/.gitkeep`
- Create: `alfred/research/data/.gitkeep`
- Create: `alfred/research/paper/.gitkeep`
- Create: `alfred/research/patents/.gitkeep`
- Create: `alfred/research/daily/.gitkeep`
- Create: `alfred/.env.example`
- Create: `alfred/pyproject.toml` (root project)
- Create: `alfred/docker-compose.yml` (skeleton)

- [ ] **Step 1: Create all directories with __init__.py files**

```bash
cd alfred

# Core
mkdir -p core/reflex/tests core/memory/preferences core/triggers core/conscious core/voice core/librarian
touch core/__init__.py core/reflex/__init__.py core/reflex/tests/__init__.py

# Domains
mkdir -p domains/home domains/media
touch domains/__init__.py domains/home/__init__.py

# Bus
mkdir -p bus/schemas
touch bus/__init__.py bus/schemas/__init__.py

# SDK
mkdir -p sdk/alfred_sdk sdk/tests
touch sdk/alfred_sdk/__init__.py sdk/tests/__init__.py

# Telemetry
mkdir -p telemetry
touch telemetry/__init__.py

# Shared
mkdir -p shared
touch shared/__init__.py

# Research vault
mkdir -p research/experiments research/data research/paper research/patents research/daily
```

- [ ] **Step 2: Create placeholder files**

`alfred/core/memory/scratchpad.md`:
```markdown
---
last_drain: null
---
# Scratchpad
<!-- Ephemeral observations appended by the system. Drained nightly by the Librarian. -->
```

`alfred/core/memory/preferences/lighting.md`:
```markdown
---
domain: home
updated: 2026-03-10
confidence: manual
---
# Lighting Preferences

- I prefer dim lighting when watching TV or movies
- Default brightness during daytime: 80%
- Default brightness in the evening: 40%
- When I go to bed, all lights should turn off
```

`alfred/core/memory/preferences/media.md`:
```markdown
---
domain: media
updated: 2026-03-10
confidence: manual
---
# Media Preferences

- I usually watch content in the living room
- When media starts playing, I prefer minimal interruptions
```

`alfred/core/memory/preferences/routines.md`:
```markdown
---
domain: general
updated: 2026-03-10
confidence: manual
---
# Routines

- Weekday mornings: lights to 80%, no media
- Evenings after 8pm: dim lights, TV likely
- Bedtime around 11pm: everything off
```

- [ ] **Step 3: Create .env.example**

`alfred/.env.example`:
```env
# Redis
REDIS_HOST=localhost
REDIS_PORT=6379

# MQTT
MQTT_HOST=localhost
MQTT_PORT=1883

# Ollama
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3:8b

# Home Assistant
HA_HOST=http://homeassistant.local:8123
HA_TOKEN=your_long_lived_access_token

# Telemetry
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
SIGNOZ_ENABLED=true

# Research vault path (for Docker volume mount)
RESEARCH_VAULT_PATH=./research
```

- [ ] **Step 4: Create root pyproject.toml**

`alfred/pyproject.toml`:
```toml
[project]
name = "alfred"
version = "0.1.0"
description = "Ambient Multi-Agent System for Smart Environments"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.0",
    "redis>=5.0",
    "asyncio-mqtt>=0.16",
    "opentelemetry-api>=1.20",
    "opentelemetry-sdk>=1.20",
    "opentelemetry-exporter-otlp>=1.20",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
    "ruff>=0.4",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["core", "bus", "domains", "telemetry", "sdk", "tests"]

[tool.ruff]
target-version = "py312"
line-length = 100
```

- [ ] **Step 5: Create docker-compose.yml skeleton**

`alfred/docker-compose.yml`:
```yaml
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

  mosquitto:
    image: eclipse-mosquitto:2
    ports:
      - "1883:1883"
    volumes:
      - ./infra/mosquitto.conf:/mosquitto/config/mosquitto.conf

  # SigNoz (OpenTelemetry collector + UI)
  # Uses the all-in-one Docker image for dev
  signoz:
    image: signoz/signoz:latest
    ports:
      - "3301:3301"   # UI
      - "4317:4317"   # OTLP gRPC
      - "4318:4318"   # OTLP HTTP
    environment:
      - SIGNOZ_LOCAL_DB_PATH=/var/lib/signoz

  # bridge:
  #   build: ./bus
  #   depends_on: [redis, mosquitto]
  #   env_file: .env

  # reflex:
  #   build: ./core/reflex
  #   depends_on: [redis]
  #   env_file: .env

volumes:
  redis_data:
```

- [ ] **Step 6: Create mosquitto config**

```bash
mkdir -p infra
```

`alfred/infra/mosquitto.conf`:
```
listener 1883
allow_anonymous true
persistence true
persistence_location /mosquitto/data/
log_dest stdout
```

- [ ] **Step 7: Update .gitignore**

`alfred/.gitignore`:
```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
.venv/
venv/

# Environment
.env

# IDE
.vscode/
.idea/

# Obsidian vault config (research data itself is tracked)
research/.obsidian/

# Superpowers brainstorm sessions
.superpowers/

# Claude Code local settings
.claude/settings.local.json
```

- [ ] **Step 8: Commit scaffolding**

```bash
git add -A
git commit -m "Scaffold monorepo directory structure, Docker Compose, and preference files"
```

---

### Task 3: Create CLAUDE.md Hierarchy

**Files:**
- Create: `alfred/CLAUDE.md`
- Create: `alfred/.claude/rules/architecture.md`
- Create: `alfred/.claude/rules/python-conventions.md`
- Create: `alfred/.claude/rules/research-protocol.md`
- Create: `alfred/.claude/rules/core/reflex-engine.md`
- Create: `alfred/.claude/rules/core/memory-system.md`
- Create: `alfred/.claude/rules/domains/domain-conventions.md`
- Create: `alfred/.claude/rules/sdk/sdk-design.md`
- Create: `alfred/.claude/rules/bus/event-schemas.md`
- Create: `alfred/.claude/rules/research/research-workflow.md`
- Create: `alfred/.claude/agents/scientist.md`
- Create: `alfred/.claude/agents/schema-reviewer.md`
- Create: `alfred/core/CLAUDE.md`
- Create: `alfred/domains/CLAUDE.md`
- Create: `alfred/bus/CLAUDE.md`
- Create: `alfred/sdk/CLAUDE.md`

- [ ] **Step 1: Create root CLAUDE.md**

`alfred/CLAUDE.md`:
```markdown
# Project Alfred

An ambient, voice-first, decoupled Multi-Agent System for smart environments.

## Your Dual Role

You are both **Lead Engineer** and **Background Research Scientist** on this project.

- As Engineer: build, review, maintain code quality
- As Scientist: instrument telemetry, observe results, update research vault

## The Four Pillars (NON-NEGOTIABLE)

@.claude/rules/architecture.md

## Code Conventions

@.claude/rules/python-conventions.md

## Research Protocol

@.claude/rules/research-protocol.md

## Tech Stack

- Python 3.12+, async-first, Pydantic v2
- OpenTelemetry → SigNoz for observability
- Docker Compose, one Dockerfile per service
- MQTT (edge) + Redis Streams (internal backbone)
- Ollama for local SLM inference (Llama 3 8B)
- alfred-sdk is the ONLY coupling to external apps

## Key Paths

- `bus/schemas/events.py` — canonical event types (single source of truth)
- `core/memory/preferences/` — Markdown preference files (read-only at runtime)
- `core/memory/scratchpad.md` — ephemeral observations (append-only at runtime)
- `sdk/` — publishable alfred-sdk package
- `research/` — Obsidian vault with experiments, data, paper drafts

## Spec

See `docs/superpowers/specs/2026-03-10-project-alfred-design.md` for full architecture.
```

- [ ] **Step 2: Create architecture rules**

`alfred/.claude/rules/architecture.md`:
```markdown
# Architecture Rules — The Four Pillars

These are non-negotiable constraints. Every design decision must respect them.

## 1. Proactivity & Dynamic Triggers
- Triggers are created dynamically by the LLM at runtime
- Never hardcode scheduled tasks, cron jobs, or IF/THEN rules
- The Trigger Engine evaluates conditions against the Event Bus and time

## 2. Decoupled Domains
- Microservices are sovereign applications in their own repos
- They work independently without Alfred
- alfred-sdk is the ONLY bridge — apps never import from alfred/ directly
- Sub-agents in domains/ are Alfred's internal staff, not external apps
- Registration is runtime discovery via client.register()

## 3. Deterministic Communication
- All inter-agent messages are Pydantic-validated JSON
- No natural language between agents — EVER
- Alfred is a router and synthesizer, not a chat participant
- Every MCP tool call and Event Bus message has a typed schema in bus/schemas/

## 4. Stateful Memory (Librarian Pattern)
- Core preferences in Markdown + YAML frontmatter (core/memory/preferences/)
- Real-time writes go to scratchpad.md ONLY (via Redis List → async writer)
- Core preference files are NEVER edited during runtime
- The Librarian Agent consolidates nightly (Phase 3)
```

- [ ] **Step 3: Create python-conventions rules**

`alfred/.claude/rules/python-conventions.md`:
```markdown
# Python Conventions

- Python 3.12+, use modern syntax (match/case, type unions with |, etc.)
- Async-first: use async/await for all I/O operations
- Pydantic v2 for all data models and schemas
- Type hints on all function signatures
- Ruff for linting and formatting (line-length 100)
- pytest + pytest-asyncio for testing
- No relative imports across top-level packages (core, bus, domains, sdk)
- Keep files focused — one clear responsibility per module
- Decorators for cross-cutting concerns (telemetry, validation)
```

- [ ] **Step 4: Create research-protocol rules**

`alfred/.claude/rules/research-protocol.md`:
```markdown
# Research Protocol

After completing ANY implementation task that touches telemetry-producing code:

1. Check research/data/ for new or updated CSVs
2. If new data exists:
   - Update or create research/daily/{YYYY-MM-DD}.md with summary statistics
   - Compute p50/p95/p99 latencies where applicable
   - Note token usage, event throughput, and anomalies
3. If a milestone is reached (new capability proven, latency target hit):
   - Create or update an experiment log in research/experiments/EXP-NNN-*.md
   - Use the format: Hypothesis, Method, Results, Analysis
4. Weekly: review research/paper/ sections and flag which need updating

## Data Format
- CSVs in research/data/: append-only, never overwrite
- Daily notes in research/daily/: auto-generated Markdown
- Experiment logs: formal structure (hypothesis → results → analysis)
```

- [ ] **Step 5: Create path-scoped rules**

`alfred/.claude/rules/core/reflex-engine.md`:
```markdown
---
paths:
  - "core/reflex/**"
---

# Reflex Engine Rules

The Reflex Engine (System 1) is the fast-path SLM that processes events.

- MUST be eval-able: structured (event, preferences) in → structured action out
- No side effects during inference — side effects happen when the action is executed
- Reads preferences from core/memory/preferences/ (read-only)
- Appends observations to scratchpad via Redis List (never direct file write)
- Target latency: sub-500ms event → action
- All inference calls MUST use @track_latency and @track_tokens decorators
- Never call the cloud LLM (System 2) from the reflex path
- Uses Ollama for local inference — model configured via OLLAMA_MODEL env var
```

`alfred/.claude/rules/core/memory-system.md`:
```markdown
---
paths:
  - "core/memory/**"
---

# Memory System Rules

- Preference files use Markdown with YAML frontmatter
- Frontmatter fields: domain, updated, confidence (manual|inferred|librarian)
- Files are read-only at runtime — only the Librarian or humans edit them
- Scratchpad writes are serialized: components push to Redis List, a single async writer drains to scratchpad.md
- Scratchpad entries are timestamped and tagged with source component
```

`alfred/.claude/rules/domains/domain-conventions.md`:
```markdown
---
paths:
  - "domains/**"
---

# Domain Sub-Agent Conventions

- Each domain is an organizational boundary (home, media, security, etc.)
- Sub-agents within a domain subscribe to the Event Bus for relevant topics
- Sub-agents translate high-level actions into microservice-specific MCP tool calls
- All communication uses Pydantic-validated JSON payloads (Pillar 3)
- Sub-agents escalate anomalies to Alfred core via typed escalation events
- Sub-agents maintain domain-specific state if needed
```

`alfred/.claude/rules/sdk/sdk-design.md`:
```markdown
---
paths:
  - "sdk/**"
---

# SDK Design Rules

- alfred-sdk is a publishable Python package — keep dependencies minimal
- It is the ONLY coupling between Alfred and external apps
- Core exports: AlfredClient, @mcp_tool, @publish, @subscribe, telemetry decorators
- Apps install it as an optional dependency
- The SDK must work standalone — no imports from alfred core, bus, or domains
- Registration via client.register() announces tool manifests to Redis registry
- MCP transport is HTTP (JSON-RPC) between networked containers
```

`alfred/.claude/rules/bus/event-schemas.md`:
```markdown
---
paths:
  - "bus/**"
---

# Event Bus Rules

- bus/schemas/events.py is the SINGLE SOURCE OF TRUTH for all event types
- All events are Pydantic v2 BaseModel subclasses
- Events are immutable once published — never mutate after creation
- MQTT topics map to Redis Stream keys via the bridge
- The bridge is a thin forwarder — no business logic
- Use consumer groups in Redis Streams for load balancing
```

`alfred/.claude/rules/research/research-workflow.md`:
```markdown
---
paths:
  - "research/**"
---

# Research Vault Workflow

- research/ is git-tracked. Commit data and notes regularly.
- .obsidian/ is gitignored (per-machine config)
- CSVs in data/ are append-only — never overwrite historical data
- Daily notes follow format: date, events processed, latency stats, token usage, anomalies
- Experiments use formal structure: EXP-NNN, hypothesis, method, results, analysis
- Paper sections in paper/ are iteratively drafted as data accumulates
```

- [ ] **Step 6: Create sub-agent definitions**

`alfred/.claude/agents/scientist.md`:
```markdown
---
name: scientist
description: Analyze telemetry data and update research vault
tools: Read, Glob, Grep, Write, Edit, Bash
model: sonnet
---

You are the Background Research Scientist for Project Alfred.

When invoked:
1. Read research/data/**/*.csv for new telemetry data
2. Compute summary statistics (mean, p50, p95, p99 latencies; token totals)
3. Update or create research/daily/{YYYY-MM-DD}.md with findings
4. Check if experiment thresholds are met (e.g., reflex < 500ms consistently)
5. If so, update the relevant research/experiments/EXP-*.md
6. Flag any paper sections in research/paper/ that need revision

Output a structured summary of what was updated and key findings.
```

`alfred/.claude/agents/schema-reviewer.md`:
```markdown
---
name: schema-reviewer
description: Review Pydantic schema changes for backward compatibility
tools: Read, Glob, Grep
model: sonnet
---

You review changes to bus/schemas/events.py and sdk/alfred_sdk/events.py.

Check for:
1. Backward-incompatible field removals or renames
2. Required fields added without defaults
3. Type changes that break existing consumers
4. Missing or inconsistent field descriptions
5. Schema drift between bus/schemas/ and sdk/ copies

Output: list of issues found, or "No issues — schemas are compatible."
```

- [ ] **Step 7: Create subdirectory CLAUDE.md files**

`alfred/core/CLAUDE.md`:
```markdown
# Core — Alfred OS

This directory contains Alfred's brain:
- `reflex/` — System 1 SLM engine (fast event → action loop)
- `memory/` — Markdown preferences + scratchpad
- `triggers/` — Dynamic trigger engine (Phase 2)
- `conscious/` — System 2 cloud LLM (Phase 3)
- `voice/` — Voice I/O adapters (Phase 3)
- `librarian/` — Nightly preference consolidation (Phase 3)

See path-scoped rules in .claude/rules/core/ for component-specific constraints.
```

`alfred/domains/CLAUDE.md`:
```markdown
# Domains

Organizational boundaries. Each domain has sub-agents that manage its concerns.

- `home/` — Smart home domain (Phase 1: home_agent.py)
- `media/` — Media domain (Phase 2: media_agent.py)

Sub-agents are Alfred's internal staff. They route actions to external microservices via MCP tool calls. All communication is Pydantic-validated JSON.
```

`alfred/bus/CLAUDE.md`:
```markdown
# Event Bus

- `schemas/events.py` — Single source of truth for all event types
- `bridge.py` — MQTT ↔ Redis Streams forwarder (no business logic)

MQTT is the edge layer (HA/devices). Redis Streams is the internal backbone.
```

`alfred/sdk/CLAUDE.md`:
```markdown
# alfred-sdk

Publishable Python package. The ONLY coupling between Alfred and external apps.

- Must work standalone — no imports from alfred core, bus, or domains
- Keep dependencies minimal
- Core: AlfredClient, @mcp_tool, @publish, @subscribe, telemetry decorators
```

- [ ] **Step 8: Commit CLAUDE.md hierarchy**

```bash
git add -A
git commit -m "Add CLAUDE.md instruction hierarchy with rules, agents, and scoped conventions"
```

---

## Chunk 2: Bus Contracts (Pydantic Event Schemas)

### Task 4: Define Canonical Event Schemas

**Files:**
- Create: `alfred/bus/schemas/events.py`
- Test: `alfred/bus/schemas/tests/__init__.py`
- Test: `alfred/bus/schemas/tests/test_events.py`

- [ ] **Step 1: Write failing tests for event schemas**

`alfred/bus/schemas/tests/test_events.py`:
```python
"""Tests for canonical event schemas."""
import pytest
from datetime import datetime, timezone


def test_state_changed_event_creation():
    from bus.schemas.events import StateChangedEvent

    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="light.living_room",
        old_state="on",
        new_state="off",
        attributes={"brightness": 0},
    )
    assert event.source == "home-service"
    assert event.domain == "home"
    assert event.entity_id == "light.living_room"
    assert event.event_type == "state_changed"
    assert event.timestamp is not None


def test_state_changed_event_rejects_missing_fields():
    from bus.schemas.events import StateChangedEvent

    with pytest.raises(Exception):
        StateChangedEvent(source="home-service")  # missing required fields


def test_action_request_creation():
    from bus.schemas.events import ActionRequest

    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="smart_home.dim_lights",
        parameters={"room": "living_room", "level": 20},
    )
    assert action.event_type == "action_request"
    assert action.tool_name == "smart_home.dim_lights"
    assert action.parameters["level"] == 20


def test_action_result_success():
    from bus.schemas.events import ActionResult

    result = ActionResult(
        source="home-service",
        request_id="req-123",
        tool_name="smart_home.dim_lights",
        status="success",
        result={"brightness": 20},
    )
    assert result.status == "success"
    assert result.error is None


def test_action_result_failure():
    from bus.schemas.events import ActionResult

    result = ActionResult(
        source="home-service",
        request_id="req-123",
        tool_name="smart_home.dim_lights",
        status="error",
        error="Device unreachable",
    )
    assert result.status == "error"
    assert result.error == "Device unreachable"


def test_telemetry_event_creation():
    from bus.schemas.events import TelemetryEvent

    event = TelemetryEvent(
        source="reflex-engine",
        metric_type="latency",
        category="reflex",
        value=142.5,
        unit="ms",
        metadata={"function": "process_event", "model": "llama3:8b"},
    )
    assert event.event_type == "telemetry"
    assert event.value == 142.5


def test_tool_registration_event():
    from bus.schemas.events import ToolRegistration

    reg = ToolRegistration(
        source="home-service",
        service_name="home-service",
        service_endpoint="http://home-service:8000/mcp",
        tools=[
            {
                "name": "smart_home.dim_lights",
                "description": "Dim lights to a level",
                "parameters": {
                    "room": {"type": "string"},
                    "level": {"type": "integer", "minimum": 0, "maximum": 100},
                },
            }
        ],
    )
    assert reg.event_type == "tool_registration"
    assert len(reg.tools) == 1
    assert reg.tools[0]["name"] == "smart_home.dim_lights"


def test_event_serialization_roundtrip():
    from bus.schemas.events import StateChangedEvent

    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="light.living_room",
        old_state="on",
        new_state="off",
    )
    json_str = event.model_dump_json()
    restored = StateChangedEvent.model_validate_json(json_str)
    assert restored.entity_id == event.entity_id
    assert restored.timestamp == event.timestamp


def test_base_event_id_uniqueness():
    from bus.schemas.events import StateChangedEvent

    e1 = StateChangedEvent(
        source="a", domain="home", entity_id="x", old_state="on", new_state="off"
    )
    e2 = StateChangedEvent(
        source="a", domain="home", entity_id="x", old_state="on", new_state="off"
    )
    assert e1.event_id != e2.event_id
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd alfred && python -m pytest bus/schemas/tests/test_events.py -v
```
Expected: FAIL (ModuleNotFoundError: No module named 'bus.schemas.events')

- [ ] **Step 3: Implement event schemas**

Create `alfred/bus/schemas/tests/__init__.py` (empty).

`alfred/bus/schemas/events.py`:
```python
"""Canonical event schemas for the Alfred Event Bus.

This is the SINGLE SOURCE OF TRUTH for all event types flowing through
MQTT and Redis Streams. All inter-agent communication uses these schemas.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class BaseEvent(BaseModel):
    """Base for all events on the Alfred Event Bus."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = Field(description="Service or component that produced this event")


class StateChangedEvent(BaseEvent):
    """A device or entity changed state. Published by microservices."""

    event_type: str = "state_changed"
    domain: str = Field(description="Domain: home, media, finance, etc.")
    entity_id: str = Field(description="Unique entity identifier, e.g. light.living_room")
    old_state: str | None = None
    new_state: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class ActionRequest(BaseEvent):
    """A request to execute an MCP tool on a microservice."""

    event_type: str = "action_request"
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    target_service: str = Field(description="Which microservice should handle this")
    tool_name: str = Field(description="MCP tool name, e.g. smart_home.dim_lights")
    parameters: dict[str, Any] = Field(default_factory=dict)


class ActionResult(BaseEvent):
    """Result of an MCP tool execution."""

    event_type: str = "action_result"
    request_id: str
    tool_name: str
    status: str = Field(description="success | error")
    result: dict[str, Any] | None = None
    error: str | None = None


class TelemetryEvent(BaseEvent):
    """Telemetry metric for observability and research."""

    event_type: str = "telemetry"
    metric_type: str = Field(description="latency | tokens | event_throughput")
    category: str = Field(description="reflex | bus | inference | etc.")
    value: float
    unit: str = Field(description="ms | tokens | bytes | count")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolRegistration(BaseEvent):
    """A microservice registering its MCP tool capabilities."""

    event_type: str = "tool_registration"
    service_name: str
    service_endpoint: str = Field(description="HTTP endpoint for MCP calls")
    tools: list[dict[str, Any]] = Field(description="List of tool manifests")


class TriggerCreated(BaseEvent):
    """The LLM dynamically created a trigger (Phase 2)."""

    event_type: str = "trigger_created"
    trigger_id: str = Field(default_factory=lambda: str(uuid4()))
    trigger_type: str = Field(description="scheduled | event_conditional | composite")
    conditions: dict[str, Any] = Field(description="Conditions that must be met to fire")
    action: ActionRequest = Field(description="Action to execute when conditions are met")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd alfred && python -m pytest bus/schemas/tests/test_events.py -v
```
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add bus/schemas/
git commit -m "Add canonical Pydantic event schemas for Event Bus"
```

---

## Chunk 3: Event Bus Infrastructure

### Task 5: Implement MQTT ↔ Redis Streams Bridge

**Files:**
- Create: `alfred/bus/bridge.py`
- Create: `alfred/bus/redis_client.py`
- Create: `alfred/bus/mqtt_client.py`
- Test: `alfred/bus/tests/__init__.py`
- Test: `alfred/bus/tests/test_bridge.py`
- Create: `alfred/bus/Dockerfile`

- [ ] **Step 1: Write failing tests for the bridge**

`alfred/bus/tests/test_bridge.py`:
```python
"""Tests for MQTT ↔ Redis Streams bridge.

Uses mocked MQTT and Redis to test message forwarding without real services.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from bus.schemas.events import StateChangedEvent


@pytest.fixture
def sample_state_event() -> StateChangedEvent:
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="light.living_room",
        old_state="on",
        new_state="off",
        attributes={"brightness": 0},
    )


def test_mqtt_topic_to_redis_stream_key():
    from bus.bridge import mqtt_topic_to_stream_key

    assert mqtt_topic_to_stream_key("home/state_changed") == "alfred:home:state_changed"
    assert mqtt_topic_to_stream_key("media/playback") == "alfred:media:playback"


def test_redis_stream_key_to_mqtt_topic():
    from bus.bridge import stream_key_to_mqtt_topic

    assert stream_key_to_mqtt_topic("alfred:home:state_changed") == "home/state_changed"
    assert stream_key_to_mqtt_topic("alfred:media:playback") == "media/playback"


@pytest.mark.asyncio
async def test_forward_mqtt_to_redis(sample_state_event):
    from bus.bridge import forward_mqtt_to_redis

    mock_redis = AsyncMock()
    payload = sample_state_event.model_dump_json().encode()

    await forward_mqtt_to_redis(
        redis=mock_redis,
        mqtt_topic="home/state_changed",
        payload=payload,
    )

    mock_redis.xadd.assert_called_once()
    call_args = mock_redis.xadd.call_args
    assert call_args[0][0] == "alfred:home:state_changed"


@pytest.mark.asyncio
async def test_forward_redis_to_mqtt():
    from bus.bridge import forward_redis_to_mqtt

    mock_mqtt = AsyncMock()
    event_data = {"event": json.dumps({"event_type": "action_request", "source": "reflex"})}

    await forward_redis_to_mqtt(
        mqtt=mock_mqtt,
        stream_key="alfred:home:command",
        event_data=event_data,
    )

    mock_mqtt.publish.assert_called_once()
    call_args = mock_mqtt.publish.call_args
    assert call_args[0][0] == "home/command"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd alfred && python -m pytest bus/tests/test_bridge.py -v
```
Expected: FAIL (No module named 'bus.bridge')

- [ ] **Step 3: Implement bridge module**

`alfred/bus/tests/__init__.py` (empty)

`alfred/bus/bridge.py`:
```python
"""MQTT ↔ Redis Streams bridge.

Thin forwarder — no business logic. Converts between MQTT topics and
Redis Stream keys using a simple naming convention:
  MQTT:  {domain}/{event_type}
  Redis: alfred:{domain}:{event_type}
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import redis.asyncio as aioredis
from asyncio_mqtt import Client as MqttClient

logger = logging.getLogger(__name__)

STREAM_PREFIX = "alfred"


def mqtt_topic_to_stream_key(topic: str) -> str:
    """Convert MQTT topic 'home/state_changed' → Redis stream 'alfred:home:state_changed'."""
    parts = topic.replace("/", ":")
    return f"{STREAM_PREFIX}:{parts}"


def stream_key_to_mqtt_topic(stream_key: str) -> str:
    """Convert Redis stream 'alfred:home:state_changed' → MQTT topic 'home/state_changed'."""
    without_prefix = stream_key.removeprefix(f"{STREAM_PREFIX}:")
    return without_prefix.replace(":", "/")


async def forward_mqtt_to_redis(
    redis: aioredis.Redis,
    mqtt_topic: str,
    payload: bytes,
) -> None:
    """Forward an MQTT message to the corresponding Redis Stream."""
    stream_key = mqtt_topic_to_stream_key(mqtt_topic)
    await redis.xadd(stream_key, {"event": payload.decode()})
    logger.debug("MQTT → Redis: %s → %s", mqtt_topic, stream_key)


async def forward_redis_to_mqtt(
    mqtt: MqttClient,
    stream_key: str,
    event_data: dict[str, Any],
) -> None:
    """Forward a Redis Stream entry to the corresponding MQTT topic."""
    topic = stream_key_to_mqtt_topic(stream_key)
    payload = event_data.get("event", "{}")
    await mqtt.publish(topic, payload.encode())
    logger.debug("Redis → MQTT: %s → %s", stream_key, topic)


async def run_bridge(
    redis_url: str = "redis://localhost:6379",
    mqtt_host: str = "localhost",
    mqtt_port: int = 1883,
    mqtt_topics: list[str] | None = None,
    redis_streams: list[str] | None = None,
) -> None:
    """Main bridge loop. Subscribes to MQTT topics and Redis Streams, forwarding between them."""
    if mqtt_topics is None:
        mqtt_topics = ["home/#", "media/#"]
    if redis_streams is None:
        redis_streams = ["alfred:reflex:actions"]

    redis = aioredis.from_url(redis_url)
    async with MqttClient(mqtt_host, mqtt_port) as mqtt:
        # Subscribe to MQTT topics
        for topic in mqtt_topics:
            await mqtt.subscribe(topic)
            logger.info("Subscribed to MQTT topic: %s", topic)

        # Run both directions concurrently
        await asyncio.gather(
            _mqtt_to_redis_loop(mqtt, redis),
            _redis_to_mqtt_loop(redis, mqtt, redis_streams),
        )


async def _mqtt_to_redis_loop(mqtt: MqttClient, redis: aioredis.Redis) -> None:
    """Listen for MQTT messages and forward to Redis."""
    async for message in mqtt.messages:
        await forward_mqtt_to_redis(
            redis=redis,
            mqtt_topic=str(message.topic),
            payload=message.payload,
        )


async def _redis_to_mqtt_loop(
    redis: aioredis.Redis,
    mqtt: MqttClient,
    streams: list[str],
) -> None:
    """Listen for Redis Stream entries and forward to MQTT."""
    last_ids = {s: "0" for s in streams}
    while True:
        results = await redis.xread(last_ids, block=1000)
        for stream_key, entries in results:
            stream_key = stream_key.decode() if isinstance(stream_key, bytes) else stream_key
            for entry_id, data in entries:
                decoded = {
                    k.decode() if isinstance(k, bytes) else k: v.decode()
                    if isinstance(v, bytes)
                    else v
                    for k, v in data.items()
                }
                await forward_redis_to_mqtt(mqtt, stream_key, decoded)
                last_ids[stream_key] = entry_id
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd alfred && python -m pytest bus/tests/test_bridge.py -v
```
Expected: All 4 tests PASS

- [ ] **Step 5: Create bridge Dockerfile**

`alfred/bus/Dockerfile`:
```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir .

COPY bus/ /app/bus/

CMD ["python", "-m", "bus.bridge"]
```

Note: The docker-compose.yml `bridge` service must set context to monorepo root:
```yaml
bridge:
  build:
    context: .
    dockerfile: bus/Dockerfile
```

- [ ] **Step 6: Add bridge __main__.py**

`alfred/bus/__main__.py`:
```python
"""Entry point for the MQTT ↔ Redis bridge service."""

import asyncio
import os
import logging

from bus.bridge import run_bridge

logging.basicConfig(level=logging.INFO)


def main():
    asyncio.run(
        run_bridge(
            redis_url=f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}",
            mqtt_host=os.getenv("MQTT_HOST", "localhost"),
            mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
        )
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Commit**

```bash
git add bus/
git commit -m "Add MQTT-Redis bridge with topic/stream key conversion"
```

---

## Chunk 4: Telemetry Decorators + alfred-sdk

### Task 6: Implement Telemetry Decorators

**Files:**
- Create: `alfred/sdk/alfred_sdk/telemetry.py`
- Test: `alfred/sdk/tests/test_telemetry.py`

- [ ] **Step 1: Write failing tests for telemetry decorators**

`alfred/sdk/tests/test_telemetry.py`:
```python
"""Tests for telemetry decorators."""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_track_latency_records_duration():
    from sdk.alfred_sdk.telemetry import track_latency, get_telemetry_buffer

    @track_latency(category="test")
    async def slow_function():
        await asyncio.sleep(0.05)
        return "done"

    result = await slow_function()
    assert result == "done"

    buffer = get_telemetry_buffer()
    assert len(buffer) >= 1
    entry = buffer[-1]
    assert entry["category"] == "test"
    assert entry["metric_type"] == "latency"
    assert entry["value"] >= 50  # at least 50ms
    assert entry["unit"] == "ms"
    assert entry["function"] == "slow_function"


@pytest.mark.asyncio
async def test_track_tokens_records_usage():
    from sdk.alfred_sdk.telemetry import track_tokens, get_telemetry_buffer

    @track_tokens(model="llama3:8b")
    async def mock_inference(prompt: str):
        return {
            "response": "dim the lights",
            "prompt_tokens": 150,
            "completion_tokens": 10,
            "total_tokens": 160,
        }

    result = await mock_inference("test prompt")
    assert result["response"] == "dim the lights"

    buffer = get_telemetry_buffer()
    token_entries = [e for e in buffer if e["metric_type"] == "tokens"]
    assert len(token_entries) >= 1
    entry = token_entries[-1]
    assert entry["model"] == "llama3:8b"
    assert entry["prompt_tokens"] == 150
    assert entry["completion_tokens"] == 10


@pytest.mark.asyncio
async def test_track_event_records_bus_metrics():
    from sdk.alfred_sdk.telemetry import track_event, get_telemetry_buffer

    @track_event(bus="redis")
    async def publish_something(topic: str, data: dict):
        return {"published": True}

    await publish_something("home/state", {"entity": "light"})

    buffer = get_telemetry_buffer()
    event_entries = [e for e in buffer if e["metric_type"] == "event_throughput"]
    assert len(event_entries) >= 1
    entry = event_entries[-1]
    assert entry["bus"] == "redis"


def test_track_latency_works_on_sync():
    from sdk.alfred_sdk.telemetry import track_latency, get_telemetry_buffer

    @track_latency(category="sync-test")
    def sync_function():
        return 42

    result = sync_function()
    assert result == 42

    buffer = get_telemetry_buffer()
    sync_entries = [e for e in buffer if e["category"] == "sync-test"]
    assert len(sync_entries) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd alfred && python -m pytest sdk/tests/test_telemetry.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement telemetry decorators**

`alfred/sdk/alfred_sdk/telemetry.py`:
```python
"""Telemetry decorators for frictionless instrumentation.

These decorators wrap functions to automatically record latency, token usage,
and event bus metrics. Data is buffered locally and can be flushed to
OpenTelemetry spans and/or the research vault collector.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import time
from datetime import datetime, timezone
from typing import Any, Callable

# Thread-safe telemetry buffer (in production, this flushes to Redis/OTel)
_telemetry_buffer: list[dict[str, Any]] = []


def get_telemetry_buffer() -> list[dict[str, Any]]:
    """Access the in-memory telemetry buffer. Primarily for testing."""
    return _telemetry_buffer


def clear_telemetry_buffer() -> None:
    """Clear the buffer. Primarily for testing."""
    _telemetry_buffer.clear()


def _record(entry: dict[str, Any]) -> None:
    """Record a telemetry entry to the buffer."""
    entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    _telemetry_buffer.append(entry)


def track_latency(category: str) -> Callable:
    """Decorator to track function execution latency in milliseconds."""

    def decorator(fn: Callable) -> Callable:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                start = time.perf_counter()
                result = await fn(*args, **kwargs)
                duration_ms = (time.perf_counter() - start) * 1000
                _record(
                    {
                        "metric_type": "latency",
                        "category": category,
                        "function": fn.__name__,
                        "value": duration_ms,
                        "unit": "ms",
                    }
                )
                return result

            return async_wrapper
        else:

            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs):
                start = time.perf_counter()
                result = fn(*args, **kwargs)
                duration_ms = (time.perf_counter() - start) * 1000
                _record(
                    {
                        "metric_type": "latency",
                        "category": category,
                        "function": fn.__name__,
                        "value": duration_ms,
                        "unit": "ms",
                    }
                )
                return result

            return sync_wrapper

    return decorator


def track_tokens(model: str) -> Callable:
    """Decorator to track LLM/SLM token usage.

    The decorated function must return a dict containing at least:
    - prompt_tokens: int
    - completion_tokens: int
    - total_tokens: int (optional, computed if missing)
    Plus any other return data.
    """

    def decorator(fn: Callable) -> Callable:

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            start = time.perf_counter()
            result = await fn(*args, **kwargs)
            duration_ms = (time.perf_counter() - start) * 1000

            if isinstance(result, dict):
                _record(
                    {
                        "metric_type": "tokens",
                        "model": model,
                        "function": fn.__name__,
                        "prompt_tokens": result.get("prompt_tokens", 0),
                        "completion_tokens": result.get("completion_tokens", 0),
                        "total_tokens": result.get(
                            "total_tokens",
                            result.get("prompt_tokens", 0)
                            + result.get("completion_tokens", 0),
                        ),
                        "inference_ms": duration_ms,
                        "unit": "tokens",
                    }
                )
            return result

        return wrapper

    return decorator


def track_event(bus: str) -> Callable:
    """Decorator to track event bus publish/subscribe metrics."""

    def decorator(fn: Callable) -> Callable:

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            start = time.perf_counter()
            result = await fn(*args, **kwargs)
            duration_ms = (time.perf_counter() - start) * 1000
            _record(
                {
                    "metric_type": "event_throughput",
                    "bus": bus,
                    "function": fn.__name__,
                    "value": duration_ms,
                    "unit": "ms",
                }
            )
            return result

        return wrapper

    return decorator
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd alfred && python -m pytest sdk/tests/test_telemetry.py -v
```
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add sdk/
git commit -m "Add telemetry decorators: track_latency, track_tokens, track_event"
```

---

### Task 7: Implement alfred-sdk Core (AlfredClient + MCP + Pub/Sub)

**Files:**
- Create: `alfred/sdk/alfred_sdk/client.py`
- Create: `alfred/sdk/alfred_sdk/mcp.py`
- Create: `alfred/sdk/alfred_sdk/events.py`
- Modify: `alfred/sdk/alfred_sdk/__init__.py`
- Create: `alfred/sdk/pyproject.toml`
- Test: `alfred/sdk/tests/test_client.py`
- Test: `alfred/sdk/tests/test_mcp.py`

- [ ] **Step 1: Write failing tests for SDK core**

`alfred/sdk/tests/test_client.py`:
```python
"""Tests for AlfredClient."""

import pytest
from unittest.mock import AsyncMock, patch


def test_client_stores_config():
    from sdk.alfred_sdk.client import AlfredClient

    client = AlfredClient(
        redis_url="redis://testhost:6379",
        mqtt_host="mqtthost",
        service_name="test-service",
    )
    assert client.service_name == "test-service"
    assert client.redis_url == "redis://testhost:6379"


def test_client_collects_tools():
    from sdk.alfred_sdk.client import AlfredClient
    from sdk.alfred_sdk.mcp import mcp_tool

    client = AlfredClient(service_name="test")

    @client.tool(name="test.hello", description="Say hello")
    def hello(name: str) -> str:
        return f"Hello {name}"

    assert len(client.tools) == 1
    assert client.tools[0]["name"] == "test.hello"


def test_client_collects_publishers():
    from sdk.alfred_sdk.client import AlfredClient

    client = AlfredClient(service_name="test")

    @client.publisher(topic="test/events")
    def emit_event(data):
        return data

    assert len(client.publishers) == 1
    assert client.publishers[0]["topic"] == "test/events"


def test_client_collects_subscribers():
    from sdk.alfred_sdk.client import AlfredClient

    client = AlfredClient(service_name="test")

    @client.subscriber(topic="test/commands")
    def on_command(payload):
        pass

    assert len(client.subscribers) == 1
    assert client.subscribers[0]["topic"] == "test/commands"


def test_client_generates_tool_manifest():
    from sdk.alfred_sdk.client import AlfredClient

    client = AlfredClient(
        service_name="home-service",
        service_endpoint="http://home-service:8000/mcp",
    )

    @client.tool(name="smart_home.dim_lights", description="Dim lights")
    def dim_lights(room: str, level: int):
        return {"ok": True}

    manifest = client.get_registration_manifest()
    assert manifest["service_name"] == "home-service"
    assert manifest["service_endpoint"] == "http://home-service:8000/mcp"
    assert len(manifest["tools"]) == 1
    assert manifest["tools"][0]["name"] == "smart_home.dim_lights"
```

`alfred/sdk/tests/test_mcp.py`:
```python
"""Tests for MCP tool serving."""

import pytest


def test_mcp_tool_decorator_preserves_function():
    from sdk.alfred_sdk.mcp import mcp_tool

    @mcp_tool(name="test.add", description="Add two numbers")
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, 3) == 5
    assert hasattr(add, "_mcp_tool_meta")
    assert add._mcp_tool_meta["name"] == "test.add"


def test_mcp_tool_extracts_parameter_schema():
    from sdk.alfred_sdk.mcp import mcp_tool

    @mcp_tool(name="test.greet", description="Greet someone")
    def greet(name: str, excited: bool = False) -> str:
        return f"Hello {name}{'!' if excited else '.'}"

    meta = greet._mcp_tool_meta
    assert "name" in meta["parameters"]
    assert "excited" in meta["parameters"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd alfred && python -m pytest sdk/tests/test_client.py sdk/tests/test_mcp.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement MCP decorator**

`alfred/sdk/alfred_sdk/mcp.py`:
```python
"""MCP tool decorator for declaring microservice capabilities."""

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable, get_type_hints


def mcp_tool(name: str, description: str) -> Callable:
    """Decorator to declare an MCP tool capability.

    Extracts parameter info from type hints to build a tool manifest.
    The decorated function still works normally when called directly.
    """

    def decorator(fn: Callable) -> Callable:
        hints = get_type_hints(fn)
        sig = inspect.signature(fn)

        parameters: dict[str, Any] = {}
        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue
            param_info: dict[str, Any] = {}
            if param_name in hints:
                param_info["type"] = hints[param_name].__name__
            if param.default is not inspect.Parameter.empty:
                param_info["default"] = param.default
            parameters[param_name] = param_info

        fn._mcp_tool_meta = {
            "name": name,
            "description": description,
            "parameters": parameters,
        }

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        wrapper._mcp_tool_meta = fn._mcp_tool_meta
        return wrapper

    return decorator
```

- [ ] **Step 4: Implement AlfredClient**

`alfred/sdk/alfred_sdk/client.py`:
```python
"""AlfredClient — the entry point for microservices to integrate with Alfred."""

from __future__ import annotations

import functools
import os
from typing import Any, Callable

from .mcp import mcp_tool


class AlfredClient:
    """Client that microservices use to register with Alfred.

    Collects tool declarations, publishers, and subscribers,
    then registers them with Alfred's tool registry on connect.
    """

    def __init__(
        self,
        service_name: str = "",
        service_endpoint: str = "",
        redis_url: str = "",
        mqtt_host: str = "",
        mqtt_port: int = 1883,
    ):
        self.service_name = service_name or os.getenv("ALFRED_SERVICE_NAME", "unknown")
        self.service_endpoint = service_endpoint or os.getenv("ALFRED_SERVICE_ENDPOINT", "")
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")
        self.mqtt_host = mqtt_host or os.getenv("MQTT_HOST", "localhost")
        self.mqtt_port = mqtt_port

        self.tools: list[dict[str, Any]] = []
        self.publishers: list[dict[str, Any]] = []
        self.subscribers: list[dict[str, Any]] = []
        self._tool_fns: dict[str, Callable] = {}

    def tool(self, name: str, description: str) -> Callable:
        """Register an MCP tool capability."""

        def decorator(fn: Callable) -> Callable:
            wrapped = mcp_tool(name=name, description=description)(fn)
            meta = wrapped._mcp_tool_meta
            self.tools.append(meta)
            self._tool_fns[name] = wrapped
            return wrapped

        return decorator

    def publisher(self, topic: str) -> Callable:
        """Register an event publisher."""

        def decorator(fn: Callable) -> Callable:
            self.publishers.append({"topic": topic, "function": fn.__name__})

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                return fn(*args, **kwargs)

            wrapper._publisher_meta = {"topic": topic}
            return wrapper

        return decorator

    def subscriber(self, topic: str) -> Callable:
        """Register an event subscriber."""

        def decorator(fn: Callable) -> Callable:
            self.subscribers.append({"topic": topic, "function": fn.__name__})

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                return fn(*args, **kwargs)

            wrapper._subscriber_meta = {"topic": topic}
            return wrapper

        return decorator

    def get_registration_manifest(self) -> dict[str, Any]:
        """Build the tool registration manifest for Alfred's registry."""
        return {
            "service_name": self.service_name,
            "service_endpoint": self.service_endpoint,
            "tools": self.tools,
            "publishers": [p["topic"] for p in self.publishers],
            "subscribers": [s["topic"] for s in self.subscribers],
        }

    async def register(self) -> None:
        """Register this service's capabilities with Alfred's tool registry on Redis."""
        import redis.asyncio as aioredis

        r = aioredis.from_url(self.redis_url)
        import json

        manifest = self.get_registration_manifest()
        await r.hset("alfred:tool_registry", self.service_name, json.dumps(manifest))
        await r.close()
```

- [ ] **Step 5: Implement SDK events helper**

`alfred/sdk/alfred_sdk/events.py`:
```python
"""Event schema helpers for the SDK.

Re-exports the canonical event types from bus/schemas for convenience.
SDK consumers can use these without importing from the bus package directly.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


class BaseEvent(BaseModel):
    """Base event — mirrors bus/schemas/events.py for SDK standalone use."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str


class StateChangedEvent(BaseEvent):
    """A device or entity changed state."""

    event_type: str = "state_changed"
    domain: str
    entity_id: str
    old_state: str | None = None
    new_state: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class ActionRequest(BaseEvent):
    """Request to execute an MCP tool."""

    event_type: str = "action_request"
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    target_service: str
    tool_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ActionResult(BaseEvent):
    """Result of an MCP tool execution."""

    event_type: str = "action_result"
    request_id: str
    tool_name: str
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None
```

- [ ] **Step 6: Update SDK __init__.py and pyproject.toml**

`alfred/sdk/alfred_sdk/__init__.py`:
```python
"""alfred-sdk — the only coupling between Alfred and external applications."""

from .client import AlfredClient
from .mcp import mcp_tool
from .telemetry import track_latency, track_tokens, track_event

__all__ = [
    "AlfredClient",
    "mcp_tool",
    "track_latency",
    "track_tokens",
    "track_event",
]
```

`alfred/sdk/pyproject.toml`:
```toml
[project]
name = "alfred-sdk"
version = "0.1.0"
description = "SDK for integrating applications with Project Alfred"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.0",
    "redis>=5.0",
]

[project.optional-dependencies]
telemetry = [
    "opentelemetry-api>=1.20",
    "opentelemetry-sdk>=1.20",
]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
cd alfred && python -m pytest sdk/tests/ -v
```
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add sdk/
git commit -m "Add alfred-sdk: AlfredClient, MCP tools, pub/sub, telemetry decorators"
```

---

## Chunk 5: Home Service, Home Agent, Reflex Engine, and EXP-001

### Task 8: Create Home Service (Separate Repo)

**Files (in workspace root, NOT inside alfred/):**
- Create: `home-service/app/__init__.py`
- Create: `home-service/app/ha_client.py`
- Create: `home-service/alfred_ext/__init__.py`
- Create: `home-service/alfred_ext/register.py`
- Create: `home-service/pyproject.toml`
- Create: `home-service/Dockerfile`
- Create: `home-service/.gitignore`
- Test: `home-service/tests/test_ha_client.py`

- [ ] **Step 1: Initialize home-service repo**

```bash
cd /Users/anirudhlath/code/private/alfred
mkdir -p home-service/app home-service/alfred_ext home-service/tests
cd home-service && git init
```

- [ ] **Step 2: Write failing test for HA client**

`home-service/tests/test_ha_client.py`:
```python
"""Tests for Home Assistant client wrapper."""

import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_get_states_returns_entities():
    from app.ha_client import HomeAssistantClient

    mock_response = AsyncMock()
    mock_response.json = MagicMock(
        return_value=[
            {"entity_id": "light.living_room", "state": "on", "attributes": {"brightness": 255}},
            {"entity_id": "media_player.tv", "state": "playing", "attributes": {}},
        ]
    )
    mock_response.raise_for_status = lambda: None

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        client = HomeAssistantClient(host="http://fake:8123", token="fake-token")
        states = await client.get_states()
        assert len(states) == 2
        assert states[0]["entity_id"] == "light.living_room"


@pytest.mark.asyncio
async def test_call_service_sends_request():
    from app.ha_client import HomeAssistantClient

    mock_response = AsyncMock()
    mock_response.json = MagicMock(return_value=[])
    mock_response.raise_for_status = lambda: None

    with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
        client = HomeAssistantClient(host="http://fake:8123", token="fake-token")
        await client.call_service("light", "turn_on", {"entity_id": "light.living_room", "brightness": 50})

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "light/turn_on" in str(call_args)
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd home-service && python -m pytest tests/test_ha_client.py -v
```
Expected: FAIL

- [ ] **Step 4: Implement HA client**

`home-service/app/__init__.py` (empty)

`home-service/app/ha_client.py`:
```python
"""Thin wrapper around Home Assistant REST API."""

from __future__ import annotations

import httpx
from typing import Any


class HomeAssistantClient:
    """Async client for Home Assistant's REST API."""

    def __init__(self, host: str, token: str):
        self.host = host.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def get_states(self) -> list[dict[str, Any]]:
        """Get all entity states."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.host}/api/states", headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def call_service(
        self, domain: str, service: str, data: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Call an HA service (e.g., light/turn_on)."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.host}/api/services/{domain}/{service}",
                headers=self.headers,
                json=data,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_entity_state(self, entity_id: str) -> dict[str, Any]:
        """Get state of a single entity."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.host}/api/states/{entity_id}", headers=self.headers
            )
            resp.raise_for_status()
            return resp.json()
```

- [ ] **Step 5: Implement Alfred integration extension**

`home-service/alfred_ext/__init__.py` (empty)

`home-service/alfred_ext/register.py`:
```python
"""Alfred integration for home-service.

Optional — this module is only used when alfred-sdk is installed.
The home-service works independently without it.
"""

from __future__ import annotations

import os
from alfred_sdk import AlfredClient

from app.ha_client import HomeAssistantClient

ha = HomeAssistantClient(
    host=os.getenv("HA_HOST", "http://homeassistant.local:8123"),
    token=os.getenv("HA_TOKEN", ""),
)

client = AlfredClient(
    service_name="home-service",
    service_endpoint=f"http://{os.getenv('HOSTNAME', 'home-service')}:8000/mcp",
)


@client.tool(name="smart_home.dim_lights", description="Dim lights in a room to a level (0-100)")
async def dim_lights(room: str, level: int) -> dict:
    entity_id = f"light.{room}"
    brightness = int(level * 2.55)  # Convert 0-100 to 0-255
    await ha.call_service("light", "turn_on", {"entity_id": entity_id, "brightness": brightness})
    return {"entity_id": entity_id, "brightness": level}


@client.tool(name="smart_home.turn_off_lights", description="Turn off all lights in a room")
async def turn_off_lights(room: str) -> dict:
    entity_id = f"light.{room}"
    await ha.call_service("light", "turn_off", {"entity_id": entity_id})
    return {"entity_id": entity_id, "state": "off"}


@client.tool(name="smart_home.set_scene", description="Activate a Home Assistant scene")
async def set_scene(scene_name: str) -> dict:
    entity_id = f"scene.{scene_name}"
    await ha.call_service("scene", "turn_on", {"entity_id": entity_id})
    return {"scene": scene_name, "activated": True}
```

- [ ] **Step 6: Create pyproject.toml and Dockerfile**

`home-service/pyproject.toml`:
```toml
[project]
name = "home-service"
version = "0.1.0"
description = "Home Assistant wrapper microservice"
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.27",
]

[project.optional-dependencies]
alfred = [
    "alfred-sdk>=0.1.0",
]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

`home-service/.gitignore`:
```gitignore
__pycache__/
*.py[cod]
*.egg-info/
.venv/
.env
```

`home-service/Dockerfile`:
```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir ".[alfred]"

COPY app/ /app/app/
COPY alfred_ext/ /app/alfred_ext/

CMD ["python", "-m", "alfred_ext.register"]
```

- [ ] **Step 7: Run tests, commit**

```bash
cd home-service && python -m pytest tests/ -v
git add -A
git commit -m "Initial home-service: HA client wrapper with Alfred SDK integration"
```

---

### Task 9: Implement Home Domain Sub-Agent

**Files:**
- Create: `alfred/domains/home/home_agent.py`
- Test: `alfred/domains/home/tests/__init__.py`
- Test: `alfred/domains/home/tests/test_home_agent.py`

- [ ] **Step 1: Write failing test for home agent**

`alfred/domains/home/tests/test_home_agent.py`:
```python
"""Tests for home domain sub-agent."""

import pytest
import json
from unittest.mock import AsyncMock, patch

from bus.schemas.events import ActionRequest, ActionResult


@pytest.mark.asyncio
async def test_home_agent_routes_dim_lights():
    from domains.home.home_agent import HomeAgent

    mock_redis = AsyncMock()
    # Simulate tool registry having home-service registered
    mock_redis.hget = AsyncMock(
        return_value=json.dumps(
            {
                "service_name": "home-service",
                "service_endpoint": "http://home-service:8000/mcp",
                "tools": [{"name": "smart_home.dim_lights"}],
            }
        )
    )

    agent = HomeAgent(redis=mock_redis)

    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="smart_home.dim_lights",
        parameters={"room": "living_room", "level": 20},
    )

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = AsyncMock()
        mock_resp.json = MagicMock(return_value={"brightness": 20})
        mock_resp.raise_for_status = lambda: None
        mock_post.return_value = mock_resp

        result = await agent.execute_action(action)

    assert result.status == "success"
    assert result.tool_name == "smart_home.dim_lights"


@pytest.mark.asyncio
async def test_home_agent_handles_unknown_tool():
    from domains.home.home_agent import HomeAgent

    mock_redis = AsyncMock()
    mock_redis.hget = AsyncMock(return_value=None)

    agent = HomeAgent(redis=mock_redis)

    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="smart_home.unknown_tool",
        parameters={},
    )

    result = await agent.execute_action(action)
    assert result.status == "error"
    assert "not found" in result.error.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd alfred && python -m pytest domains/home/tests/test_home_agent.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement home agent**

`alfred/domains/home/tests/__init__.py` (empty)

`alfred/domains/home/home_agent.py`:
```python
"""Home domain sub-agent.

Routes actions from the Reflex Engine to the home-service microservice
via MCP tool calls over HTTP. Discovers service endpoints from the
Redis tool registry.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import redis.asyncio as aioredis

from bus.schemas.events import ActionRequest, ActionResult

logger = logging.getLogger(__name__)


class HomeAgent:
    """Sub-agent for the home domain."""

    def __init__(self, redis: aioredis.Redis):
        self.redis = redis

    async def _get_service_endpoint(self, service_name: str) -> str | None:
        """Look up a service endpoint from the tool registry."""
        manifest_json = await self.redis.hget("alfred:tool_registry", service_name)
        if manifest_json is None:
            return None
        manifest = json.loads(manifest_json)
        return manifest.get("service_endpoint")

    async def execute_action(self, action: ActionRequest) -> ActionResult:
        """Execute an action by calling the target microservice's MCP endpoint."""
        endpoint = await self._get_service_endpoint(action.target_service)

        if endpoint is None:
            return ActionResult(
                source="home-agent",
                request_id=action.request_id,
                tool_name=action.tool_name,
                status="error",
                error=f"Service '{action.target_service}' not found in tool registry",
            )

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    endpoint,
                    json={
                        "method": action.tool_name,
                        "params": action.parameters,
                        "id": action.request_id,
                    },
                )
                resp.raise_for_status()
                result_data = resp.json()

            return ActionResult(
                source="home-agent",
                request_id=action.request_id,
                tool_name=action.tool_name,
                status="success",
                result=result_data if isinstance(result_data, dict) else {"data": result_data},
            )
        except Exception as e:
            logger.error("Action execution failed: %s", e)
            return ActionResult(
                source="home-agent",
                request_id=action.request_id,
                tool_name=action.tool_name,
                status="error",
                error=str(e),
            )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd alfred && python -m pytest domains/home/tests/test_home_agent.py -v
```
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd alfred && git add domains/
git commit -m "Add home domain sub-agent with MCP tool routing via registry"
```

---

### Task 10: Implement Reflex Engine

**Files:**
- Create: `alfred/core/reflex/engine.py`
- Create: `alfred/core/reflex/memory_reader.py`
- Create: `alfred/core/reflex/ollama_client.py`
- Test: `alfred/core/reflex/tests/test_engine.py`
- Test: `alfred/core/reflex/tests/test_memory_reader.py`

- [ ] **Step 1: Write failing test for memory reader**

`alfred/core/reflex/tests/test_memory_reader.py`:
```python
"""Tests for Markdown preference reader."""

import pytest
import tempfile
import os


def test_read_single_preference_file():
    from core.reflex.memory_reader import read_preferences

    with tempfile.TemporaryDirectory() as tmpdir:
        pref_file = os.path.join(tmpdir, "lighting.md")
        with open(pref_file, "w") as f:
            f.write("""---
domain: home
updated: 2026-03-10
confidence: manual
---
# Lighting Preferences

- I prefer dim lighting when watching TV or movies
- Default brightness during daytime: 80%
""")

        prefs = read_preferences(tmpdir)
        assert "lighting" in prefs.lower() or "dim" in prefs.lower()
        assert "watching TV" in prefs


def test_read_multiple_preference_files():
    from core.reflex.memory_reader import read_preferences

    with tempfile.TemporaryDirectory() as tmpdir:
        for name, content in [
            ("lighting.md", "# Lighting\n- Dim when watching TV\n"),
            ("media.md", "# Media\n- Usually watch in living room\n"),
        ]:
            with open(os.path.join(tmpdir, name), "w") as f:
                f.write(content)

        prefs = read_preferences(tmpdir)
        assert "Dim when watching TV" in prefs
        assert "living room" in prefs


def test_read_preferences_empty_directory():
    from core.reflex.memory_reader import read_preferences

    with tempfile.TemporaryDirectory() as tmpdir:
        prefs = read_preferences(tmpdir)
        assert prefs == ""
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd alfred && python -m pytest core/reflex/tests/test_memory_reader.py -v
```

- [ ] **Step 3: Implement memory reader**

`alfred/core/reflex/memory_reader.py`:
```python
"""Read Markdown preference files from core/memory/preferences/.

Returns concatenated plain text for injection into the SLM prompt.
Strips YAML frontmatter — the SLM only needs the natural language content.
"""

from __future__ import annotations

import os
import re


def read_preferences(preferences_dir: str) -> str:
    """Read all .md files in the preferences directory and return concatenated content."""
    if not os.path.isdir(preferences_dir):
        return ""

    sections: list[str] = []
    for filename in sorted(os.listdir(preferences_dir)):
        if not filename.endswith(".md"):
            continue
        filepath = os.path.join(preferences_dir, filename)
        with open(filepath) as f:
            content = f.read()

        # Strip YAML frontmatter
        content = re.sub(r"^---\n.*?\n---\n", "", content, flags=re.DOTALL)
        content = content.strip()
        if content:
            sections.append(content)

    return "\n\n".join(sections)
```

- [ ] **Step 4: Run memory reader tests**

```bash
cd alfred && python -m pytest core/reflex/tests/test_memory_reader.py -v
```
Expected: All 3 tests PASS

- [ ] **Step 5: Write failing tests for Reflex Engine**

`alfred/core/reflex/tests/test_engine.py`:
```python
"""Tests for the Reflex Engine — System 1 SLM inference loop."""

import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock

from bus.schemas.events import StateChangedEvent, ActionRequest


@pytest.fixture
def tv_on_event():
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="media_player.living_room_tv",
        old_state="off",
        new_state="on",
        attributes={"friendly_name": "Living Room TV"},
    )


@pytest.fixture
def mock_preferences():
    return (
        "# Lighting Preferences\n\n"
        "- I prefer dim lighting when watching TV or movies\n"
        "- Default brightness during daytime: 80%\n"
    )


@pytest.mark.asyncio
async def test_reflex_engine_produces_action(tv_on_event, mock_preferences):
    from core.reflex.engine import ReflexEngine

    # Mock Ollama to return a structured action
    mock_ollama_response = {
        "response": json.dumps(
            {
                "tool_name": "smart_home.dim_lights",
                "target_service": "home-service",
                "parameters": {"room": "living_room", "level": 20},
            }
        ),
        "prompt_tokens": 200,
        "completion_tokens": 30,
        "total_tokens": 230,
    }

    with patch("core.reflex.memory_reader.read_preferences", return_value=mock_preferences):
        with patch("core.reflex.ollama_client.infer", new_callable=AsyncMock, return_value=mock_ollama_response):
            engine = ReflexEngine(preferences_dir="/fake/prefs")
            action = await engine.process_event(tv_on_event)

    assert action is not None
    assert action.tool_name == "smart_home.dim_lights"
    assert action.parameters["level"] == 20
    assert action.target_service == "home-service"


@pytest.mark.asyncio
async def test_reflex_engine_returns_none_for_no_action(mock_preferences):
    from core.reflex.engine import ReflexEngine

    # Event that doesn't warrant an action
    boring_event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="sensor.temperature",
        new_state="22.5",
    )

    mock_ollama_response = {
        "response": json.dumps({"action": "none"}),
        "prompt_tokens": 150,
        "completion_tokens": 10,
        "total_tokens": 160,
    }

    with patch("core.reflex.memory_reader.read_preferences", return_value=mock_preferences):
        with patch("core.reflex.ollama_client.infer", new_callable=AsyncMock, return_value=mock_ollama_response):
            engine = ReflexEngine(preferences_dir="/fake/prefs")
            action = await engine.process_event(boring_event)

    assert action is None
```

- [ ] **Step 6: Run tests to verify they fail**

```bash
cd alfred && python -m pytest core/reflex/tests/test_engine.py -v
```

- [ ] **Step 7: Implement Ollama client**

`alfred/core/reflex/ollama_client.py`:
```python
"""Thin async client for Ollama's generate API."""

from __future__ import annotations

import os

import httpx

from sdk.alfred_sdk.telemetry import track_tokens

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = "llama3:8b"


async def infer(prompt: str, model: str | None = None) -> dict:
    """Send a prompt to Ollama and return the response with token counts."""
    model = model or os.getenv("OLLAMA_MODEL", DEFAULT_MODEL)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{OLLAMA_HOST}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    return {
        "response": data.get("response", ""),
        "prompt_tokens": data.get("prompt_eval_count", 0),
        "completion_tokens": data.get("eval_count", 0),
        "total_tokens": data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
    }
```

- [ ] **Step 8: Implement Reflex Engine**

`alfred/core/reflex/engine.py`:
```python
"""Reflex Engine — System 1 fast-path SLM inference.

Consumes events from Redis Streams, reads Markdown preferences,
prompts the local SLM, and produces structured ActionRequests.

Design for eval-ability: structured (event, preferences) in → structured action out.
No side effects during inference.
"""

from __future__ import annotations

import json
import logging

from bus.schemas.events import StateChangedEvent, ActionRequest
from core.reflex.memory_reader import read_preferences
from core.reflex import ollama_client
from sdk.alfred_sdk.telemetry import track_latency

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Alfred's Reflex Engine — a fast-acting steward for a smart home.

Given an event from the smart home and the user's preferences, decide if an action is needed.

Rules:
- Only act if the event clearly matches a user preference
- If no action is needed, respond with: {"action": "none"}
- If an action IS needed, respond with:
  {"tool_name": "<tool>", "target_service": "<service>", "parameters": {<params>}}

Available tools:
- smart_home.dim_lights(room: str, level: int 0-100)
- smart_home.turn_off_lights(room: str)
- smart_home.set_scene(scene_name: str)

Respond ONLY with valid JSON. No explanation."""


class ReflexEngine:
    """The System 1 fast-path inference engine."""

    def __init__(self, preferences_dir: str):
        self.preferences_dir = preferences_dir

    @track_latency(category="reflex")
    async def process_event(self, event: StateChangedEvent) -> ActionRequest | None:
        """Process a state change event and optionally produce an action.

        This is the core inference loop — designed for eval-ability:
        structured input (event + preferences) → structured output (action or None).
        """
        preferences = read_preferences(self.preferences_dir)

        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"## User Preferences\n{preferences}\n\n"
            f"## Event\n"
            f"Entity: {event.entity_id}\n"
            f"Domain: {event.domain}\n"
            f"Changed: {event.old_state} → {event.new_state}\n"
            f"Attributes: {json.dumps(event.attributes)}\n\n"
            f"## Your Decision (JSON only):"
        )

        response = await ollama_client.infer(prompt)
        return self._parse_response(response, event)

    def _parse_response(
        self, response: dict, event: StateChangedEvent
    ) -> ActionRequest | None:
        """Parse the SLM's JSON response into an ActionRequest or None."""
        try:
            raw = response.get("response", "")
            parsed = json.loads(raw)

            if parsed.get("action") == "none":
                logger.debug("No action for event %s", event.entity_id)
                return None

            tool_name = parsed.get("tool_name")
            if not tool_name:
                logger.warning("SLM response missing tool_name: %s", raw)
                return None

            return ActionRequest(
                source="reflex-engine",
                target_service=parsed.get("target_service", "home-service"),
                tool_name=tool_name,
                parameters=parsed.get("parameters", {}),
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Failed to parse SLM response: %s — %s", e, response)
            return None
```

- [ ] **Step 9: Run all Reflex Engine tests**

```bash
cd alfred && python -m pytest core/reflex/tests/ -v
```
Expected: All 5 tests PASS

- [ ] **Step 10: Commit**

```bash
cd alfred && git add core/reflex/ core/__init__.py
git commit -m "Add Reflex Engine: memory reader, Ollama client, SLM inference loop"
```

---

### Task 11: Shared Config + Telemetry Collector + Scratchpad Writer

**Files:**
- Create: `alfred/shared/config.py`
- Create: `alfred/telemetry/collector.py`
- Create: `alfred/telemetry/schemas.py`
- Create: `alfred/core/memory/scratchpad_writer.py`
- Test: `alfred/core/memory/tests/__init__.py`
- Test: `alfred/core/memory/tests/test_scratchpad_writer.py`

- [ ] **Step 1: Implement shared config**

`alfred/shared/config.py`:
```python
"""Shared configuration loader. Reads from environment variables with .env fallback."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AlfredConfig:
    redis_host: str = "localhost"
    redis_port: int = 6379
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3:8b"
    ha_host: str = "http://homeassistant.local:8123"
    ha_token: str = ""
    research_vault_path: str = "./research"
    signoz_enabled: bool = True
    otel_endpoint: str = "http://localhost:4317"

    @classmethod
    def from_env(cls) -> AlfredConfig:
        return cls(
            redis_host=os.getenv("REDIS_HOST", "localhost"),
            redis_port=int(os.getenv("REDIS_PORT", "6379")),
            mqtt_host=os.getenv("MQTT_HOST", "localhost"),
            mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
            ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "llama3:8b"),
            ha_host=os.getenv("HA_HOST", "http://homeassistant.local:8123"),
            ha_token=os.getenv("HA_TOKEN", ""),
            research_vault_path=os.getenv("RESEARCH_VAULT_PATH", "./research"),
            signoz_enabled=os.getenv("SIGNOZ_ENABLED", "true").lower() == "true",
            otel_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}"
```

- [ ] **Step 2: Implement telemetry collector**

`alfred/telemetry/collector.py`:
```python
"""Telemetry collector — drains metrics from the telemetry buffer to research vault CSVs.

Runs as a background task. Reads from the in-memory telemetry buffer (or Redis Stream
in production) and appends to CSV files + generates daily Markdown summaries.
"""

from __future__ import annotations

import csv
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def flush_to_csv(entries: list[dict[str, Any]], vault_path: str) -> None:
    """Append telemetry entries to the appropriate CSV files in the research vault."""
    data_dir = Path(vault_path) / "data"

    for entry in entries:
        category = entry.get("category", entry.get("metric_type", "general"))
        category_dir = data_dir / category
        category_dir.mkdir(parents=True, exist_ok=True)

        csv_path = category_dir / "raw.csv"
        file_exists = csv_path.exists()

        fieldnames = sorted(entry.keys())
        with open(csv_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerow(entry)


def generate_daily_summary(vault_path: str, date: str | None = None) -> str:
    """Generate a daily Markdown summary from today's telemetry data."""
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    data_dir = Path(vault_path) / "data"
    if not data_dir.exists():
        return ""

    lines = [f"# Daily Research Note — {date}\n"]

    # Scan each category directory for CSVs
    for category_dir in sorted(data_dir.iterdir()):
        if not category_dir.is_dir():
            continue

        csv_path = category_dir / "raw.csv"
        if not csv_path.exists():
            continue

        values = []
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("timestamp", "").startswith(date) and "value" in row:
                    try:
                        values.append(float(row["value"]))
                    except ValueError:
                        pass

        if values:
            lines.append(f"\n## {category_dir.name}")
            lines.append(f"- Count: {len(values)}")
            lines.append(f"- Mean: {statistics.mean(values):.1f}")
            lines.append(f"- Median (p50): {statistics.median(values):.1f}")
            if len(values) >= 2:
                sorted_vals = sorted(values)
                p95_idx = int(len(sorted_vals) * 0.95)
                p99_idx = int(len(sorted_vals) * 0.99)
                lines.append(f"- p95: {sorted_vals[min(p95_idx, len(sorted_vals)-1)]:.1f}")
                lines.append(f"- p99: {sorted_vals[min(p99_idx, len(sorted_vals)-1)]:.1f}")

    summary = "\n".join(lines)

    # Write to daily/ directory
    daily_dir = Path(vault_path) / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    daily_path = daily_dir / f"{date}.md"
    with open(daily_path, "w") as f:
        f.write(summary)

    return summary
```

- [ ] **Step 3: Implement telemetry schemas**

`alfred/telemetry/schemas.py`:
```python
"""Pydantic models for telemetry data — typed versions of the dict entries
produced by the SDK decorators."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class LatencyMetric(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metric_type: str = "latency"
    category: str
    function: str
    value: float = Field(description="Duration in milliseconds")
    unit: str = "ms"


class TokenMetric(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metric_type: str = "tokens"
    model: str
    function: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    inference_ms: float
    unit: str = "tokens"


class EventMetric(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metric_type: str = "event_throughput"
    bus: str
    function: str
    value: float = Field(description="Publish latency in milliseconds")
    unit: str = "ms"
```

- [ ] **Step 4: Write failing test for scratchpad writer**

`alfred/core/memory/tests/__init__.py` (empty)

`alfred/core/memory/tests/test_scratchpad_writer.py`:
```python
"""Tests for the scratchpad async writer."""

import pytest
import tempfile
import os


@pytest.mark.asyncio
async def test_scratchpad_writer_drains_queue():
    from core.memory.scratchpad_writer import ScratchpadWriter
    from unittest.mock import AsyncMock

    # Mock Redis that returns 3 entries then None
    mock_redis = AsyncMock()
    entries = [
        b"2026-03-10T14:00:00Z [reflex] TV turned on in living room",
        b"2026-03-10T14:00:01Z [reflex] Dimmed lights to 20%",
        None,  # signals end of queue
    ]
    mock_redis.lpop = AsyncMock(side_effect=entries)

    with tempfile.TemporaryDirectory() as tmpdir:
        scratchpad_path = os.path.join(tmpdir, "scratchpad.md")
        # Create initial scratchpad
        with open(scratchpad_path, "w") as f:
            f.write("---\nlast_drain: null\n---\n# Scratchpad\n")

        writer = ScratchpadWriter(
            redis=mock_redis,
            queue_key="alfred:scratchpad:queue",
            scratchpad_path=scratchpad_path,
        )
        drained = await writer.drain_once()

        assert drained == 2
        with open(scratchpad_path) as f:
            content = f.read()
        assert "TV turned on" in content
        assert "Dimmed lights" in content


@pytest.mark.asyncio
async def test_scratchpad_writer_empty_queue():
    from core.memory.scratchpad_writer import ScratchpadWriter
    from unittest.mock import AsyncMock

    mock_redis = AsyncMock()
    mock_redis.lpop = AsyncMock(return_value=None)

    with tempfile.TemporaryDirectory() as tmpdir:
        scratchpad_path = os.path.join(tmpdir, "scratchpad.md")
        with open(scratchpad_path, "w") as f:
            f.write("# Scratchpad\n")

        writer = ScratchpadWriter(
            redis=mock_redis,
            queue_key="alfred:scratchpad:queue",
            scratchpad_path=scratchpad_path,
        )
        drained = await writer.drain_once()

        assert drained == 0
```

- [ ] **Step 5: Run tests to verify they fail**

```bash
cd alfred && python -m pytest core/memory/tests/test_scratchpad_writer.py -v
```
Expected: FAIL

- [ ] **Step 6: Implement scratchpad writer**

`alfred/core/memory/scratchpad_writer.py`:
```python
"""Scratchpad async writer.

Drains observations from a Redis List (alfred:scratchpad:queue) and appends
them to scratchpad.md. This serializes all scratchpad writes through a single
coroutine, preventing concurrent file corruption.

Components push observations to the Redis List; this writer is the only
process that touches the scratchpad file.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


class ScratchpadWriter:
    """Serialized writer for the scratchpad file."""

    def __init__(
        self,
        redis: aioredis.Redis,
        queue_key: str = "alfred:scratchpad:queue",
        scratchpad_path: str = "core/memory/scratchpad.md",
    ):
        self.redis = redis
        self.queue_key = queue_key
        self.scratchpad_path = scratchpad_path

    async def drain_once(self) -> int:
        """Drain all pending entries from the Redis List to the scratchpad file.

        Returns the number of entries drained.
        """
        entries: list[str] = []
        while True:
            entry = await self.redis.lpop(self.queue_key)
            if entry is None:
                break
            if isinstance(entry, bytes):
                entry = entry.decode()
            entries.append(entry)

        if not entries:
            return 0

        with open(self.scratchpad_path, "a") as f:
            for entry in entries:
                f.write(f"\n{entry}")

        logger.info("Drained %d entries to scratchpad", len(entries))
        return len(entries)

    async def run(self, interval_seconds: float = 5.0) -> None:
        """Run the writer loop, draining the queue at regular intervals."""
        logger.info("Scratchpad writer started (interval: %.1fs)", interval_seconds)
        while True:
            try:
                await self.drain_once()
            except Exception as e:
                logger.error("Scratchpad drain error: %s", e)
            await asyncio.sleep(interval_seconds)
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
cd alfred && python -m pytest core/memory/tests/test_scratchpad_writer.py -v
```
Expected: All 2 tests PASS

- [ ] **Step 8: Commit**

```bash
cd alfred && git add shared/ telemetry/ core/memory/
git commit -m "Add shared config, telemetry collector + schemas, and scratchpad writer"
```

---

### Task 12: Create EXP-001 Research Scaffold + Integration Test

**Files:**
- Create: `alfred/research/experiments/EXP-001-reflex-latency.md`
- Create: `alfred/research/paper/00-outline.md`
- Create: `alfred/tests/integration/__init__.py`
- Create: `alfred/tests/integration/test_reflex_end_to_end.py`

- [ ] **Step 1: Create experiment template**

`alfred/research/experiments/EXP-001-reflex-latency.md`:
```markdown
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
```

- [ ] **Step 2: Create paper outline**

`alfred/research/paper/00-outline.md`:
```markdown
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
```

- [ ] **Step 3: Write integration test (mocked, runnable without services)**

`alfred/tests/__init__.py` (empty)
`alfred/tests/integration/__init__.py` (empty)

`alfred/tests/integration/test_reflex_end_to_end.py`:
```python
"""Integration test: full event → reflex → action pipeline.

Uses mocked Ollama and Redis to test the complete flow without external services.
This is the eval-ability contract test — structured in, structured out.
"""

import pytest
import json
from unittest.mock import AsyncMock, patch

from bus.schemas.events import StateChangedEvent, ActionRequest
from core.reflex.engine import ReflexEngine


@pytest.fixture
def preferences_dir(tmp_path):
    prefs = tmp_path / "preferences"
    prefs.mkdir()

    lighting = prefs / "lighting.md"
    lighting.write_text(
        "---\ndomain: home\n---\n"
        "# Lighting Preferences\n\n"
        "- I prefer dim lighting when watching TV or movies\n"
        "- Default brightness during daytime: 80%\n"
        "- Default brightness in the evening: 40%\n"
    )
    return str(prefs)


@pytest.fixture
def tv_on_event():
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="media_player.living_room_tv",
        old_state="off",
        new_state="on",
        attributes={"friendly_name": "Living Room TV"},
    )


@pytest.mark.asyncio
async def test_full_reflex_pipeline(preferences_dir, tv_on_event):
    """The canonical test: TV turns on → Reflex reads preferences → dims lights."""

    ollama_response = {
        "response": json.dumps(
            {
                "tool_name": "smart_home.dim_lights",
                "target_service": "home-service",
                "parameters": {"room": "living_room", "level": 20},
            }
        ),
        "prompt_tokens": 200,
        "completion_tokens": 25,
        "total_tokens": 225,
    }

    with patch(
        "core.reflex.ollama_client.infer",
        new_callable=AsyncMock,
        return_value=ollama_response,
    ):
        engine = ReflexEngine(preferences_dir=preferences_dir)
        action = await engine.process_event(tv_on_event)

    # Structured output verification (eval contract)
    assert action is not None
    assert isinstance(action, ActionRequest)
    assert action.tool_name == "smart_home.dim_lights"
    assert action.target_service == "home-service"
    assert action.parameters["room"] == "living_room"
    assert 0 <= action.parameters["level"] <= 100


@pytest.mark.asyncio
async def test_reflex_no_action_for_irrelevant_event(preferences_dir):
    """Temperature sensor change should not trigger any action."""

    temp_event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="sensor.outside_temperature",
        new_state="22.5",
    )

    ollama_response = {
        "response": json.dumps({"action": "none"}),
        "prompt_tokens": 180,
        "completion_tokens": 8,
        "total_tokens": 188,
    }

    with patch(
        "core.reflex.ollama_client.infer",
        new_callable=AsyncMock,
        return_value=ollama_response,
    ):
        engine = ReflexEngine(preferences_dir=preferences_dir)
        action = await engine.process_event(temp_event)

    assert action is None
```

- [ ] **Step 4: Run all tests across the monorepo**

```bash
cd alfred && python -m pytest -v
```
Expected: All tests across bus/, sdk/, core/, domains/, and tests/ PASS

- [ ] **Step 5: Commit**

```bash
cd alfred && git add research/ tests/
git commit -m "Add EXP-001 template, paper outline, and end-to-end integration tests"
```

---

## Summary

### What This Plan Builds (in order)

| Task | Deliverable | Commit |
|------|-------------|--------|
| 1 | Workspace restructure (alfred/ as git repo) | "Initial commit" |
| 2 | Full directory scaffold + Docker Compose + preferences | "Scaffold monorepo" |
| 3 | CLAUDE.md hierarchy + rules + agents | "Add CLAUDE.md hierarchy" |
| 4 | Pydantic event schemas (bus/schemas/) | "Add canonical event schemas" |
| 5 | MQTT ↔ Redis bridge | "Add MQTT-Redis bridge" |
| 6 | Telemetry decorators (SDK) | "Add telemetry decorators" |
| 7 | AlfredClient + MCP + pub/sub (SDK) | "Add alfred-sdk" |
| 8 | Home Service (separate repo) | "Initial home-service" |
| 9 | Home domain sub-agent | "Add home agent" |
| 10 | Reflex Engine + memory reader + Ollama client | "Add Reflex Engine" |
| 11 | Shared config + telemetry collector + scratchpad writer | "Add config + collector + scratchpad writer" |
| 12 | EXP-001 scaffold + integration tests | "Add EXP-001 + integration tests" |

### What's Next After This Plan

- Set up HA on CachyOS PC
- Pull Llama 3 8B via Ollama
- `docker compose up` the full stack
- Trigger a real HA event and measure the trace → **EXP-001 live run**
- Record results in research vault
