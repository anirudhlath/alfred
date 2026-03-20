# Phase 3 Step 0-1: Prerequisites + Domain Routing + Observability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve backlog prerequisites, add generic domain routing, and wire up OpenTelemetry + Loguru — unblocking all subsequent Phase 3 plans.

**Architecture:** Replace all `logging.basicConfig()` with centralized Loguru, generalize `TraceRecord` for System 2 tracing, make `ContextReader` scan all services, introduce a `DomainRouter` that replaces hardcoded `HomeAgent`, and integrate OpenTelemetry SDK with a `@traced` decorator for distributed tracing.

**Tech Stack:** Python 3.13+, Loguru, OpenTelemetry SDK, Pydantic v2, Redis Streams, pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-03-19-alfred-expanded-vision-design.md` (Phase 3 Steps 0-1)

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `shared/logging.py` | Centralized Loguru setup (`configure_logging()`) |
| `core/routing/__init__.py` | Package init |
| `core/routing/domain_router.py` | `DomainAgent` protocol + `DomainRouter` class |
| `shared/otel.py` | OpenTelemetry SDK initialization (`init_tracing()`) |
| `shared/traced.py` | `@traced` decorator for OTel spans |
| `tests/shared/test_logging.py` | Loguru setup tests |
| `tests/shared/test_tracing.py` | Generalized TraceRecord tests |
| `tests/core/routing/__init__.py` | Package init |
| `tests/core/routing/test_domain_router.py` | DomainRouter tests |
| `tests/core/reflex/test_context_reader_multi.py` | Multi-service ContextReader tests |
| `tests/shared/test_traced.py` | `@traced` decorator tests |

### Modified Files

| File | Change |
|------|--------|
| `shared/streams.py` | Add all Phase 3 Redis stream/key constants |
| `shared/config.py` | Add Phase 3 config fields (Claude API, session timeout, etc.) |
| `shared/tracing.py` | Generalize `TraceRecord` — decouple from `StateChangedEvent` |
| `core/reflex/context_reader.py` | Multi-service `SCAN alfred:context:*` |
| `core/reflex/__main__.py` | Replace `logging.basicConfig()` with Loguru, replace `HomeAgent` with `DomainRouter` |
| `core/reflex/runner.py` | Accept `DomainAgent` protocol instead of `HomeAgent` type |
| `core/triggers/__main__.py` | Replace `logging.basicConfig()` with Loguru |
| `runner/__main__.py` | Replace `logging.basicConfig()` with Loguru |
| `bus/__main__.py` | Replace `logging.basicConfig()` with Loguru |
| `domains/home/home_agent.py` | Implement `DomainAgent` protocol |
| `pyproject.toml` | Add `loguru` dependency |

---

## Task 1: Add Loguru Dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add loguru to dependencies**

In `pyproject.toml`, add `"loguru>=0.7"` to the `dependencies` list. Also add `"types-loguru>=0.7"` to the `dev` optional-dependencies for mypy --strict compatibility.

**Note:** OpenTelemetry packages (`opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp`) are already in `pyproject.toml` — do NOT re-add them. Only `loguru` and `types-loguru` are new here.

```toml
# In [project] dependencies, add:
"loguru>=0.7",

# In [project.optional-dependencies] dev, add:
"types-loguru>=0.7",
```

- [ ] **Step 2: Install updated dependencies**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv pip install -e ".[dev]"`
Expected: Successfully installed loguru

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "deps: add loguru for structured logging"
```

---

## Task 2: Centralized Loguru Setup (`shared/logging.py`)

**Files:**
- Create: `shared/logging.py`
- Create: `tests/shared/test_logging.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/shared/test_logging.py
"""Tests for centralized Loguru logging setup."""

from __future__ import annotations

import sys
from unittest.mock import patch

from shared.logging import configure_logging


def test_configure_logging_returns_logger() -> None:
    """configure_logging() returns a loguru logger with bind context."""
    log = configure_logging(service="test-svc")
    assert hasattr(log, "info")
    assert hasattr(log, "bind")


def test_configure_logging_intercepts_stdlib(capsys: object) -> None:
    """stdlib logging calls are intercepted by loguru after configure_logging()."""
    import logging

    configure_logging(service="test-svc")
    # stdlib logger should now route through loguru's InterceptHandler
    stdlib_logger = logging.getLogger("test.stdlib")
    # Should not raise
    stdlib_logger.info("hello from stdlib")


