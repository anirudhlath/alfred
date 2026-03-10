# Phase 1 Live Runner — Design Spec

**Date:** 2026-03-10
**Status:** Approved
**Scope:** Orchestration loop, deployment infrastructure, Home Assistant setup

## Problem

Phase 1 built all the components (Reflex Engine, Bridge, Home Agent, SDK, telemetry) but there is no runner that ties them into a live process. The Phase 1 deliverable — "SLM processes real HA event → context-aware action in <500ms" — requires an end-to-end runnable system.

## What We're Building

Three things:

1. **Orchestration Loop** (`core/reflex/__main__.py`) — The main process that reads events from Redis Streams, runs the Reflex Engine, dispatches actions via Home Agent, and publishes results back.

2. **Home Assistant Instance** (`home-assistant/` repo) — Separate repo with HA running in a container, configured with MQTT integration and template entities (virtual lights, virtual media player) for testing.

3. **Infrastructure + Deployment** — OCI-compliant container images, Apple container runtime scripts for dev (macOS 26), Docker Compose for production (CachyOS server).

## Architecture

### Data Flow

```
Home Assistant (:8123)
    │  MQTT: home/state_changed
    ▼
Mosquitto (:1883)
    │
    ▼
Bridge (bus/)
    │
    ▼
Redis Streams (:6379)
    │  alfred:home:state_changed
    ▼
Reflex Runner (core/reflex/__main__.py)
    ├──▶ Ollama (gpt-oss:20b) — inference
    ├──▶ Home Agent (domains/home/) — action dispatch
    │       └──▶ home-service (:8000/mcp) — MCP JSON-RPC
    │               └──▶ Home Assistant REST API
    └──▶ Scratchpad Writer — observation logging
```

### Container Network

All containers on shared bridge network `alfred-net`:

| Container | Image | Ports | Network Alias |
|-----------|-------|-------|---------------|
| redis | redis:7-alpine | 6379 | redis |
| mosquitto | eclipse-mosquitto:2 | 1883 | mosquitto |
| homeassistant | ghcr.io/home-assistant/home-assistant:stable | 8123 | homeassistant |
| bridge | built from alfred Containerfile | — | bridge |
| reflex | built from alfred Containerfile | — | reflex |
| home-service | built from home-service Containerfile | 8000 | home-service |

Ollama runs natively on the host (not containerized) — accessed via `host.containers.internal` or host network.

### OCI + Dual Runtime Strategy

- All images built as OCI-compliant via `Containerfile` (standard Dockerfile syntax)
- **Dev (M4 Max MBP):** Apple container runtime (`container` CLI on macOS 26) with shell scripts
- **Production (CachyOS server):** Docker Compose
- Images transfer between runtimes via `container image save` / `docker load`

## Components

### 1. Reflex Runner (`core/reflex/__main__.py`)

The orchestration loop:

```
async def main():
    1. Load AlfredConfig from env
    2. Connect to Redis
    3. Ensure consumer group "reflex-engine" on "alfred:home:state_changed"
    4. Start background tasks:
       - ScratchpadWriter.run()
       - Periodic telemetry flush (every 30s)
    5. Create ReflexEngine + HomeAgent
    6. Loop:
       a. XREADGROUP(group="reflex-engine", consumer="worker-1",
                     streams=["alfred:home:state_changed"], block=5000ms)
       b. For each entry:
          - Deserialize to StateChangedEvent
          - engine.process_event(event) → ActionRequest | None
          - If action: agent.execute_action(action) → ActionResult
          - Publish result to "alfred:home:action_results" stream
          - Push observation to scratchpad queue
          - XACK the entry
```

Design decisions:
- Redis consumer groups (XREADGROUP) for at-least-once delivery
- ACK only after successful processing
- block=5000ms prevents busy-waiting
- Single consumer for Phase 1; consumer group enables horizontal scaling later

### 2. home-service MCP Server (`home-service/app/server.py`)

Thin FastAPI application:

- `POST /mcp` — JSON-RPC endpoint receiving `{"method": "...", "params": {...}, "id": "..."}`
- Dispatches to registered tool functions (dim_lights, turn_off_lights, set_scene)
- On startup: calls `client.register()` to announce tools to Redis registry
- Returns JSON-RPC response

### 3. Home Assistant Config (`home-assistant/`)

