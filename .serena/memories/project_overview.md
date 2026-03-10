# Project Alfred — Overview

## Purpose
Alfred is an ambient, voice-first, decoupled Multi-Agent System for smart environments.
Inspired by Alfred Pennyworth — a proactive AI butler for smart homes.

## Tech Stack
- **Language:** Python 3.13+, async-first
- **Data models:** Pydantic v2 (strict JSON schemas)
- **Package manager:** `uv` (astral) — never use pip directly
- **Lint/format:** `ruff` (line-length 100, double quotes, space indent)
- **Type checking:** `mypy --strict` with pydantic plugin
- **Testing:** `pytest` + `pytest-asyncio` (asyncio_mode = "auto")
- **Observability:** OpenTelemetry → SigNoz
- **Messaging:** MQTT (edge/HA) + Redis Streams (internal backbone)
- **Inference:** Ollama for local SLM (gpt-oss models)
- **Containers:** OCI Containerfiles, Apple `container` CLI (dev), Docker Compose (prod)

## Architecture Highlights
- **System 1 (Reflex Engine):** Local SLM for sub-500ms event→action
- **System 2 (Conscious Engine):** Cloud LLM for complex reasoning (Phase 3)
- **Event Bus:** MQTT bridge ↔ Redis Streams
- **Memory:** Markdown preference files (read-only) + scratchpad (append-only)
- **SDK:** alfred-sdk is the ONLY coupling point for external apps
- **Domains contain agents** (not the other way around)

## Key Entry Points
- `python -m core.reflex` — Reflex Runner
- `python -m bus` — MQTT↔Redis Bridge
