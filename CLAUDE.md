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

## Design Principles

- **No hardcoded tool/service lists** — tools, agents, and services auto-register at runtime via the SDK tool registry; the Reflex Engine prompt must be built dynamically from the registry, not from hardcoded strings
- **SOLID + DRY** — favor abstraction and single sources of truth; constants over literals, registries over enums

## Tech Stack

- Python 3.13+, async-first, Pydantic v2
- `uv` for package management, `ruff` for lint/format, `mypy --strict` for types
- OpenTelemetry → SigNoz for observability
- OCI Containerfiles, Apple container runtime (dev) + Docker Compose (prod)
- MQTT (edge) + Redis Streams (internal backbone)
- Ollama for local SLM inference (gpt-oss:20b on dev, configurable via OLLAMA_MODEL)
- alfred-sdk is the ONLY coupling to external apps

## Key Paths

- `bus/schemas/events.py` — canonical event types (single source of truth)
- `core/reflex/__main__.py` — Reflex Runner entry point (`python -m core.reflex`)
- `core/reflex/tool_registry.py` — reads tool manifests from Redis `alfred:tool_registry`
- `bus/__main__.py` — Bridge entry point (`python -m bus`)
- `core/memory/preferences/` — Markdown preference files (read-only at runtime)
- `core/memory/scratchpad.md` — ephemeral observations (append-only at runtime)
- `shared/config.py` — central env config (loads .env via python-dotenv)
- `sdk/alfred_sdk/feature.py` — `BaseFeature`, `@tool` decorator, manifest models
- `sdk/` — publishable alfred-sdk package
- `domains/home/home_agent.py` — routes actions to home-service via MCP/HTTP
- `research/` — Obsidian vault with experiments, data, paper drafts

## Spec

See `docs/superpowers/specs/2026-03-10-project-alfred-design.md` for full architecture.
