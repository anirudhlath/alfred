# Phase 1 Live Runner — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Phase 1 runnable end-to-end: Home Assistant state change → Reflex Engine → action executed — all on a local Mac with Apple container runtime.

**Architecture:** An orchestration loop (`core/reflex/__main__.py`) reads events from Redis Streams via consumer groups, runs the Reflex Engine, dispatches actions through Home Agent to home-service, and logs observations. Infrastructure runs in OCI containers via Apple's `container` CLI on macOS or Docker Compose on Linux.

**Tech Stack:** Python 3.13, FastAPI, uvicorn, Redis Streams (XREADGROUP), MQTT, Ollama (gpt-oss:20b), Apple container runtime (macOS 26), Docker Compose (production), Home Assistant.

**Spec:** `docs/superpowers/specs/2026-03-10-phase1-live-runner-design.md`

---

## Chunk 1: Code (TDD)

### Task 1: Environment Configuration

**Files:**
- Update: `alfred/.env.example` (already exists with `llama3:8b` defaults — update to match spec)
- Verify: `alfred/.gitignore` includes `.env`

- [ ] **Step 1: Update .env.example with all documented env vars**

```env
# Alfred Environment Configuration
# Copy to .env and fill in values.

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379

# MQTT (Mosquitto)
MQTT_HOST=localhost
MQTT_PORT=1883

# Ollama (local SLM inference)
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=gpt-oss:20b

# Home Assistant
HA_HOST=http://localhost:8123
HA_TOKEN=

# Research vault
RESEARCH_VAULT_PATH=./research

# Telemetry (SigNoz/OpenTelemetry)
SIGNOZ_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

- [ ] **Step 2: Create .env from example (gitignored)**

```bash
cp .env.example .env
```

Verify `.env` is in `.gitignore`. If not, add it.

- [ ] **Step 3: Commit**

```bash
git add .env.example .gitignore
git commit -m "Add .env.example with documented environment variables"
```

---

### Task 2: AlfredClient Tool Dispatch

The home-service MCP server needs to dispatch incoming JSON-RPC calls to registered tool functions. Add a `dispatch()` method and internal `_tool_fns` registry to `AlfredClient`.

**Files:**
- Modify: `sdk/alfred_sdk/client.py`
- Test: `sdk/tests/test_client.py`

**Context:** The `@client.tool()` decorator currently stores metadata in `self.tools` (list of dicts) but discards the callable reference. We need to keep a `name → function` mapping so the MCP server can dispatch calls by tool name.

**Important:** The `mcp_tool` decorator wraps functions with a sync wrapper, so `dispatch()` must handle both sync and async callables using `inspect.isawaitable()`. Store the original `fn` (not the mcp_tool wrapper) in `_tool_fns` to avoid unnecessary indirection.

- [ ] **Step 1: Write the failing tests**

Add to `sdk/tests/test_client.py`:

```python
@pytest.mark.asyncio
async def test_dispatch_calls_registered_async_tool() -> None:
    from sdk.alfred_sdk.client import AlfredClient

    client = AlfredClient(service_name="test-service")

    @client.tool(name="test.greet", description="Say hello")
    async def greet(name: str) -> dict[str, str]:
        return {"message": f"Hello, {name}!"}

    result = await client.dispatch("test.greet", {"name": "Alfred"})
    assert result == {"message": "Hello, Alfred!"}


@pytest.mark.asyncio
async def test_dispatch_calls_registered_sync_tool() -> None:
    from sdk.alfred_sdk.client import AlfredClient

    client = AlfredClient(service_name="test-service")

    @client.tool(name="test.add", description="Add two numbers")
    def add(a: int, b: int) -> dict[str, int]:
        return {"sum": a + b}

    result = await client.dispatch("test.add", {"a": 2, "b": 3})
    assert result == {"sum": 5}


