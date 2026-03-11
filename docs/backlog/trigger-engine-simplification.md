# Trigger Engine — Simplification Backlog

**Source:** Code review (2026-03-10) — items deferred from /simplify pass.

## Deferred Items

### 1. In-memory trigger cache
**Priority:** High
**Files:** `core/triggers/store.py`, `core/triggers/engine.py`

`evaluate_tick()` and `evaluate_event()` call `HGETALL` + full deserialization on every tick (1/s) and every event. Add an in-process `dict[str, BaseTrigger]` cache populated at startup, updated on `save()`/`delete()`. Safe because the engine is a single process.

### 2. Composite child trigger caching
**Priority:** Medium
**Files:** `core/triggers/types/composite.py`

`CompositeTrigger.evaluate()` reconstructs child `BaseTrigger` instances on every call. Cache parsed children in a Pydantic `model_validator` or `@cached_property`. Also: child IDs should include list index (`f"{self.trigger_id}:child:{i}"`) instead of all sharing the same ID.

### 3. Replace hand-rolled HTTP server with framework
**Priority:** Medium
**Files:** `core/triggers/server.py`

`server.py` is a raw `asyncio.start_server` HTTP handler (70 lines). No TLS, no keep-alive, no content-type validation. Inconsistent with home-service which uses uvicorn/FastAPI. Replace with minimal FastAPI or aiohttp endpoint for production readiness.

### 4. Sensor triggers evaluated on tick for no reason
**Priority:** Low
**Files:** `core/triggers/engine.py`, `core/triggers/models.py`

`evaluate_tick()` evaluates all triggers including `SensorTrigger`s, which always return `False` when `context.event is None`. Add a `responds_to_tick: bool` class-level attribute on trigger types so the engine can skip irrelevant evaluations.

### 5. Extract shared stream entry parsing
**Priority:** Low
**Files:** `core/triggers/__main__.py`, `core/reflex/runner.py`

The bytes/str decode pattern for Redis stream entries is duplicated between the trigger engine and reflex runner event loops. Extract a `parse_stream_event(entry_data) -> StateChangedEvent | None` utility into `shared/` or `bus/`.

### 6. Extract shared logging setup
**Priority:** Low
**Files:** `core/triggers/__main__.py`, `core/reflex/__main__.py`

`logging.basicConfig()` with identical format string is copy-pasted between entry points. Extract to `shared/logging.py` as `configure_logging()`.

### 7. `TriggerFeature.__init__` type safety
**Priority:** Low
**Files:** `core/triggers/feature.py`

`self._store = None  # type: ignore[assignment]` creates a latent `AttributeError` if called without context. Options: make `ctx` required, separate registry-side subclass, or declare `_store: TriggerStore | None` with guard helper.

### 8. `load()` fallback not available at runtime
**Priority:** Low
**Files:** `core/triggers/store.py`

`list_all()` only reads Redis — if Redis drops between startup and a tick, triggers silently disappear. With the in-memory cache (item 1), this becomes moot.
