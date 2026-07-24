# Alfred

[![CI](https://github.com/anirudhlath/alfred/actions/workflows/ci.yml/badge.svg)](https://github.com/anirudhlath/alfred/actions/workflows/ci.yml)

An ambient, voice-first multi-agent system for smart environments. Inspired by Alfred Pennyworth.

Alfred processes real-time events from smart home devices, responds to voice and text commands, and proactively manages your environment — all while maintaining the demeanor of a proper English butler.

**What can Alfred do?** See the [Product Requirements Document](docs/PRD.md) — vision, product principles, and a maintained catalog of every capability with its current status.

## Architecture

Alfred uses a **dual-process cognitive model**:

- **System 1 (Reflex Engine)** — a local SLM (Ollama) handles the fast path. State-change events pass through the SLM in sub-500ms to decide whether an action is needed.
- **System 2 (Conscious Engine)** — Claude handles complex reasoning, multi-step planning, and conversational requests via an agentic tool-use loop.

```mermaid
flowchart TB
    subgraph BUS["Event Bus"]
        MQTT["MQTT broker (Mosquitto)"] <--> Bridge["MQTT–Redis Bridge"]
        Bridge <--> Streams[("Redis Streams<br/>consumer groups, Pydantic-validated events")]
    end

    HA["Home Assistant"] <--> MQTT

    subgraph CHANNELS["Interaction Channels"]
        WebAuthn["WebAuthn passkeys +<br/>Tailscale network gating"] --> WebChannel
        WebChannel["Web Channel<br/>FastAPI + WebSocket + PWA<br/>WhisperSTT / PiperTTS voice"]
        SignalBridge["Signal bridge<br/>(separate service)"]
    end

    WebChannel -->|UserRequest| Streams
    SignalBridge -->|UserRequest| Streams
    Streams -->|AlfredResponse| WebChannel

    subgraph CORE["Dual-Process Core"]
        Reflex["Reflex Engine — System 1<br/>local SLM (Ollama), fast path"]
        Triggers["Trigger Engine<br/>LLM-created triggers,<br/>deterministic firing"]
        Conscious["Conscious Engine — System 2<br/>Claude agentic tool-use loop<br/>sessions, cost caps, identity gate"]
    end

    Streams -->|StateChangedEvent| Reflex
    Streams -->|StateChangedEvent / TriggerFired| Triggers
    Streams -->|UserRequest| Conscious
    Conscious -->|AlfredResponse| Streams

    Reflex -->|ActionRequest| Agents
    Triggers -->|ActionRequest| Agents
    Conscious -->|ActionRequest| Agents

    Agents["Domain Agents<br/>(HomeAgent)"] -->|"JSON-RPC (MCP)"| Services["Microservices<br/>home-service via alfred-sdk"]
    Services --> HA

    subgraph MEMORY["Three-Layer Memory"]
        Episodic["Episodic<br/>Redis hot + SQLite cold,<br/>embedding search"]
        Semantic["Semantic<br/>Markdown profiles + preferences"]
        Procedural["Procedural<br/>YAML routines"]
        Librarian["Librarian<br/>nightly consolidation,<br/>pattern detection, decay"]
    end

    Reflex -->|ReflexObservation| Ingestor["Memory Ingestor"] --> Episodic
    Conscious <-->|recall / context assembly| MEMORY
    Librarian --> Episodic
    Librarian --> Semantic
    Librarian --> Procedural

    Conscious --> Integrations["IntegrationRegistry<br/>weather, calendar, health, robinhood"]
    Conscious --> Notify["Notification Dispatcher<br/>APNs push, WebSocket, voice TTS"]

    Evals["Evals Runner<br/>DeepEval metrics + mocked regression suite"] -.-> Reflex
    Evals -.-> Conscious
    Telemetry["Telemetry<br/>OpenTelemetry + latency/token CSVs"] -.-> Reflex
```

### Four Pillars

1. **Proactivity** — triggers are created dynamically by the LLM, never hardcoded
2. **Decoupling** — microservices are sovereign apps; `alfred-sdk` is the only bridge
3. **Deterministic Communication** — all inter-agent messages are Pydantic-validated JSON
4. **Stateful Memory** — three-layer biologically-inspired memory with nightly Librarian consolidation

### Components

| Component | Purpose | Entry Point |
|-----------|---------|-------------|
| MQTT-Redis Bridge | Edge transport adapter | `python -m bus` |
| Reflex Engine | System 1 fast path (SLM) | `python -m core.reflex` |
| Trigger Engine | Proactive automation | `python -m core.triggers` |
| Conscious Engine | System 2 reasoning (Claude) | `python -m core.conscious` |
| Web Channel | FastAPI + WebSocket server | `python -m core.channels` |
| Librarian | Nightly memory consolidation | `python -m core.librarian` |
| Unified Runner | Multi-process supervisor | `python -m runner` |

### Memory System

- **Episodic** — Redis (hot) + SQLite (cold) with embedding-based semantic search
- **Semantic** — Markdown profiles and preferences (read-only at runtime)
- **Procedural** — YAML routines encoding learned behavioral sequences

### Integrations

Weather (Open-Meteo), Apple Calendar (CalDAV), Apple Health, Robinhood — all registered via `IntegrationRegistry` with decorator-based discovery.

### Interaction Channels

- **Web PWA** — chat + voice via WebSocket
- **Voice** — WhisperSTT (local) + PiperTTS (local)
- **Signal** — separate bridge service (not yet public)

## Setup

### Quickstart

```bash
git clone https://github.com/anirudhlath/alfred && cd alfred
uv venv --python 3.13 && uv pip install -e ".[dev]"

cp .env.example .env          # fill in OPENROUTER_API_KEY (+ HA_TOKEN for home control)
uv run alfredctl doctor       # validate .env before starting (optional but recommended)
uv run alfredctl up           # build the image + start everything, prints the URL
```

That's it. `alfredctl up` builds the fat image, auto-clones the `home-service` sibling if
it's missing, starts the container, and prints the reachable URL (default
`http://localhost:8081`). The full guide is [`docs/deployment.md`](docs/deployment.md).

### What you configure

Only one value is strictly required for a reasoning assistant; everything else has a
working default.

- **`OPENROUTER_API_KEY`** — System 2 (reasoning/conversation). Get one at
  [openrouter.ai/keys](https://openrouter.ai/keys). (`CLAUDE_API_KEY` also works.)
- **`HA_TOKEN`** — only for Home Assistant control. Mint it in HA → profile → Security →
  Long-lived access tokens.
- **Local inference** — for the fast System 1 path, a local [Ollama](https://ollama.com)
  (`ollama pull gpt-oss:20b`) or any OpenAI-compatible server (vLLM/LM Studio).

Sensible defaults handle the rest: memory embeddings use an **ungated** model (no HF token
needed), the secrets passphrase is **generated and persisted** on first boot, and host
services reachable at `localhost` are **auto-rewritten** to the container gateway.

### Prerequisites

- A container runtime — Docker, Apple `container` (macOS), or Podman; `alfredctl`
  auto-detects whichever is on `PATH`.
- The [`alfred-home-service`](https://github.com/anirudhlath/alfred-home-service) sibling
  repo — `alfredctl build` clones it automatically as `../home-service` if absent.

### Data modes

`--mode` picks the data lifecycle:

| Mode | Use case | State |
|------|----------|-------|
| `persistent` (default) | Production / self-hosting | Survives restarts |
| `ephemeral` | Worktree / PR testing | Thrown away on teardown |
| `seed` | Demo / QA | `ephemeral` + dummy fixtures pre-loaded |

Other commands: `uv run alfredctl down`, `logs -f`, `shell`, `urls`, `doctor`, and `smoke`.
See [`docs/containerization.md`](docs/containerization.md) for the full command reference.

### Production (Docker Compose)

```bash
cp .env.example .env                          # fill it in
uv run alfredctl build --tag alfred:latest
docker compose up -d
```

The compose-of-one needs only your `.env` — no passphrase or host-networking flags to
remember (the container generates the passphrase and rewrites `localhost` hosts itself).

### Native (non-container) dev

`uv run python -m runner` also runs directly against your own Redis Stack + Mosquitto —
no container, no wrapper script required.

```bash
uv sync --extra dev        # core + dev tooling
uv sync --all-extras       # everything (voice, integrations, memory, evals)

# Or pick extras individually
uv sync --extra dev --extra voice         # WhisperSTT + PiperTTS
uv sync --extra dev --extra integrations  # Calendar, Robinhood
uv sync --extra dev --extra memory        # Sentence transformers, sqlite-vec
uv sync --extra dev --extra evals         # DeepEval
```

```bash
# With Redis Stack + Mosquitto already running:
cd ../home-service && uv run uvicorn app.server:app --port 8000   # separate repo
uv run python -m runner                                            # all Alfred services
```

Or run services individually:

```bash
uv run python -m bus              # MQTT-Redis bridge
uv run python -m core.reflex     # Reflex Engine
uv run python -m core.triggers   # Trigger Engine
uv run python -m core.conscious  # Conscious Engine
uv run python -m core.channels   # Web channel + PWA
```

### Smoke Test

```bash
bash scripts/smoke-test.sh    # native — requires the stack already running
uv run alfredctl smoke        # containerized — boots seed mode, verifies, tears down
uv run alfredctl smoke --deep # also drives a real request through System 2 (needs a key)
```

## Evals

```bash
uv run python -m evals run                  # System 1 (requires Ollama)
uv run python -m evals regression           # System 1 regression (mocked, CI-safe)
uv run python -m evals conscious            # System 2 (dry-run)
uv run python -m evals demo                 # Good Morning end-to-end demo
uv run python -m evals run -n 5             # Repeat 5x with aggregate stats
uv run python -m evals compare <run1> <run2> # Compare two runs
```

## Development

```bash
# Lint + format
uv run ruff check . --fix
uv run ruff format .

# Type check
uv run mypy bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/

# Tests
uv run pytest
```

## Configuration

All configuration is via environment variables (`.env` auto-loaded). `.env.example` is the
annotated source of truth, split into a short **REQUIRED** section and defaulted
**OPTIONAL** settings; the most common ones:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | — | System 2 cloud LLM key (required for reasoning) |
| `CLAUDE_MODEL` | `openrouter/anthropic/claude-sonnet-4` | System 2 model (LiteLLM string) |
| `REFLEX_BACKEND` | `ollama` | System 1 backend: `ollama` \| `openai` |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama API (localhost auto-rewritten in-container) |
| `HA_HOST` | `http://localhost:8123` | Home Assistant base URL |
| `HA_TOKEN` | — | HA long-lived token (required for home control) |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Ungated by default; `EMBEDDING_DIM` auto-tracks it |
| `ALFRED_TRUSTED_NETWORKS` | — | Extra trusted CIDRs (loopback + LAN + Tailscale trusted by default) |
| `ALFRED_SECRETS_PASSPHRASE` | auto-generated | Keyring passphrase; persisted on first boot if unset |

See [`docs/deployment.md`](docs/deployment.md) for the guided walkthrough and
[`docs/containerization.md`](docs/containerization.md) for the containerization design.

## Related Repos

- [`alfred-home-service`](https://github.com/anirudhlath/alfred-home-service) — Home Assistant wrapper microservice built on `alfred-sdk`
- [`alfred-ios`](https://github.com/anirudhlath/alfred-ios) — SwiftUI voice + chat companion app
- `alfred-signal-bridge` — Signal messaging channel (not yet public)

## License

[AGPL-3.0-or-later](LICENSE) © 2025–2026 Anirudh Lath

Briefly published under MIT during initial release prep (July 13–15, 2026); relicensed to AGPL-3.0-or-later on 2026-07-15. Contributions require a one-time [CLA](CLA.md) signature and a per-commit [DCO](CONTRIBUTING.md) sign-off.

<!-- protection smoke test: ci-ok ruleset gate verification (task 13) -->