@pytest.mark.asyncio
async def test_dispatch_unknown_tool_raises() -> None:
    from sdk.alfred_sdk.client import AlfredClient

    client = AlfredClient(service_name="test-service")

    with pytest.raises(KeyError, match="Unknown tool"):
        await client.dispatch("nonexistent.tool", {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest sdk/tests/test_client.py::test_dispatch_calls_registered_async_tool sdk/tests/test_client.py::test_dispatch_calls_registered_sync_tool sdk/tests/test_client.py::test_dispatch_unknown_tool_raises -v`

Expected: FAIL — `AlfredClient` has no `dispatch` method.

- [ ] **Step 3: Implement dispatch in AlfredClient**

In `sdk/alfred_sdk/client.py`, add `import inspect` at top, add `_tool_fns` dict to `__init__`, store original `fn` in `tool()`, and add `dispatch()` method:

```python
# At top of file, add:
import inspect

# In __init__, add:
self._tool_fns: dict[str, Callable[..., Any]] = {}

# In tool() decorator, after self.tools.append(meta), add:
self._tool_fns[name] = fn  # Store original fn, not the mcp_tool wrapper

# New method:
async def dispatch(self, method: str, params: dict[str, Any]) -> Any:
    """Dispatch an MCP tool call to the registered handler."""
    fn = self._tool_fns.get(method)
    if fn is None:
        raise KeyError(f"Unknown tool: {method}")
    result = fn(**params)
    if inspect.isawaitable(result):
        return await result
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest sdk/tests/test_client.py -v`

Expected: All tests pass.

- [ ] **Step 5: Run full test suite**

Run: `pytest --tb=short -q`

Expected: All tests pass (no regressions).

- [ ] **Step 6: Commit**

```bash
git add sdk/alfred_sdk/client.py sdk/tests/test_client.py
git commit -m "Add dispatch() method to AlfredClient for MCP tool routing"
```

---

### Task 3: home-service MCP Server

Add a FastAPI application to home-service that serves the MCP endpoint at `POST /mcp`. On startup, registers tools with Alfred's Redis registry.

**Files:**
- Create: `home-service/app/server.py`
- Create: `home-service/tests/test_server.py`
- Modify: `home-service/pyproject.toml`

**Context:**
- `home-service/alfred_ext/register.py` already creates an `AlfredClient` instance (`client`) and registers three tools (`smart_home.dim_lights`, `smart_home.turn_off_lights`, `smart_home.set_scene`).
- The server imports this client and uses `client.dispatch()` (from Task 2) to route incoming calls.
- JSON-RPC format: `{"method": "smart_home.dim_lights", "params": {"room": "living_room", "level": 20}, "id": "req-123"}`

- [ ] **Step 1: Add FastAPI + uvicorn dependencies to pyproject.toml**

In `home-service/pyproject.toml`, add to `dependencies`:

```toml
[project]
dependencies = [
    "httpx>=0.27",
    "fastapi>=0.115",
    "uvicorn>=0.34",
]
```

Install: `cd home-service && uv pip install -e ".[dev,alfred]"`

- [ ] **Step 2: Write the failing tests**

Create `home-service/tests/test_server.py`:

```python
"""Tests for the MCP JSON-RPC server."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_mcp_endpoint_dispatches_tool_call() -> None:
    # Mock HA client so tools don't make real HTTP calls
    with patch("alfred_ext.register.ha") as mock_ha:
        mock_ha.call_service = AsyncMock(return_value=[])

        from app.server import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/mcp",
                json={
                    "method": "smart_home.dim_lights",
                    "params": {"room": "living_room", "level": 20},
                    "id": "req-001",
                },
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "req-001"
    assert "result" in data
    assert data["result"]["entity_id"] == "light.living_room"


@pytest.mark.asyncio
async def test_mcp_endpoint_unknown_method_returns_error() -> None:
    from app.server import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/mcp",
            json={
                "method": "nonexistent.tool",
                "params": {},
                "id": "req-002",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "req-002"
    assert "error" in data


@pytest.mark.asyncio
async def test_health_endpoint() -> None:
    from app.server import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd home-service && python -m pytest tests/test_server.py -v`

Expected: FAIL — `app.server` module doesn't exist.

- [ ] **Step 4: Implement the MCP server**

Create `home-service/app/server.py`:

```python
"""MCP JSON-RPC server for home-service.

Receives tool calls from Alfred's Home Agent and dispatches
to registered tool handlers via AlfredClient.dispatch().
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class McpRequest(BaseModel):
    """JSON-RPC style MCP tool call request."""

    method: str
    params: dict[str, Any] = {}
    id: str


class McpResponse(BaseModel):
    """JSON-RPC style MCP tool call response."""

    id: str
    result: dict[str, Any] | None = None
    error: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Register tools with Alfred on startup."""
    try:
        from alfred_ext.register import client

        await client.register()
        logger.info("Registered tools with Alfred registry")
    except Exception as e:
        # Registration failure is non-fatal — Alfred may not be running yet
        logger.warning("Could not register with Alfred: %s", e)
    yield


app = FastAPI(title="home-service", lifespan=lifespan)


@app.post("/mcp")
async def mcp_endpoint(request: McpRequest) -> McpResponse:
    """Handle an MCP tool call."""
    from alfred_ext.register import client

    try:
        result = await client.dispatch(request.method, request.params)
        return McpResponse(
            id=request.id,
            result=result if isinstance(result, dict) else {"data": result},
        )
    except KeyError as e:
        return McpResponse(id=request.id, error=str(e))
    except Exception as e:
        logger.error("Tool execution failed: %s", e)
        return McpResponse(id=request.id, error=str(e))


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "service": "home-service"}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd home-service && python -m pytest tests/test_server.py -v`

Expected: All 3 tests pass.

- [ ] **Step 6: Run full home-service test suite**

Run: `cd home-service && python -m pytest -v`

Expected: All tests pass (including existing ha_client tests).

- [ ] **Step 7: Commit**

```bash
cd home-service
git add app/server.py tests/test_server.py pyproject.toml
git commit -m "Add FastAPI MCP server with JSON-RPC endpoint and health check"
```

---

### Task 4: Reflex Runner Orchestration Loop

The main event loop that ties everything together: reads events from Redis Streams, runs the Reflex Engine, dispatches actions, logs observations.

**Files:**
- Create: `core/reflex/__main__.py`
- Create: `core/reflex/tests/test_runner.py`

**Context:**
- Uses Redis consumer groups (XREADGROUP) for reliable message delivery
- ACKs messages only after successful processing
- Runs ScratchpadWriter and telemetry flush as background tasks
- Existing components used: `ReflexEngine`, `HomeAgent`, `ScratchpadWriter`, `AlfredConfig`

- [ ] **Step 1: Write the failing tests**

Create `core/reflex/tests/test_runner.py`:

```python
"""Tests for the Reflex Runner orchestration loop."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bus.schemas.events import ActionRequest, StateChangedEvent


@pytest.mark.asyncio
async def test_process_stream_entry_produces_action() -> None:
    """A valid state change event should be processed and produce an action."""
    from core.reflex.runner import process_stream_entry

    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="media_player.living_room_tv",
        old_state="off",
        new_state="on",
        attributes={"friendly_name": "Living Room TV"},
    )
    event_json = event.model_dump_json()

    mock_engine = AsyncMock()
    mock_engine.process_event.return_value = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="smart_home.dim_lights",
        parameters={"room": "living_room", "level": 20},
    )

    mock_agent = AsyncMock()
    mock_agent.execute_action.return_value = MagicMock(
        model_dump_json=MagicMock(return_value='{"status":"success"}')
    )

    mock_redis = AsyncMock()

    result = await process_stream_entry(
        entry_data={"event": event_json},
        engine=mock_engine,
        agent=mock_agent,
        redis=mock_redis,
        result_stream="alfred:home:action_results",
        scratchpad_queue="alfred:scratchpad:queue",
    )

    assert result is True
    mock_engine.process_event.assert_called_once()
    mock_agent.execute_action.assert_called_once()
    mock_redis.xadd.assert_called_once()
    mock_redis.lpush.assert_called_once()