Separate git repo. Docker Compose runs HA with:
- MQTT integration pointing to Mosquitto
- Template entities:
  - `light.living_room` — dimmable virtual light
  - `light.bedroom` — dimmable virtual light
  - `media_player.living_room_tv` — virtual TV with on/off
- Empty `automations.yaml` (Alfred handles all automation)

HA publishes state changes to MQTT automatically when entities change via the UI.

### 4. Dev Scripts (`scripts/`)

For Apple container runtime on macOS:

- `dev-up.sh` — Creates `alfred-net` network, starts Redis, Mosquitto, builds and runs Bridge + Reflex + home-service
- `dev-down.sh` — Stops and removes all Alfred containers
- `dev-logs.sh` — Tails logs from all containers
- `smoke-test.sh` — Publishes a test MQTT event, verifies action result appears on Redis stream

### 5. Containerfiles

**alfred/Containerfile** — Single image for both Bridge and Reflex Runner:
- Python 3.13-slim base
- Install dependencies with uv
- Entrypoint selectable via CMD: `python -m bus` or `python -m core.reflex`

**home-service/Containerfile:**
- Python 3.13-slim base
- Install with uv (including alfred-sdk + fastapi)
- CMD: uvicorn app.server:app

### 6. Docker Compose (`docker-compose.yml`)

Production compose for CachyOS server. Includes all services with proper depends_on, health checks, restart policies, and volume mounts.

## File Changes

### Alfred Monorepo (new/modified)

| File | Status | Purpose |
|------|--------|---------|
| `core/reflex/__main__.py` | NEW | Orchestration loop |
| `Containerfile` | NEW | OCI image for bridge + reflex |
| `.env.example` | NEW | Documented env vars |
| `scripts/dev-up.sh` | NEW | Apple container startup |
| `scripts/dev-down.sh` | NEW | Tear down dev containers |
| `scripts/dev-logs.sh` | NEW | Tail container logs |
| `scripts/smoke-test.sh` | NEW | End-to-end smoke test |
| `docker-compose.yml` | MODIFIED | Full production compose |
| `bus/Dockerfile` | RENAME | → `bus/Containerfile` (unused, image unified) |

### home-service (new/modified)

| File | Status | Purpose |
|------|--------|---------|
| `app/server.py` | NEW | FastAPI MCP endpoint |
| `Containerfile` | NEW | OCI image |
| `pyproject.toml` | MODIFIED | Add fastapi, uvicorn deps |
| `Dockerfile` | RENAME | → Containerfile |

### home-assistant (new repo)

| File | Status | Purpose |
|------|--------|---------|
| `docker-compose.yml` | NEW | HA container |
| `scripts/dev-up.sh` | NEW | Apple container alternative |
| `config/configuration.yaml` | NEW | MQTT + template entities |
| `config/automations.yaml` | NEW | Empty placeholder |

## Error Handling

| Failure | Behavior |
|---------|----------|
| Ollama down | Log error, don't ACK message, retry on next XREADGROUP cycle |
| home-service down | HomeAgent returns ActionResult(status="error"), event ACKed |
| Redis down | Processes fail to connect and exit; container restarts handle recovery |
| HA down | home-service HTTP call fails, returns error ActionResult |
| Malformed event | Pydantic validation error logged, message ACKed (skip bad data) |

## Testing

Existing 39 tests remain unchanged (all mocked).

New tests:
- **Reflex Runner event loop** — mocked Redis + mocked engine, verify XREADGROUP/XACK flow
- **home-service MCP endpoint** — httpx test client against FastAPI app
- **Smoke test script** — live end-to-end: publish MQTT event → verify Redis action result

## Environment Variables

```
REDIS_HOST=redis          # or localhost for native dev
REDIS_PORT=6379
MQTT_HOST=mosquitto       # or localhost for native dev
MQTT_PORT=1883
OLLAMA_HOST=http://host.containers.internal:11434  # or http://localhost:11434
OLLAMA_MODEL=gpt-oss:20b
HA_HOST=http://homeassistant:8123
HA_TOKEN=<long-lived access token from HA>
RESEARCH_VAULT_PATH=./research
```

## Out of Scope

- SigNoz/OpenTelemetry integration (infrastructure exists in compose, wiring is Phase 2)
- Multi-consumer scaling (consumer group is set up but single worker for now)
- SSL/TLS for MQTT or Redis (dev environment)
- Trigger Engine (Phase 2)
