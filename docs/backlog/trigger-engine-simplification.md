# Trigger Engine — Simplification Backlog

**Source:** Code review (2026-03-10) — items deferred from /simplify pass.

## Deferred Items

### 1. ~~In-memory trigger cache~~ DONE
**Completed:** 2026-03-17 (trigger-engine-hardening branch)
Write-through `_cache` dict in `TriggerStore`. `list_all()`/`get()` read from cache. `refresh()` periodic safety net.

### 2. ~~Composite child trigger caching~~ DONE
**Completed:** 2026-03-17 (trigger-engine-hardening branch)
`model_post_init` pre-builds children with indexed IDs (`child:0`, `child:1`, ...) via `PrivateAttr`.

### 3. ~~Replace hand-rolled HTTP server with framework~~ DONE
**Completed:** 2026-03-17 (trigger-engine-hardening branch)
FastAPI with REST endpoints + JSON-RPC backward-compat shim. uvicorn.Server for graceful shutdown.

**Also completed:** TimeTrigger cron validation at construction (not in original backlog). `model_post_init` creates sentinel croniter to fail fast on bad cron expressions.

### 4. Sensor triggers evaluated on tick for no reason
**Priority:** Low
**Files:** `core/triggers/engine.py`, `core/triggers/models.py`

`evaluate_tick()` evaluates all triggers including `SensorTrigger`s, which always return `False` when `context.event is None`. Add a `responds_to_tick: bool` class-level attribute on trigger types so the engine can skip irrelevant evaluations.

### 5. Extract shared stream entry parsing
**Priority:** Low
**Files:** `core/triggers/__main__.py`, `core/reflex/runner.py`

The bytes/str decode pattern for Redis stream entries is duplicated between the trigger engine and reflex runner event loops. Extract a `parse_stream_event(entry_data) -> StateChangedEvent | None` utility into `shared/` or `bus/`.

### 6. ~~Extract shared logging setup~~ DONE
**Completed:** 2026-03-19 (phase3-prerequisites branch)
Centralized Loguru setup in `shared/logging.py`. All entry points updated.

### 7. `TriggerFeature.__init__` type safety
**Priority:** Low
**Files:** `core/triggers/feature.py`

`self._store = None  # type: ignore[assignment]` creates a latent `AttributeError` if called without context. Options: make `ctx` required, separate registry-side subclass, or declare `_store: TriggerStore | None` with guard helper.

### 8. `load()` fallback not available at runtime
**Priority:** Low
**Files:** `core/triggers/store.py`

`list_all()` only reads Redis — if Redis drops between startup and a tick, triggers silently disappear. With the in-memory cache (item 1), this becomes moot.