@pytest.mark.asyncio
async def test_process_stream_entry_no_action() -> None:
    """An irrelevant event should not produce an action."""
    from core.reflex.runner import process_stream_entry

    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="sensor.temperature",
        new_state="22.5",
    )

    mock_engine = AsyncMock()
    mock_engine.process_event.return_value = None

    mock_agent = AsyncMock()
    mock_redis = AsyncMock()

    result = await process_stream_entry(
        entry_data={"event": event.model_dump_json()},
        engine=mock_engine,
        agent=mock_agent,
        redis=mock_redis,
        result_stream="alfred:home:action_results",
        scratchpad_queue="alfred:scratchpad:queue",
    )

    assert result is False
    mock_engine.process_event.assert_called_once()
    mock_agent.execute_action.assert_not_called()
    mock_redis.xadd.assert_not_called()


@pytest.mark.asyncio
async def test_process_stream_entry_malformed_event() -> None:
    """A malformed event should be logged and skipped, not crash."""
    from core.reflex.runner import process_stream_entry

    mock_engine = AsyncMock()
    mock_agent = AsyncMock()
    mock_redis = AsyncMock()

    result = await process_stream_entry(
        entry_data={"event": "not valid json {{{"},
        engine=mock_engine,
        agent=mock_agent,
        redis=mock_redis,
        result_stream="alfred:home:action_results",
        scratchpad_queue="alfred:scratchpad:queue",
    )

    assert result is False
    mock_engine.process_event.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_consumer_group_creates_if_missing() -> None:
    """Consumer group creation should be idempotent."""
    from core.reflex.runner import ensure_consumer_group

    mock_redis = AsyncMock()
    # First call succeeds (group doesn't exist)
    mock_redis.xgroup_create = AsyncMock()

    await ensure_consumer_group(mock_redis, "alfred:home:state_changed", "reflex-engine")

    mock_redis.xgroup_create.assert_called_once_with(
        "alfred:home:state_changed", "reflex-engine", id="0", mkstream=True
    )


@pytest.mark.asyncio
async def test_ensure_consumer_group_ignores_exists_error() -> None:
    """If consumer group already exists, should not raise."""
    import redis.asyncio as aioredis

    from core.reflex.runner import ensure_consumer_group

    mock_redis = AsyncMock()
    mock_redis.xgroup_create = AsyncMock(
        side_effect=aioredis.ResponseError("BUSYGROUP Consumer Group name already exists")
    )

    # Should not raise
    await ensure_consumer_group(mock_redis, "alfred:home:state_changed", "reflex-engine")


@pytest.mark.asyncio
async def test_process_stream_entry_handles_bytes_keys() -> None:
    """Redis returns bytes keys — verify they're handled."""
    from core.reflex.runner import process_stream_entry

    event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="sensor.temperature",
        new_state="22.5",
    )

    mock_engine = AsyncMock()
    mock_engine.process_event.return_value = None
    mock_agent = AsyncMock()
    mock_redis = AsyncMock()

    result = await process_stream_entry(
        entry_data={b"event": event.model_dump_json().encode()},
        engine=mock_engine,
        agent=mock_agent,
        redis=mock_redis,
        result_stream="alfred:home:action_results",
        scratchpad_queue="alfred:scratchpad:queue",
    )

    assert result is False
    mock_engine.process_event.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest core/reflex/tests/test_runner.py -v`

Expected: FAIL — `core.reflex.runner` module doesn't exist.

- [ ] **Step 3: Implement the runner module**

Create `core/reflex/runner.py`:

```python
"""Reflex Runner — orchestration loop for the System 1 pipeline.

Reads events from Redis Streams (consumer group), runs the Reflex Engine,
dispatches actions via Home Agent, and logs observations to the scratchpad.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis

from bus.schemas.events import StateChangedEvent
from core.reflex.engine import ReflexEngine
from domains.home.home_agent import HomeAgent

logger = logging.getLogger(__name__)


async def ensure_consumer_group(
    redis: aioredis.Redis[Any],
    stream: str,
    group: str,
) -> None:
    """Create a consumer group if it doesn't already exist."""
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
        logger.info("Created consumer group '%s' on stream '%s'", group, stream)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" in str(e):
            logger.debug("Consumer group '%s' already exists", group)
        else:
            raise