def test_configure_logging_adds_service_context() -> None:
    """Logger returned by configure_logging has service name bound."""
    log = configure_logging(service="my-service")
    # The bound extra should contain service
    # We verify by checking the record produced
    records: list[dict[str, object]] = []

    def sink(message: object) -> None:
        records.append({"text": str(message)})

    from loguru import logger
    logger.add(sink, format="{extra[service]} | {message}", filter=lambda r: "service" in r["extra"])
    log.info("test message")

    assert len(records) >= 1
    assert "my-service" in str(records[-1]["text"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/shared/test_logging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.logging'`

- [ ] **Step 3: Write implementation**

```python
# shared/logging.py
"""Centralized Loguru logging setup.

Replaces all logging.basicConfig() calls across Alfred entry points.
Call configure_logging() once at service startup.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from loguru import Logger


class _InterceptHandler(logging.Handler):
    """Route stdlib logging through Loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        # Find caller from where the logged message originated
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame = logging.currentframe()
        depth = 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def configure_logging(
    service: str,
    *,
    level: str = "INFO",
    json_output: bool = False,
) -> Logger:
    """Configure Loguru as the sole logging backend.

    Args:
        service: Service name bound to all log records.
        level: Minimum log level (default INFO).
        json_output: If True, emit JSON-serialized logs (for production).

    Returns:
        A Loguru logger with service context bound.
    """
    # Remove default loguru handler
    logger.remove()

    # Console sink
    if json_output:
        logger.add(sys.stderr, serialize=True, level=level)
    else:
        fmt = (
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> "
            "[<cyan>{extra[service]}</cyan>] "
            "<level>{level: <8}</level> "
            "<level>{message}</level>"
        )
        logger.add(sys.stderr, format=fmt, level=level, colorize=True)

    # Intercept stdlib logging
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)

    # Return logger with service context bound
    return logger.bind(service=service)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/shared/test_logging.py -v`
Expected: PASS

- [ ] **Step 5: Run ruff + mypy**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && ruff check shared/logging.py && ruff format shared/logging.py && mypy shared/logging.py --strict`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add shared/logging.py tests/shared/test_logging.py
git commit -m "feat: centralized Loguru logging setup in shared/logging.py"
```

---

## Task 3: Replace `logging.basicConfig()` Across All Entry Points

**Files:**
- Modify: `runner/__main__.py:22-23`
- Modify: `core/reflex/__main__.py:126-129`
- Modify: `core/triggers/__main__.py` (find `logging.basicConfig`)
- Modify: `bus/__main__.py` (find `logging.basicConfig`)

- [ ] **Step 1: Replace in `runner/__main__.py`**

Replace:
```python
import logging
...
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("runner")
```

With:
```python
from shared.logging import configure_logging
...
log = configure_logging(service="runner")
```

Remove unused `import logging`.

- [ ] **Step 2: Replace in `core/reflex/__main__.py`**

Replace `logging.basicConfig(...)` and `logging.getLogger(__name__)` calls with:
```python
from shared.logging import configure_logging
```
In `main()`, replace basicConfig with `configure_logging(service="reflex")`. Keep `logger = logging.getLogger(__name__)` at module level since it routes through Loguru now via the intercept handler.

- [ ] **Step 3: Replace in `core/triggers/__main__.py`**

Same pattern — replace `logging.basicConfig()` with `configure_logging(service="triggers")`.

- [ ] **Step 4: Replace in `bus/__main__.py`**

Same pattern — replace `logging.basicConfig()` with `configure_logging(service="bridge")`.

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -x -q`
Expected: All existing tests pass

- [ ] **Step 6: Run ruff + mypy on changed files**

Run: `ruff check runner/ core/reflex/__main__.py core/triggers/__main__.py bus/__main__.py --fix && ruff format runner/ core/ bus/ && mypy runner/ core/ bus/ --strict`

- [ ] **Step 7: Commit**

```bash
git add runner/__main__.py core/reflex/__main__.py core/triggers/__main__.py bus/__main__.py
git commit -m "refactor: replace logging.basicConfig with centralized Loguru setup"
```

---

## Task 4: Add Phase 3 Redis Stream Constants

**Files:**
- Modify: `shared/streams.py`

- [ ] **Step 1: Add all new constants**

Add to `shared/streams.py`:

```python
# Phase 3: Conscious Engine
USER_REQUESTS_STREAM = "alfred:user:requests"
USER_RESPONSES_STREAM = "alfred:user:responses"
SESSIONS_KEY_PREFIX = "alfred:sessions:"
NOTIFICATIONS_STREAM = "alfred:notifications:queue"

# Phase 3: Memory
EPISODIC_STREAM = "alfred:memory:episodic"
VOICEPRINT_KEY = "alfred:identity:voiceprint"

# Phase 3: Runtime config + cost
RUNTIME_CONFIG_KEY = "alfred:config:runtime"
COST_DAILY_KEY = "alfred:cost:daily"

# Phase 3: Integration registry
INTEGRATION_REGISTRY_KEY = "alfred:integration_registry"
```

- [ ] **Step 2: Run mypy**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && mypy shared/streams.py --strict`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add shared/streams.py
git commit -m "feat: add Phase 3 Redis stream/key constants to shared/streams.py"
```

---

## Task 5: Extend `AlfredConfig` with Phase 3 Fields

**Files:**
- Modify: `shared/config.py`

- [ ] **Step 1: Add new config fields**

Add these fields to the `AlfredConfig` dataclass and `from_env()`:

```python
# Phase 3: Conscious Engine
claude_api_key: str = ""
claude_model: str = "claude-opus-4-6"
session_timeout_minutes: int = 30
proactivity_level: str = "opinionated"  # opinionated | moderate | conservative

# Phase 3: Cost
daily_cost_cap_usd: float = 5.0

# Phase 3: Memory
episodic_hot_days: int = 7
episodic_compress_days: int = 90

# Phase 3: Voice
voice_confidence_threshold: float = 0.85

# Phase 3: Signal
signal_phone_number: str = ""

# Phase 3: Logging
log_level: str = "INFO"
log_json: bool = False
```

And corresponding `os.getenv()` calls in `from_env()`.

- [ ] **Step 2: Run mypy**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && mypy shared/config.py --strict`
Expected: PASS

- [ ] **Step 3: Update `.env.example`**

Add the new env vars with commented descriptions.

- [ ] **Step 4: Commit**

```bash
git add shared/config.py .env.example
git commit -m "feat: extend AlfredConfig with Phase 3 configuration fields"
```

---

## Task 6: Generalize `TraceRecord` in `shared/tracing.py`

**Files:**
- Modify: `shared/tracing.py`
- Create: `tests/shared/test_tracing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/shared/test_tracing.py
"""Tests for generalized TraceRecord models."""

from __future__ import annotations

from datetime import UTC, datetime

from bus.schemas.events import ActionRequest, StateChangedEvent
from shared.tracing import ConsciousTraceRecord, ReflexTraceRecord, TraceRecordBase


def test_reflex_trace_record_has_event() -> None:
    event = StateChangedEvent(
        source="test", domain="home", entity_id="light.test", new_state="on"
    )
    record = ReflexTraceRecord(
        trace_id="t1",
        timestamp=datetime.now(UTC),
        model="llama3:8b",
        event=event,
        preferences_text="prefs",
        tools=[],
        prompt="prompt",
        raw_response="{}",
        parsed_action=None,
        latency_ms=100.0,
        prompt_tokens=50,
        completion_tokens=20,
    )
    assert record.system == "reflex"
    assert record.event.entity_id == "light.test"


def test_conscious_trace_record_has_request_id() -> None:
    record = ConsciousTraceRecord(
        trace_id="t2",
        timestamp=datetime.now(UTC),
        model="claude-opus-4-6",
        request_id="req-123",
        session_id="sess-456",
        channel="web_pwa",
        prompt="prompt",
        raw_response="response text",
        parsed_action=None,
        tool_calls=[],
        latency_ms=1200.0,
        prompt_tokens=500,
        completion_tokens=200,
    )
    assert record.system == "conscious"
    assert record.request_id == "req-123"


def test_base_fields_shared() -> None:
    """Both record types share TraceRecordBase fields."""
    record = ConsciousTraceRecord(
        trace_id="t3",
        timestamp=datetime.now(UTC),
        model="claude-opus-4-6",
        request_id="req",
        session_id="sess",
        channel="signal",
        prompt="p",
        raw_response="r",
        parsed_action=None,
        tool_calls=[],
        latency_ms=100.0,
        prompt_tokens=10,
        completion_tokens=5,
    )
    assert record.trace_id == "t3"
    assert record.latency_ms == 100.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/shared/test_tracing.py -v`
Expected: FAIL — `ImportError: cannot import name 'ConsciousTraceRecord'`

- [ ] **Step 3: Rewrite shared/tracing.py with generalized hierarchy**

```python
# shared/tracing.py
"""TraceRecord — structured inference traces for evals, debugging, and observability.

Split into ReflexTraceRecord (System 1) and ConsciousTraceRecord (System 2)
sharing a common TraceRecordBase.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any, Literal

from pydantic import BaseModel

from bus.schemas.events import ActionRequest, StateChangedEvent  # noqa: TC001


class TraceRecordBase(BaseModel):
    """Common fields shared by all inference trace records."""

    trace_id: str
    timestamp: datetime
    model: str
    system: str  # "reflex" or "conscious"

    # Prompt
    prompt: str

    # Output
    raw_response: str
    parsed_action: ActionRequest | None

    # Metrics
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int


class ReflexTraceRecord(TraceRecordBase):
    """Trace of a single System 1 SLM inference call."""

    system: Literal["reflex"] = "reflex"

    # Reflex-specific inputs
    event: StateChangedEvent
    preferences_text: str
    tools: list[dict[str, Any]]


class ConsciousTraceRecord(TraceRecordBase):
    """Trace of a single System 2 Claude inference call."""

    system: Literal["conscious"] = "conscious"

    # Conscious-specific inputs
    request_id: str
    session_id: str
    channel: str

    # Agentic loop
    tool_calls: list[dict[str, Any]]


# Backward compat alias — existing code imports TraceRecord
TraceRecord = ReflexTraceRecord
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/shared/test_tracing.py -v`
Expected: PASS

- [ ] **Step 5: Verify existing TraceRecord usages still work**

The `TraceRecord = ReflexTraceRecord` alias preserves backward compatibility. Run:

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -x -q`
Expected: All existing tests pass

- [ ] **Step 6: Run ruff + mypy**

Run: `ruff check shared/tracing.py tests/shared/test_tracing.py --fix && ruff format shared/ tests/shared/ && mypy shared/tracing.py --strict`

- [ ] **Step 7: Commit**

```bash
git add shared/tracing.py tests/shared/test_tracing.py
git commit -m "refactor: generalize TraceRecord into ReflexTraceRecord + ConsciousTraceRecord"
```

---

## Task 7: Multi-Service `ContextReader`

**Files:**
- Modify: `core/reflex/context_reader.py`
- Create: `tests/core/reflex/test_context_reader_multi.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/reflex/test_context_reader_multi.py
"""Tests for multi-service ContextReader."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.reflex.context_reader import ContextReader
from sdk.alfred_sdk.context import ContextEntry, ContextSnapshot
from shared.streams import CONTEXT_KEY_PREFIX


@pytest.fixture
def mock_redis() -> AsyncMock:
    return AsyncMock()


@pytest.mark.asyncio
async def test_multi_service_scan(mock_redis: AsyncMock) -> None:
    """ContextReader scans all alfred:context:* keys, not just home-service."""
    snap_home = ContextSnapshot(
        controllable={"light": [ContextEntry(entity_id="light.living", state="on")]},
    )
    snap_weather = ContextSnapshot(
        sensors={"weather": [ContextEntry(entity_id="weather.home", state="sunny")]},
    )

    # Mock SCAN to return two keys
    async def mock_scan_iter(match: str, count: int = 100) -> Any:
        for key in [f"{CONTEXT_KEY_PREFIX}home-service", f"{CONTEXT_KEY_PREFIX}weather-service"]:
            yield key.encode()

    mock_redis.scan_iter = mock_scan_iter

    async def mock_get(key: str | bytes) -> bytes | None:
        k = key.decode() if isinstance(key, bytes) else key
        if k.endswith("home-service"):
            return snap_home.model_dump_json().encode()
        if k.endswith("weather-service"):
            return snap_weather.model_dump_json().encode()
        return None

    mock_redis.get = AsyncMock(side_effect=mock_get)

    reader = ContextReader(redis=mock_redis)
    rendered = await reader.get_rendered_context()

    assert "light.living" in rendered
    assert "weather.home" in rendered


@pytest.mark.asyncio
async def test_empty_scan_returns_empty(mock_redis: AsyncMock) -> None:
    """ContextReader returns empty string when no context keys exist."""
    async def mock_scan_iter(match: str, count: int = 100) -> Any:
        return
        yield  # make it an async generator

    mock_redis.scan_iter = mock_scan_iter

    reader = ContextReader(redis=mock_redis)
    rendered = await reader.get_rendered_context()
    assert rendered == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/reflex/test_context_reader_multi.py -v`
Expected: FAIL — ContextReader constructor still takes `service_name`

- [ ] **Step 3: Rewrite ContextReader for multi-service scan**

```python
# core/reflex/context_reader.py
"""Context reader — fetches and renders service context from Redis."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from sdk.alfred_sdk.context import ContextSnapshot
from shared.streams import CONTEXT_KEY_PREFIX

if TYPE_CHECKING:
    from core.reflex.runner import AioRedis

logger = logging.getLogger(__name__)


def render_snapshot(snapshot: ContextSnapshot) -> str:
    """Render a ContextSnapshot into Markdown for the LLM prompt."""
    lines: list[str] = []

    for domain, entries in sorted(snapshot.controllable.items()):
        title = domain.replace("_", " ").title() + "s"
        lines.append(f"### {title}")
        for e in entries:
            attrs = ""
            if e.attributes:
                attr_parts = [f"{k}: {v}" for k, v in e.attributes.items()]
                attrs = f" ({', '.join(attr_parts)})"
            lines.append(f"- {e.entity_id}: {e.state}{attrs}")
        lines.append("")

    for domain, entries in sorted(snapshot.sensors.items()):
        title = domain.replace("_", " ").title() + "s"
        lines.append(f"### {title}")
        for e in entries:
            lines.append(f"- {e.entity_id}: {e.state}")
        lines.append("")

    return "\n".join(lines).rstrip()


class ContextReader:
    """Reads and caches service context from Redis.

    Scans all alfred:context:* keys to aggregate context from all
    registered services (not just home-service).
    """

    CACHE_TTL = 300.0  # 5 minutes

    def __init__(self, redis: AioRedis) -> None:
        self._redis = redis
        self._cached_rendered: str = ""
        self._cache_time: float = 0.0
        self._cache_valid: bool = False

    async def get_rendered_context(self) -> str:
        """Return rendered Markdown context from all services, re-fetching after TTL."""
        now = time.monotonic()
        if not self._cache_valid or (now - self._cache_time) > self.CACHE_TTL:
            merged = ContextSnapshot()

            async for key in self._redis.scan_iter(
                match=f"{CONTEXT_KEY_PREFIX}*", count=100
            ):
                raw: bytes | None = await self._redis.get(key)  # type: ignore[misc]
                if raw is None:
                    continue
                try:
                    snap = ContextSnapshot.model_validate_json(raw)
                except Exception as exc:
                    k = key.decode() if isinstance(key, bytes) else key
                    logger.warning("Failed to parse context from %s: %s", k, exc)
                    continue

                for domain, entries in snap.controllable.items():
                    merged.controllable.setdefault(domain, []).extend(entries)
                for domain, entries in snap.sensors.items():
                    merged.sensors.setdefault(domain, []).extend(entries)

            self._cached_rendered = render_snapshot(merged)
            self._cache_time = now
            self._cache_valid = True

        return self._cached_rendered
```

- [ ] **Step 4: Update `core/reflex/__main__.py`**

Remove the `service_name` kwarg from `ContextReader()` construction (line 77):

```python
# Before:
context_reader = ContextReader(redis=r, service_name="home-service")

# After (service_name removed):
context_reader = ContextReader(redis=r)
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/reflex/test_context_reader_multi.py -v && python -m pytest -x -q`
Expected: All PASS

- [ ] **Step 6: Run ruff + mypy**

Run: `ruff check core/reflex/context_reader.py --fix && ruff format core/reflex/ && mypy core/reflex/context_reader.py --strict`

- [ ] **Step 7: Commit**

```bash
git add core/reflex/context_reader.py tests/core/reflex/test_context_reader_multi.py core/reflex/__main__.py
git commit -m "feat: ContextReader scans all alfred:context:* keys (multi-service)"
```

---

## Task 8: `DomainAgent` Protocol + `DomainRouter`

**Files:**
- Create: `core/routing/__init__.py`
- Create: `core/routing/domain_router.py`
- Create: `tests/core/routing/__init__.py`
- Create: `tests/core/routing/test_domain_router.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/routing/test_domain_router.py
"""Tests for DomainRouter."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bus.schemas.events import ActionRequest, ActionResult
from core.routing.domain_router import DomainAgent, DomainRouter


class FakeAgent:
    """Fake domain agent for testing."""

    async def execute_action(self, action: ActionRequest) -> ActionResult:
        return ActionResult(
            source="fake-agent",
            request_id=action.request_id,
            tool_name=action.tool_name,
            status="success",
            result={"ok": True},
        )


@pytest.fixture
def router() -> DomainRouter:
    r = DomainRouter()
    r.register("home-service", FakeAgent())
    return r


@pytest.mark.asyncio
async def test_route_to_registered_agent(router: DomainRouter) -> None:
    action = ActionRequest(
        source="test",
        target_service="home-service",
        tool_name="smart_home.dim_lights",
        parameters={"room": "living", "level": 50},
    )
    result = await router.route(action)
    assert result.status == "success"
    assert result.tool_name == "smart_home.dim_lights"


@pytest.mark.asyncio
async def test_route_unknown_service_returns_error(router: DomainRouter) -> None:
    action = ActionRequest(
        source="test",
        target_service="unknown-service",
        tool_name="some.tool",
    )
    result = await router.route(action)
    assert result.status == "error"
    assert "unknown-service" in (result.error or "")


def test_register_multiple_agents() -> None:
    router = DomainRouter()
    router.register("svc-a", FakeAgent())
    router.register("svc-b", FakeAgent())
    assert router.registered_services == {"svc-a", "svc-b"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/routing/test_domain_router.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create `core/routing/__init__.py`**

```python
# core/routing/__init__.py
```

Also create `tests/core/routing/__init__.py` (empty).

- [ ] **Step 4: Implement `DomainRouter`**

```python
# core/routing/domain_router.py
"""Generic domain routing — dispatches ActionRequests to the correct domain agent."""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from bus.schemas.events import ActionRequest, ActionResult

logger = logging.getLogger(__name__)


@runtime_checkable
class DomainAgent(Protocol):
    """Protocol for domain agents that execute actions."""

    async def execute_action(self, action: ActionRequest) -> ActionResult: ...


class DomainRouter:
    """Routes ActionRequests to the appropriate domain agent by target_service.

    Agents register at startup. The router reads action.target_service
    and dispatches. Unknown services return an error result.
    Adding a new domain = adding a new agent + registering it.
    """

    def __init__(self) -> None:
        self._agents: dict[str, DomainAgent] = {}

    def register(self, service_pattern: str, agent: DomainAgent) -> None:
        """Register a domain agent for a service pattern.

        Args:
            service_pattern: The target_service string from ToolInfo (e.g. "home-service").
            agent: The domain agent instance.
        """
        self._agents[service_pattern] = agent
        logger.info("Registered domain agent for '%s'", service_pattern)

    @property
    def registered_services(self) -> set[str]:
        """Return set of registered service patterns."""
        return set(self._agents)

    async def route(self, action: ActionRequest) -> ActionResult:
        """Route an ActionRequest to the appropriate domain agent."""
        agent = self._agents.get(action.target_service)
        if agent is None:
            logger.warning(
                "No domain agent registered for service '%s'", action.target_service
            )
            return ActionResult(
                source="domain-router",
                request_id=action.request_id,
                tool_name=action.tool_name,
                status="error",
                error=f"No domain agent registered for service '{action.target_service}'",
            )
        return await agent.execute_action(action)
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/routing/test_domain_router.py -v`
Expected: All PASS

- [ ] **Step 6: Run ruff + mypy**

Run: `ruff check core/routing/ tests/core/routing/ --fix && ruff format core/routing/ tests/core/routing/ && mypy core/routing/ --strict`

- [ ] **Step 7: Commit**

```bash
git add core/routing/ tests/core/routing/
git commit -m "feat: DomainAgent protocol + DomainRouter for generic action dispatch"
```

---

## Task 9: Wire DomainRouter into Reflex Runner

**Files:**
- Modify: `core/reflex/runner.py:45-49` — Accept `DomainAgent` instead of `HomeAgent`
- Modify: `core/reflex/__main__.py:19,83` — Construct DomainRouter, register HomeAgent

- [ ] **Step 1: Update `core/reflex/runner.py` to accept `DomainAgent`**

Change the `process_stream_entry` signature: replace `agent: HomeAgent` with `agent: DomainAgent` (imported from `core.routing.domain_router`). Update the TYPE_CHECKING import accordingly.

```python
# In runner.py, change:
if TYPE_CHECKING:
    from core.routing.domain_router import DomainAgent
    ...

async def process_stream_entry(
    entry_data: Mapping[str | bytes, str | bytes],
    engine: ReflexEngine,
    agent: DomainAgent,  # was HomeAgent
    ...
```

- [ ] **Step 2: Update `core/reflex/__main__.py` to use DomainRouter**

Replace:
```python
from domains.home.home_agent import HomeAgent
...
agent = HomeAgent(redis=r)
```

With:
```python
from core.routing.domain_router import DomainRouter
from domains.home.home_agent import HomeAgent
...
router = DomainRouter()
router.register("home-service", HomeAgent(redis=r))
```

And pass `router` to `process_stream_entry()` instead of `agent` — the `DomainRouter` satisfies `DomainAgent` protocol since it has `route()` not `execute_action()`. Wait — we need `DomainRouter` to also satisfy the protocol. Actually, `process_stream_entry` calls `agent.execute_action(action)`. So we should make `DomainRouter` route via `execute_action`:

**Option:** Add an `execute_action` alias on `DomainRouter` that delegates to `route`:

```python
# In DomainRouter, add:
async def execute_action(self, action: ActionRequest) -> ActionResult:
    """Alias for route() — satisfies DomainAgent protocol."""
    return await self.route(action)
```

This way `DomainRouter` itself satisfies `DomainAgent` protocol.

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -x -q`
Expected: All PASS

- [ ] **Step 4: Run ruff + mypy**

Run: `ruff check core/reflex/ core/routing/ --fix && ruff format core/ && mypy core/reflex/ core/routing/ --strict`

- [ ] **Step 5: Commit**

```bash
git add core/reflex/runner.py core/reflex/__main__.py core/routing/domain_router.py
git commit -m "refactor: wire DomainRouter into Reflex Runner, replacing hardcoded HomeAgent"
```

---

## Task 10: OpenTelemetry SDK Initialization (`shared/otel.py`)

**Files:**
- Create: `shared/otel.py`
- Create: `tests/shared/test_otel.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/shared/test_otel.py
"""Tests for OpenTelemetry SDK initialization."""

from __future__ import annotations

from opentelemetry import trace

from shared.otel import init_tracing


def test_init_tracing_creates_tracer() -> None:
    """init_tracing returns a working tracer."""
    tracer = init_tracing(service_name="test-service", endpoint=None)
    assert tracer is not None
    span = tracer.start_span("test-span")
    span.end()


def test_init_tracing_without_endpoint_uses_noop() -> None:
    """When endpoint is None, tracer still works (noop exporter)."""
    tracer = init_tracing(service_name="test-noop", endpoint=None)
    with tracer.start_as_current_span("noop-test") as span:
        assert span is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/shared/test_otel.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.otel'`

- [ ] **Step 3: Implement**

```python
# shared/otel.py
"""OpenTelemetry SDK initialization for Alfred services."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter


def init_tracing(
    service_name: str,
    endpoint: str | None = "http://localhost:4317",
) -> trace.Tracer:
    """Initialize OpenTelemetry tracing and return a Tracer.

    Args:
        service_name: Name of the service (appears in SigNoz).
        endpoint: OTLP gRPC endpoint. None = console exporter only (dev/test).

    Returns:
        An OpenTelemetry Tracer instance.
    """
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
            provider.add_span_processor(BatchSpanProcessor(exporter))
        except Exception:
            # Fallback to console if OTLP endpoint unreachable
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    else:
        # Dev/test mode — no export
        pass

    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/shared/test_otel.py -v`
Expected: PASS

- [ ] **Step 5: Run ruff + mypy**

Run: `ruff check shared/otel.py --fix && ruff format shared/otel.py && mypy shared/otel.py --strict`

- [ ] **Step 6: Commit**

```bash
git add shared/otel.py tests/shared/test_otel.py
git commit -m "feat: OpenTelemetry SDK initialization in shared/otel.py"
```

---

## Task 11: `@traced` Decorator (`shared/traced.py`)

**Files:**
- Create: `shared/traced.py`
- Create: `tests/shared/test_traced.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/shared/test_traced.py
"""Tests for @traced decorator."""

from __future__ import annotations

import pytest

from shared.traced import traced


@traced(name="test.sync_fn")
def sync_function(x: int) -> int:
    return x * 2


@traced(name="test.async_fn")
async def async_function(x: int) -> int:
    return x * 3


def test_traced_sync() -> None:
    result = sync_function(5)
    assert result == 10


@pytest.mark.asyncio
async def test_traced_async() -> None:
    result = await async_function(5)
    assert result == 15


@traced()
def auto_named_fn() -> str:
    return "hello"


def test_traced_auto_names_from_function() -> None:
    result = auto_named_fn()
    assert result == "hello"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/shared/test_traced.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# shared/traced.py
"""@traced decorator — creates OpenTelemetry spans for function calls."""

from __future__ import annotations

import asyncio
import functools
from typing import Any, overload

from opentelemetry import trace


@overload
def traced(fn: Any) -> Any: ...


@overload
def traced(
    *,
    name: str | None = None,
) -> Any: ...


def traced(
    fn: Any | None = None,
    *,
    name: str | None = None,
) -> Any:
    """Decorator that wraps a function in an OpenTelemetry span.

    Supports both sync and async functions.
    Supports both @traced and @traced(name="custom.name").
    """

    def decorator(f: Any) -> Any:
        span_name = name or f"{f.__module__}.{f.__qualname__}"
        tracer = trace.get_tracer(f.__module__)

        if asyncio.iscoroutinefunction(f):

            @functools.wraps(f)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with tracer.start_as_current_span(span_name) as span:
                    try:
                        result = await f(*args, **kwargs)
                        return result
                    except Exception as exc:
                        span.set_status(
                            trace.StatusCode.ERROR, str(exc)
                        )
                        span.record_exception(exc)
                        raise

            return async_wrapper
        else:

            @functools.wraps(f)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                with tracer.start_as_current_span(span_name) as span:
                    try:
                        result = f(*args, **kwargs)
                        return result
                    except Exception as exc:
                        span.set_status(
                            trace.StatusCode.ERROR, str(exc)
                        )
                        span.record_exception(exc)
                        raise

            return sync_wrapper

    if fn is not None:
        return decorator(fn)
    return decorator
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/shared/test_traced.py -v`
Expected: PASS

- [ ] **Step 5: Run ruff + mypy**

Run: `ruff check shared/traced.py --fix && ruff format shared/traced.py && mypy shared/traced.py --strict`

- [ ] **Step 6: Commit**

```bash
git add shared/traced.py tests/shared/test_traced.py
git commit -m "feat: @traced decorator for OpenTelemetry spans"
```

---

## Task 12: Wire OTel into Entry Points

**Files:**
- Modify: `core/reflex/__main__.py`
- Modify: `core/triggers/__main__.py`
- Modify: `runner/__main__.py`

- [ ] **Step 1: Add `init_tracing()` calls to entry points**

In each `main()` function, after `configure_logging()`, add:

```python
from shared.otel import init_tracing
from shared.config import AlfredConfig

config = AlfredConfig.from_env()
tracer = init_tracing(
    service_name="reflex",  # or "triggers", "runner"
    endpoint=config.otel_endpoint if config.signoz_enabled else None,
)
```

- [ ] **Step 2: Add `@traced` to `ReflexEngine.process_event`**

In `core/reflex/engine.py`, add `@traced(name="reflex.process_event")` above `@track_latency(category="reflex")` on `process_event()`. Import from `shared.traced`.

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -x -q`
Expected: All PASS

- [ ] **Step 4: Run ruff + mypy on all changed files**

Run: `ruff check core/ runner/ shared/ --fix && ruff format core/ runner/ shared/ && mypy core/ runner/ shared/ --strict`

- [ ] **Step 5: Commit**

```bash
git add core/reflex/__main__.py core/reflex/engine.py core/triggers/__main__.py runner/__main__.py
git commit -m "feat: wire OpenTelemetry tracing into all entry points"
```

---

## Task 13: Update Documentation

**Files:**
- Modify: `docs/backlog/trigger-engine-simplification.md` — Mark item 6 as DONE
- Modify: `docs/backlog/context-provider.md` — Mark multi-service scan as DONE

- [ ] **Step 1: Mark backlog items complete**

In `docs/backlog/trigger-engine-simplification.md`, update item 6:
```markdown
### 6. ~~Extract shared logging setup~~ DONE
**Completed:** 2026-03-19 (phase3-prerequisites branch)
Centralized Loguru setup in `shared/logging.py`. All entry points updated.
```

In `docs/backlog/context-provider.md`, update the first section:
```markdown
## ~~Agent-Scoped Context Visibility~~ DONE
**Completed:** 2026-03-19 (phase3-prerequisites branch)
`ContextReader` now scans all `alfred:context:*` keys via Redis SCAN.
```

- [ ] **Step 2: Commit**

```bash
git add docs/backlog/
git commit -m "docs: mark completed backlog items (logging setup, multi-service context)"
```

---

## Task 14: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -v`

- [ ] **Step 2: Run full linting + type checking**

Run: `ruff check . --fix && ruff format . && mypy bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`

- [ ] **Step 3: Verify no regressions**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && bash scripts/smoke-test.sh` (if infrastructure is running)
