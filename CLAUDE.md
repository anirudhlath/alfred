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

- Python 3.13+, async-first, Pydantic v2
- `uv` for package management, `ruff` for lint/format, `mypy --strict` for types
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