async def process_stream_entry(
    entry_data: Mapping[str | bytes, str | bytes],
    engine: ReflexEngine,
    agent: HomeAgent,
    redis: aioredis.Redis[Any],
    result_stream: str,
    scratchpad_queue: str,
) -> bool:
    """Process a single Redis Stream entry. Returns True if an action was taken.

    Raises on retriable errors (e.g., Ollama down) so the caller can
    choose not to ACK the message. Returns False for skip-worthy errors
    (malformed event, no action needed).
    """
    raw_event = entry_data.get("event") or entry_data.get(b"event")
    if raw_event is None:
        logger.warning("Stream entry missing 'event' field: %s", entry_data)
        return False

    event_str = raw_event.decode() if isinstance(raw_event, bytes) else raw_event

    try:
        event = StateChangedEvent.model_validate_json(event_str)
    except Exception as e:
        logger.error("Failed to parse event: %s — %s", e, event_str[:200])
        return False

    # NOTE: engine.process_event() calls Ollama. If Ollama is down, this
    # raises (httpx.ConnectError, etc.). We intentionally let it propagate
    # so the caller does NOT ACK the message — Redis will redeliver it.
    action = await engine.process_event(event)
    if action is None:
        logger.debug("No action for event %s", event.entity_id)
        return False

    result = await agent.execute_action(action)

    await redis.xadd(result_stream, {"event": result.model_dump_json()})

    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    observation = f"{timestamp} [reflex] {action.tool_name}({action.parameters}) → {result.status}"
    await redis.lpush(scratchpad_queue, observation)

    logger.info(
        "Action: %s → %s (status=%s)", event.entity_id, action.tool_name, result.status
    )
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest core/reflex/tests/test_runner.py -v`

Expected: All 6 tests pass.

- [ ] **Step 5: Implement the __main__.py entry point**

Create `core/reflex/__main__.py`:

```python
"""Entry point for the Reflex Runner service.

Usage: python -m core.reflex
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

import redis.asyncio as aioredis

from core.memory.scratchpad_writer import ScratchpadWriter
from core.reflex.engine import ReflexEngine
from core.reflex.runner import ensure_consumer_group, process_stream_entry
from domains.home.home_agent import HomeAgent
from sdk.alfred_sdk.telemetry import clear_telemetry_buffer, get_telemetry_buffer
from shared.config import AlfredConfig
from telemetry.collector import flush_to_csv

logger = logging.getLogger(__name__)

STREAM = "alfred:home:state_changed"
GROUP = "reflex-engine"
CONSUMER = "worker-1"
RESULT_STREAM = "alfred:home:action_results"
SCRATCHPAD_QUEUE = "alfred:scratchpad:queue"

_shutdown = asyncio.Event()


def _handle_signal() -> None:
    logger.info("Shutdown signal received")
    _shutdown.set()


async def flush_telemetry_periodically(
    config: AlfredConfig, interval: float = 30.0
) -> None:
    """Periodically flush the telemetry buffer to CSV."""
    while True:
        await asyncio.sleep(interval)
        buf = get_telemetry_buffer()
        if buf:
            entries = list(buf)
            clear_telemetry_buffer()
            flush_to_csv(entries, config.research_vault_path)
            logger.info("Flushed %d telemetry entries", len(entries))


async def run(config: AlfredConfig) -> None:
    """Main Reflex Runner event loop."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    r: aioredis.Redis[Any] = aioredis.from_url(config.redis_url)

    await ensure_consumer_group(r, STREAM, GROUP)

    engine = ReflexEngine(preferences_dir="core/memory/preferences")
    agent = HomeAgent(redis=r)
    writer = ScratchpadWriter(redis=r, queue_key=SCRATCHPAD_QUEUE)

    # Background tasks
    scratchpad_task = asyncio.create_task(writer.run())
    telemetry_task = asyncio.create_task(flush_telemetry_periodically(config))

    logger.info("Reflex Runner started. Listening on stream '%s'...", STREAM)

    try:
        while not _shutdown.is_set():
            entries: list[
                tuple[
                    bytes | str,
                    list[tuple[bytes | str, dict[bytes | str, bytes | str]]],
                ]
            ] = await r.xreadgroup(
                GROUP, CONSUMER, {STREAM: ">"}, count=10, block=5000
            )

            for _stream_key, stream_entries in entries:
                for entry_id, entry_data in stream_entries:
                    try:
                        await process_stream_entry(
                            entry_data=entry_data,
                            engine=engine,
                            agent=agent,
                            redis=r,
                            result_stream=RESULT_STREAM,
                            scratchpad_queue=SCRATCHPAD_QUEUE,
                        )
                        # ACK only on success — retriable errors (Ollama down)
                        # propagate as exceptions and the message stays pending
                        # for redelivery on next XREADGROUP cycle.
                        await r.xack(STREAM, GROUP, entry_id)
                    except Exception as e:
                        logger.error(
                            "Error processing entry %s: %s — will retry", entry_id, e
                        )
    finally:
        logger.info("Shutting down Reflex Runner...")
        scratchpad_task.cancel()
        telemetry_task.cancel()
        await r.aclose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    config = AlfredConfig.from_env()
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run full test suite**

Run: `pytest --tb=short -q`

Expected: All tests pass.

- [ ] **Step 7: Run linters**

```bash
ruff check . --fix && ruff format .
mypy bus/ core/ domains/ sdk/ shared/ telemetry/
```

Expected: Clean.

- [ ] **Step 8: Commit**

```bash
git add core/reflex/__main__.py core/reflex/runner.py core/reflex/tests/test_runner.py
git commit -m "Add Reflex Runner: orchestration loop with consumer groups and telemetry flush"
```

---

## Chunk 2: Containers + Infrastructure

### Task 5: Containerfiles (OCI-compliant)

Create OCI container images for both the alfred monorepo services and home-service.

**Files:**
- Create: `alfred/Containerfile` (replaces commented stubs in compose)
- Create: `home-service/Containerfile`
- Delete: `alfred/bus/Dockerfile` (consolidated into root Containerfile)

**Context:**
- The alfred Containerfile builds a single image that can run either the Bridge (`python -m bus`) or the Reflex Runner (`python -m core.reflex`) depending on the CMD.
- Apple container `build` command uses Containerfile by default.
- Use `uv` for dependency installation per project conventions.

- [ ] **Step 1: Create alfred/.containerignore**

```
.venv/
.git/
__pycache__/
*.pyc
.env
research/
docs/
tests/
*.egg-info/
.mypy_cache/
.ruff_cache/
```

- [ ] **Step 2: Create alfred/Containerfile**

Note: Source must be copied before `pip install` (editable install requires source). We use a non-editable install (`--no-deps` in second stage) for correct layer caching.

```dockerfile
# Alfred core services — Bridge and Reflex Runner
# Build: container build -t alfred .
# Run bridge:  container run --name bridge  ... alfred python -m bus
# Run reflex:  container run --name reflex  ... alfred python -m core.reflex

FROM python:3.13-slim

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy source
COPY pyproject.toml /app/
COPY bus/ /app/bus/
COPY core/ /app/core/
COPY domains/ /app/domains/
COPY sdk/ /app/sdk/
COPY shared/ /app/shared/
COPY telemetry/ /app/telemetry/

# Non-editable install (editable requires source present, non-editable is correct for containers)
RUN uv pip install --system --no-cache .

# Ensure all packages are importable from /app
ENV PYTHONPATH=/app

# Default: run the reflex runner
CMD ["python", "-m", "core.reflex"]
```

- [ ] **Step 3: Create home-service/Containerfile**

Note: alfred-sdk is not published to PyPI. The SDK source is copied from the workspace. The Docker Compose build context is set to the workspace root (`..`) so both `alfred/sdk/` and `home-service/` are accessible.

```dockerfile
# home-service — HA wrapper microservice
# Build context must be the workspace root (parent of alfred/ and home-service/)
# so we can access alfred/sdk/ for the unpublished alfred-sdk package.

FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install alfred-sdk from monorepo source (not on PyPI)
COPY alfred/sdk/ /tmp/alfred-sdk/
RUN uv pip install --system --no-cache /tmp/alfred-sdk/ && rm -rf /tmp/alfred-sdk/

# Install home-service dependencies
COPY home-service/pyproject.toml /app/
COPY home-service/app/ /app/app/
COPY home-service/alfred_ext/ /app/alfred_ext/
RUN uv pip install --system --no-cache .

EXPOSE 8000

CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 4: Delete obsolete bus/Dockerfile**

```bash
rm alfred/bus/Dockerfile
```

- [ ] **Step 5: Verify alfred image builds**

```bash
cd alfred
container build -t alfred .
```

Expected: Build completes successfully.

- [ ] **Step 6: Verify home-service image builds**

The home-service build context is the workspace root:
```bash
cd /Users/anirudhlath/code/private/alfred
container build -t home-service -f home-service/Containerfile .
```

Expected: Build completes successfully. alfred-sdk is installed from local source.

- [ ] **Step 7: Commit**

```bash
cd alfred
rm bus/Dockerfile
git add Containerfile .containerignore
git commit -m "Add OCI Containerfile for alfred services, remove obsolete bus/Dockerfile"

cd ../home-service
git add Containerfile
git commit -m "Add OCI Containerfile for home-service (SDK from monorepo source)"
```

---

### Task 6: Docker Compose (Production)

Update the existing docker-compose.yml to be a complete production deployment for the CachyOS server. All services, health checks, restart policies.

**Files:**
- Modify: `alfred/docker-compose.yml`

- [ ] **Step 1: Rewrite docker-compose.yml**

Note: Uses a named external network `alfred-net` so the separate HA compose project can join the same network. The home-service build context is the workspace root (`..`) so it can access `alfred/sdk/` for the unpublished alfred-sdk.

```yaml
# Production deployment — all services containerized.
# Usage:
#   docker network create alfred-net  # once
#   docker compose up -d
#
# For dev on macOS, use scripts/dev-up.sh with Apple container runtime instead.

services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
    restart: unless-stopped
    networks:
      - alfred-net

  mosquitto:
    image: eclipse-mosquitto:2
    ports:
      - "1883:1883"
    volumes:
      - ./infra/mosquitto.conf:/mosquitto/config/mosquitto.conf
    healthcheck:
      test: ["CMD-SHELL", "mosquitto_pub -t '$$SYS/health' -m ok -u '' -P '' || exit 1"]
      interval: 5s
      timeout: 3s
      retries: 5
    restart: unless-stopped
    networks:
      - alfred-net

  bridge:
    build:
      context: .
      dockerfile: Containerfile
    command: python -m bus
    depends_on:
      redis:
        condition: service_healthy
      mosquitto:
        condition: service_healthy
    env_file: .env
    environment:
      - REDIS_HOST=redis
      - MQTT_HOST=mosquitto
    restart: unless-stopped
    networks:
      - alfred-net

  reflex:
    build:
      context: .
      dockerfile: Containerfile
    command: python -m core.reflex
    depends_on:
      redis:
        condition: service_healthy
    env_file: .env
    environment:
      - REDIS_HOST=redis
      - MQTT_HOST=mosquitto
      - OLLAMA_HOST=http://host.docker.internal:11434
    restart: unless-stopped
    networks:
      - alfred-net

  home-service:
    build:
      context: ..
      dockerfile: home-service/Containerfile
    ports:
      - "8000:8000"
    depends_on:
      redis:
        condition: service_healthy
    env_file: .env
    environment:
      - REDIS_HOST=redis
      - HA_HOST=http://homeassistant:8123
    restart: unless-stopped
    networks:
      - alfred-net

networks:
  alfred-net:
    name: alfred-net
    external: true

volumes:
  redis_data:
```

- [ ] **Step 2: Verify compose config is valid**

```bash
docker compose config --quiet
```

Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "Update docker-compose.yml with full production deployment"
```

---

### Task 7: Apple Container Dev Scripts

Shell scripts for running infrastructure on macOS 26 using Apple's `container` CLI.

**Files:**
- Create: `alfred/scripts/dev-up.sh`
- Create: `alfred/scripts/dev-down.sh`
- Create: `alfred/scripts/dev-logs.sh`

**Context:**
- Apple container CLI has no compose equivalent — scripts manage individual containers.
- For dev, only infrastructure runs in containers (Redis, Mosquitto). Python services run natively for faster iteration.
- The `container system start` command must be run once to initialize the runtime.

- [ ] **Step 1: Create scripts/dev-up.sh**

```bash
#!/usr/bin/env bash
# Start Alfred infrastructure in Apple containers (macOS 26+).
# Python services (bridge, reflex, home-service) run natively for dev.
#
# Usage: ./scripts/dev-up.sh

set -euo pipefail

NETWORK="alfred-net"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "==> Starting Apple container runtime..."
container system start 2>/dev/null || true

# Wait for system to be ready
for i in $(seq 1 10); do
    if container system status &>/dev/null; then
        break
    fi
    echo "    Waiting for container runtime... ($i/10)"
    sleep 2
done

echo "==> Creating network '$NETWORK'..."
container network create "$NETWORK" 2>/dev/null || echo "    Network already exists"

echo "==> Starting Redis..."
if ! container inspect redis &>/dev/null; then
    container run -d \
        --name redis \
        --network "$NETWORK" \
        -p 6379:6379 \
        redis:7-alpine
else
    container start redis 2>/dev/null || echo "    Redis already running"
fi

echo "==> Starting Mosquitto..."
if ! container inspect mosquitto &>/dev/null; then
    container run -d \
        --name mosquitto \
        --network "$NETWORK" \
        -p 1883:1883 \
        -v "$PROJECT_DIR/infra/mosquitto.conf:/mosquitto/config/mosquitto.conf" \
        eclipse-mosquitto:2
else
    container start mosquitto 2>/dev/null || echo "    Mosquitto already running"
fi

echo ""
echo "Infrastructure running:"
echo "  Redis:     localhost:6379"
echo "  Mosquitto: localhost:1883"
echo ""
echo "Now run the Python services natively:"
echo "  Terminal 1: python -m bus                # Bridge"
echo "  Terminal 2: python -m core.reflex        # Reflex Runner"
echo "  Terminal 3: cd ../home-service && uvicorn app.server:app --port 8000  # home-service"
```

- [ ] **Step 2: Create scripts/dev-down.sh**

```bash
#!/usr/bin/env bash
# Stop and remove Alfred infrastructure containers.
#
# Usage: ./scripts/dev-down.sh

set -euo pipefail

echo "==> Stopping Alfred containers..."
for name in redis mosquitto; do
    if container inspect "$name" &>/dev/null; then
        container stop "$name" 2>/dev/null || true
        container rm "$name" 2>/dev/null || true
        echo "    Removed $name"
    fi
done

echo "==> Removing network..."
container network rm alfred-net 2>/dev/null || true

echo "Done."
```

- [ ] **Step 3: Create scripts/dev-logs.sh**

```bash
#!/usr/bin/env bash
# Tail logs from all Alfred infrastructure containers.
#
# Usage: ./scripts/dev-logs.sh [container-name]

set -euo pipefail

if [ $# -gt 0 ]; then
    container logs "$1" 2>&1
else
    echo "==> Redis logs:"
    container logs redis 2>&1 | tail -5
    echo ""
    echo "==> Mosquitto logs:"
    container logs mosquitto 2>&1 | tail -5
fi
```

- [ ] **Step 4: Make scripts executable**

```bash
chmod +x scripts/dev-up.sh scripts/dev-down.sh scripts/dev-logs.sh
```

- [ ] **Step 5: Commit**

```bash
git add scripts/
git commit -m "Add Apple container dev scripts for macOS infrastructure"
```

---

## Chunk 3: Home Assistant + End-to-End

### Task 8: Home Assistant Repository

Set up a separate repo with Home Assistant configuration, template entities, and an MQTT automation that publishes state changes in Alfred's event format.

**Files (new repo: `home-assistant/`):**
- Create: `docker-compose.yml`
- Create: `scripts/dev-up.sh`
- Create: `config/configuration.yaml`
- Create: `config/automations.yaml`

**Context:**
- HA runs as a container on the `alfred-net` network.
- Template entities provide virtual devices (lights, TV) for testing.
- An automation triggers on any state change for our entities and publishes a `StateChangedEvent`-compatible JSON payload to MQTT topic `home/state_changed`.
- The user will need to complete HA onboarding (create account) via the web UI on first run, then create a long-lived access token for the home-service.

- [ ] **Step 1: Initialize the repository**

```bash
mkdir -p /Users/anirudhlath/code/private/alfred/home-assistant
cd /Users/anirudhlath/code/private/alfred/home-assistant
git init
```

- [ ] **Step 2: Create config/configuration.yaml**

```yaml
# Home Assistant configuration for Alfred development.
#
# Template entities provide virtual devices for testing.
# MQTT automation publishes state changes in Alfred's event format.

homeassistant:
  name: Alfred Dev
  unit_system: imperial
  time_zone: America/Denver

# MQTT broker connection
# - On alfred-net (containers): use "mosquitto" (container name)
# - On host (native dev): use "localhost"
mqtt:
  broker: mosquitto
  port: 1883

# Virtual input helpers (backing store for template entities)
# Brightness uses HA native 0-255 scale.
input_boolean:
  living_room_tv:
    name: Living Room TV
    icon: mdi:television

input_number:
  living_room_light_brightness:
    name: Living Room Light Brightness
    min: 0
    max: 255
    step: 1
    icon: mdi:brightness-6
  bedroom_light_brightness:
    name: Bedroom Light Brightness
    min: 0
    max: 255
    step: 1
    icon: mdi:brightness-6

# Template lights that wrap the input helpers.
# HA template lights: state returns "on"/"off", brightness returns 0-255.
# turn_on receives 'brightness' variable (0-255) from HA service calls.
template:
  - light:
      - name: "Living Room"
        unique_id: light_living_room
        state: >
          {{ 'on' if states('input_number.living_room_light_brightness') | int > 0 else 'off' }}
        brightness: >
          {{ states('input_number.living_room_light_brightness') | int }}
        turn_on:
          - action: input_number.set_value
            target:
              entity_id: input_number.living_room_light_brightness
            data:
              value: "{{ brightness | default(255) }}"
        turn_off:
          - action: input_number.set_value
            target:
              entity_id: input_number.living_room_light_brightness
            data:
              value: 0

      - name: "Bedroom"
        unique_id: light_bedroom
        state: >
          {{ 'on' if states('input_number.bedroom_light_brightness') | int > 0 else 'off' }}
        brightness: >
          {{ states('input_number.bedroom_light_brightness') | int }}
        turn_on:
          - action: input_number.set_value
            target:
              entity_id: input_number.bedroom_light_brightness
            data:
              value: "{{ brightness | default(255) }}"
        turn_off:
          - action: input_number.set_value
            target:
              entity_id: input_number.bedroom_light_brightness
            data:
              value: 0

# Load automations from separate file
automation: !include automations.yaml

# Enable the logger
logger:
  default: info
  logs:
    homeassistant.components.mqtt: debug
```

- [ ] **Step 3: Create config/automations.yaml**

This automation watches our template entities and publishes state changes to MQTT in Alfred's `StateChangedEvent` JSON format.

```yaml
# Publish state changes to Alfred via MQTT.
#
# When any watched entity changes state, this automation publishes
# a JSON payload to home/state_changed that matches Alfred's
# StateChangedEvent schema. The Bridge forwards this to Redis Streams.

- id: alfred_state_publisher
  alias: "Alfred: Publish State Changes"
  description: "Forward entity state changes to Alfred via MQTT"
  mode: queued
  max: 20
  triggers:
    - trigger: state
      entity_id:
        - light.living_room
        - light.bedroom
        - input_boolean.living_room_tv
  actions:
    - action: mqtt.publish
      data:
        topic: "home/state_changed"
        payload: >-
          {"source":"home-service","domain":"home","entity_id":"{{ trigger.entity_id }}","old_state":"{{ trigger.from_state.state if trigger.from_state else 'unknown' }}","new_state":"{{ trigger.to_state.state }}","attributes":{{ trigger.to_state.attributes | tojson }}}
```

- [ ] **Step 4: Create docker-compose.yml (for production/Linux)**

```yaml
# Home Assistant for Alfred development.
# Usage:
#   docker network create alfred-net  # once (or alfred's compose creates it)
#   docker compose up -d
#
# First run: visit http://localhost:8123 to complete onboarding.

services:
  homeassistant:
    image: ghcr.io/home-assistant/home-assistant:stable
    ports:
      - "8123:8123"
    volumes:
      - ./config:/config
    environment:
      - TZ=America/Denver
    restart: unless-stopped
    networks:
      - alfred-net

networks:
  alfred-net:
    name: alfred-net
    external: true
```

- [ ] **Step 5: Create scripts/dev-up.sh (Apple container)**

```bash
#!/usr/bin/env bash
# Start Home Assistant in an Apple container (macOS 26+).
#
# Requires: alfred-net network already created (by alfred/scripts/dev-up.sh)
#
# Usage: ./scripts/dev-up.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "==> Starting Home Assistant..."
if ! container inspect homeassistant &>/dev/null; then
    container run -d \
        --name homeassistant \
        --network alfred-net \
        -p 8123:8123 \
        -v "$PROJECT_DIR/config:/config" \
        -e TZ=America/Denver \
        ghcr.io/home-assistant/home-assistant:stable
else
    container start homeassistant 2>/dev/null || echo "    HA already running"
fi

echo ""
echo "Home Assistant running:"
echo "  Web UI: http://localhost:8123"
echo ""
echo "First run setup:"
echo "  1. Open http://localhost:8123 in your browser"
echo "  2. Create your account"
echo "  3. Go to Profile (bottom left) → Long-Lived Access Tokens"
echo "  4. Create a token and set it in alfred/.env as HA_TOKEN=<token>"
```

- [ ] **Step 6: Make scripts executable**

```bash
chmod +x scripts/dev-up.sh
```

- [ ] **Step 7: Create .gitignore**

```gitignore
# HA runtime files (generated on first run)
config/.storage/
config/.cloud/
config/home-assistant_v2.db
config/home-assistant_v2.db-wal
config/home-assistant_v2.db-shm
config/tts/
config/deps/
config/custom_components/
config/__pycache__/
```

- [ ] **Step 8: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/home-assistant
git add .
git commit -m "Initial HA config: MQTT integration, template entities, Alfred state publisher"
```

- [ ] **Step 9: Push to GitHub**

```bash
gh repo create alfred-home-assistant --private --source=. --push
```

---

### Task 9: Smoke Test Script

A shell script that validates the full live pipeline: publish a test MQTT event → verify Alfred processes it → check for action result on Redis.

**Files:**
- Create: `alfred/scripts/smoke-test.sh`

**Prerequisites:** Redis, Mosquitto, Bridge, Reflex Runner, home-service, and Ollama must be running.

- [ ] **Step 1: Create scripts/smoke-test.sh**

```bash
#!/usr/bin/env bash
# End-to-end smoke test for Alfred Phase 1.
#
# Publishes a fake state_changed event to MQTT, waits for the
# Reflex Engine to process it, and checks for an action result
# on the Redis action_results stream.
#
# Prerequisites: all services running (./scripts/dev-up.sh + Python processes)
#
# Usage: ./scripts/smoke-test.sh

set -euo pipefail

MQTT_HOST="${MQTT_HOST:-localhost}"
MQTT_PORT="${MQTT_PORT:-1883}"
REDIS_HOST="${REDIS_HOST:-localhost}"
REDIS_PORT="${REDIS_PORT:-6379}"
TIMEOUT=30

# Check prerequisites
command -v mosquitto_pub >/dev/null 2>&1 || { echo "ERROR: mosquitto_pub not found. Install: brew install mosquitto"; exit 1; }
command -v redis-cli >/dev/null 2>&1 || { echo "ERROR: redis-cli not found. Install: brew install redis"; exit 1; }

echo "=== Alfred Phase 1 Smoke Test ==="
echo ""

# Check services
echo "Checking services..."
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping >/dev/null 2>&1 || { echo "ERROR: Redis not reachable at $REDIS_HOST:$REDIS_PORT"; exit 1; }
echo "  ✓ Redis"

mosquitto_pub -h "$MQTT_HOST" -p "$MQTT_PORT" -t "test/ping" -m "ping" 2>/dev/null || { echo "ERROR: Mosquitto not reachable at $MQTT_HOST:$MQTT_PORT"; exit 1; }
echo "  ✓ Mosquitto"

# Verify Bridge is running by checking its consumer group exists on the stream
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" XINFO GROUPS alfred:home:state_changed 2>/dev/null | grep -q "reflex-engine" || { echo "ERROR: Bridge/Reflex Runner consumer group not found. Is the Reflex Runner running?"; exit 1; }
echo "  ✓ Reflex Runner (consumer group active)"

curl -sf http://localhost:8000/health >/dev/null 2>&1 || { echo "ERROR: home-service not reachable at localhost:8000"; exit 1; }
echo "  ✓ home-service"

curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 || { echo "ERROR: Ollama not reachable at localhost:11434"; exit 1; }
echo "  ✓ Ollama"

echo ""

# Get current length of action_results stream (to detect new entries)
BEFORE_LEN=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" XLEN alfred:home:action_results 2>/dev/null || echo "0")
echo "Action results stream length before: $BEFORE_LEN"

# Publish test event: TV turns on
echo ""
echo "Publishing test event: Living Room TV turned ON..."
EVENT_JSON='{"source":"home-service","domain":"home","entity_id":"media_player.living_room_tv","old_state":"off","new_state":"on","attributes":{"friendly_name":"Living Room TV"}}'
mosquitto_pub -h "$MQTT_HOST" -p "$MQTT_PORT" -t "home/state_changed" -m "$EVENT_JSON"

# Wait for action result
echo "Waiting for Reflex Engine to process (timeout: ${TIMEOUT}s)..."
for i in $(seq 1 "$TIMEOUT"); do
    AFTER_LEN=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" XLEN alfred:home:action_results 2>/dev/null || echo "0")
    if [ "$AFTER_LEN" -gt "$BEFORE_LEN" ]; then
        echo ""
        echo "=== Action result received! ==="
        # Read the latest entry
        redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" XREVRANGE alfred:home:action_results + - COUNT 1
        echo ""
        echo "=== Scratchpad queue ==="
        redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" LRANGE alfred:scratchpad:queue 0 5
        echo ""
        echo "✓ SMOKE TEST PASSED — Alfred processed the event and produced an action."
        exit 0
    fi
    sleep 1
done

echo ""
echo "✗ SMOKE TEST FAILED — No action result within ${TIMEOUT}s."
echo ""
echo "=== Diagnostics ==="
echo ""
echo "Events on state_changed stream:"
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" XRANGE alfred:home:state_changed - + COUNT 3 2>/dev/null || echo "  (stream empty or unreachable)"
echo ""
echo "Pending messages (unprocessed by reflex-engine):"
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" XPENDING alfred:home:state_changed reflex-engine 2>/dev/null || echo "  (no pending info)"
echo ""
echo "Check Reflex Runner terminal for errors (Ollama connectivity, parse failures, etc.)"
exit 1
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/smoke-test.sh
```

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke-test.sh
git commit -m "Add end-to-end smoke test script for Phase 1 pipeline"
```

---

### Task 10: End-to-End Validation

Manual walkthrough to verify the full pipeline works on the M4 Max MBP.

**Prerequisites:** All previous tasks completed.

- [ ] **Step 1: Install CLI tools for smoke test**

```bash
brew install mosquitto redis
```

(Only needed for `mosquitto_pub` and `redis-cli` CLI tools. The servers run in containers.)

- [ ] **Step 2: Start infrastructure**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
./scripts/dev-up.sh
```

Verify Redis and Mosquitto are running:
```bash
redis-cli ping          # Expected: PONG
mosquitto_pub -t test -m ok   # Expected: no error
```

- [ ] **Step 3: Start Home Assistant**

```bash
cd /Users/anirudhlath/code/private/alfred/home-assistant
./scripts/dev-up.sh
```

Visit http://localhost:8123, complete onboarding:
1. Create account
2. Skip integrations wizard
3. Go to Profile → Long-Lived Access Tokens → Create Token
4. Copy token to `alfred/.env` as `HA_TOKEN=<your-token>`

- [ ] **Step 4: Start Python services (3 terminals)**

Terminal 1 — Bridge:
```bash
cd /Users/anirudhlath/code/private/alfred/alfred
source .venv/bin/activate
python -m bus
```

Terminal 2 — Reflex Runner:
```bash
cd /Users/anirudhlath/code/private/alfred/alfred
source .venv/bin/activate
python -m core.reflex
```

Terminal 3 — home-service:
```bash
cd /Users/anirudhlath/code/private/alfred/home-service
source .venv/bin/activate
uvicorn app.server:app --port 8000
```

- [ ] **Step 5: Run smoke test**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
./scripts/smoke-test.sh
```

Expected: Test passes — action result appears on Redis stream.

- [ ] **Step 6: Test via Home Assistant UI**

1. Open http://localhost:8123
2. Toggle the "Living Room TV" input boolean → ON
3. Watch Terminal 2 (Reflex Runner) — should log an action
4. Check that lights dimmed (check input_number brightness changed):
   ```bash
   redis-cli XREVRANGE alfred:home:action_results + - COUNT 1
   ```

- [ ] **Step 7: Verify telemetry**

After a few events, check that telemetry data was flushed:
```bash
ls research/data/
cat research/data/latency/raw.csv
```

Expected: CSV files with latency and token usage data.
