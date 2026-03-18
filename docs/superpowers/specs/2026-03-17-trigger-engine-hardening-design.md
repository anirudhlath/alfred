# Trigger Engine Hardening — Design Specification

**Date:** 2026-03-17
**Status:** Approved
**Author:** Anirudh Lath + Claude (Lead Engineer / Background Scientist)

---

## 1. Problem Statement

The Trigger Engine works correctly but has performance waste in its hot path. Every second (tick loop) and on every incoming event (event loop), the engine:

1. Fetches ALL triggers from Redis via `HGETALL` + full JSON deserialization + Pydantic validation
2. Re-parses cron strings for every `TimeTrigger.evaluate()` call
3. Reconstructs child `BaseTrigger` instances for every `CompositeTrigger.evaluate()` call

The trigger set changes rarely (only on LLM-initiated CRUD), but the engine pays full deserialization cost on every tick. With 100 triggers, this is ~6,000 deserializations/minute for data that hasn't changed.

Additionally, the HTTP server (`server.py`) is a hand-rolled 77-line `asyncio.start_server` handler that's inconsistent with the rest of the stack (FastAPI/uvicorn in home-service) and lacks request validation, TLS, keep-alive, and middleware hooks.

## 2. Goals

1. Eliminate per-tick Redis round-trips — reads should hit an in-memory cache
2. Eliminate per-evaluate object reconstruction in CompositeTrigger and cron re-parsing in TimeTrigger
3. Replace the raw asyncio HTTP server with FastAPI/uvicorn for consistency and production readiness
4. Maintain correctness — CRUD operations must still persist to Redis + YAML snapshots
5. No changes to the public API — callers of `TriggerStore`, `TriggerEngine`, and tool endpoints see identical behavior

## 3. Non-Goals

- Tick loop drift fix (minor, separate concern)
- Optimistic locking for concurrent CRUD (single process, not a real risk)
- OpenTelemetry metrics instrumentation (separate hardening pass)
- Sensor trigger type coercion bug (separate fix)
- Sensor triggers evaluated on tick for no reason — backlog item #4, separate optimization (add `responds_to_tick` class attribute)
- Multi-instance cache coordination (engine is explicitly single-process)

---

## 4. Design

### 4.1 In-Memory Trigger Cache

**File:** `core/triggers/store.py`

`TriggerStore` gains a `_cache: dict[str, BaseTrigger]` field, populated at startup and kept in sync via write-through on every mutation.

#### API Changes (internal only)

| Method | Current Behavior | New Behavior |
|--------|-----------------|--------------|
| `load()` | HGETALL + disk fallback, returns list | Same, but also populates `_cache` |
| `list_all(enabled_only)` | HGETALL + deserialize + filter | Returns from `_cache`, filters in-memory. Remains `async def` (body is synchronous) to avoid breaking callers. |
| `get(trigger_id)` | HGET from Redis + deserialize | Returns from `_cache`, zero Redis calls. Remains `async def` for same reason. |
| `save(trigger)` | HSET + YAML snapshot | Same, plus `_cache[trigger_id] = trigger` |
| `delete(trigger_id)` | HDEL + YAML delete | Same, plus `del _cache[trigger_id]` |
| `refresh()` | N/A (new) | HGETALL, replaces `_cache` entirely |

#### Cache Lifecycle

```
Startup:
    store.load()
        → HGETALL (or disk fallback)
        → _cache = {t.trigger_id: t for t in triggers}

Hot path (every tick / every event):
    store.list_all(enabled_only=True)
        → [t for t in _cache.values() if t.enabled]
        → zero Redis calls

CRUD (rare, tool-call initiated):
    store.save(trigger)
        → HSET to Redis
        → YAML snapshot to disk
        → _cache[trigger.trigger_id] = trigger

    store.delete(trigger_id)
        → HDEL from Redis
        → YAML delete from disk
        → del _cache[trigger_id]

Safety net (every 60s, configurable):
    store.refresh()
        → HGETALL
        → _cache = {parsed triggers}
```

#### Why Write-Through Is Sufficient

The trigger engine is a single-process system. All mutations flow through `TriggerStore.save()` and `TriggerStore.delete()` — there's no external writer. The 60-second refresh is a safety net for manual Redis edits during debugging, not a correctness requirement.

#### Concurrency Safety

`list_all()` and `get()` are synchronous reads from the in-memory dict (no `await` between cache access and return). Since the event loop is single-threaded, `refresh()` cannot interleave with a read. Dict reference assignment in `refresh()` is atomic at the CPython bytecode level.

#### Snapshot Behavior After Caching

`snapshot_all()` calls `list_all()`, which now reads the cache instead of Redis. This is intentional — the cache is the authoritative in-process state. The periodic YAML snapshot captures the cache, and `refresh()` re-syncs the cache from Redis every 60s. If Redis is manually edited, the next `refresh()` picks it up, and the next `snapshot_all()` persists it.

#### `refresh()` Cost

`refresh()` performs a full HGETALL + deserialization — the same cost as the old `list_all()`. It must never be called from a hot path. It runs only in `refresh_loop` (every 60s), not in `evaluate_tick()` or `evaluate_event()`.

### 4.2 Model-Level Caching

#### CompositeTrigger (`core/triggers/types/composite.py`)

Add `model_post_init` that parses `conditions.children` into concrete `BaseTrigger` instances once at construction time. Store in `_cached_children: list[BaseTrigger]`.

```
Construction (once):
    for each child_spec in conditions.children:
        child_cls = TriggerRegistry.get(child_spec["trigger_type"])
        child = child_cls(trigger_id=f"{self.trigger_id}:child:{i}", ...)
        _cached_children.append(child)

evaluate() (every tick):
    for child in _cached_children:  ← pre-built, no allocation
        if child.evaluate(context):
            matched += 1
```

Triggers are immutable between CRUD operations — `save()` creates a new model via `model_copy()`, which re-runs `model_post_init`. No invalidation logic needed.

#### TimeTrigger (`core/triggers/types/time.py`)

Add `model_post_init` that pre-parses the cron string into a stored `croniter` instance (`_cached_cron`). `evaluate()` reuses it by re-seeding with the current time instead of re-parsing the cron expression.

Same immutability argument — the cron string doesn't change between CRUD operations, and `model_copy()` triggers re-initialization.

#### Performance Impact

| Trigger Type | Current (per tick) | After (per tick) |
|-------------|-------------------|-----------------|
| TimeTrigger | Parse cron string + compute next fire | Re-seed cached croniter |
| CompositeTrigger (5 children) | 5× registry lookup + 5× Pydantic validation + 5× object allocation | Iterate pre-built list |
| Any trigger via list_all | HGETALL + N× JSON parse + N× Pydantic validate | Dict values iteration |

### 4.3 FastAPI Server Replacement

**File:** `core/triggers/server.py` (full rewrite)

Replace the raw `asyncio.start_server` HTTP handler with a FastAPI application. The current server dispatches JSON-RPC through `AlfredClient.dispatch()`. The new server exposes REST endpoints that call `TriggerFeature` tool methods directly.

#### Endpoints

| Method | Path | Handler | Maps To |
|--------|------|---------|---------|
| `POST` | `/triggers` | `create_trigger` | `TriggerFeature.create_trigger()` |
| `GET` | `/triggers` | `list_triggers` | `TriggerFeature.list_triggers()` |
| `PATCH` | `/triggers/{id}` | `update_trigger` | `TriggerFeature.update_trigger()` |
| `DELETE` | `/triggers/{id}` | `delete_trigger` | `TriggerFeature.delete_trigger()` |
| `PATCH` | `/triggers/{id}/toggle` | `toggle_trigger` | `TriggerFeature.toggle_trigger()` |
| `GET` | `/health` | `health_check` | Returns `{"status": "ok"}` |

#### Server Lifecycle

`run_server()` signature changes from `run_server(client: AlfredClient, ...)` to `run_server(client: AlfredClient, feature: TriggerFeature, ...)`. It builds a `FastAPI` app, attaches REST routes that close over the `TriggerFeature` instance and a JSON-RPC shim that delegates to `client.dispatch()`, and runs `uvicorn.Server` programmatically within the existing asyncio event loop. Port remains 8001. `__main__.py` must construct `TriggerFeature` before passing it to both the server and `AlfredClient` registration.

```python
app = FastAPI(title="Trigger Engine")
# Routes registered via app.post/get/patch/delete
config = uvicorn.Config(app, host="0.0.0.0", port=8001, log_level="info")
server = uvicorn.Server(config)
await server.serve()
```

#### JSON-RPC to REST Migration

The current server exposes a JSON-RPC endpoint that delegates to `AlfredClient.dispatch()`. The Reflex Engine and domain agents call trigger tools by sending JSON-RPC requests to `http://localhost:8001`. The new server replaces this with REST endpoints.

To maintain backward compatibility during transition, the FastAPI server includes a `POST /jsonrpc` endpoint that accepts JSON-RPC requests and delegates to `AlfredClient.dispatch()` — identical to the current behavior. This preserves the existing wire protocol while the REST endpoints provide the new interface.

The JSON-RPC shim can be removed once all callers are confirmed to use the SDK tool-call path (which resolves tools via Redis `alfred:tool_registry` and dispatches locally, not over HTTP).

#### What Stays the Same

- `AlfredClient` still registers tools in Redis `alfred:tool_registry` — this is how the Reflex Engine discovers trigger CRUD capabilities via the MCP/tool-call path
- The `POST /jsonrpc` endpoint preserves the existing wire protocol for any caller currently sending JSON-RPC to port 8001
- The REST endpoints are an **additional** interface for direct HTTP access (debugging, future UI)

#### What We Get

- Request validation via Pydantic (FastAPI native)
- Consistent stack with home-service (FastAPI + uvicorn)
- Auto-generated OpenAPI docs at `/docs`
- Easy to add middleware (auth, rate limiting, CORS) later
- Health check endpoint for the supervisor/monitoring

#### Dependencies

`fastapi` and `uvicorn[standard]` added to `pyproject.toml` as runtime dependencies.

### 4.4 __main__.py Changes

**File:** `core/triggers/__main__.py`

#### Startup Sequence

```
1. Connect Redis
2. Create TriggerStore → load() populates _cache
3. Create TriggerEngine (unchanged)
4. Register tools via AlfredClient (unchanged)
5. Build FastAPI app with TriggerFeature routes
```

#### Concurrent Tasks

| Task | Change |
|------|--------|
| `tick_loop` | Unchanged behavior, now hits cache via `list_all()` |
| `event_loop` | Unchanged behavior, now hits cache via `list_all()` |
| `snapshot_loop` | Unchanged |
| `refresh_loop` | **NEW** — calls `store.refresh()` every 60s (configurable) |
| `uvicorn server` | **REPLACES** raw `asyncio.start_server` |

---

## 5. Files Changed

| File | Change Type | Description |
|------|-------------|-------------|
| `core/triggers/store.py` | Modify | Add `_cache` dict, write-through on save/delete, cache-read on list_all, new `refresh()` |
| `core/triggers/types/composite.py` | Modify | Add `model_post_init` to pre-build child triggers, use `_cached_children` in `evaluate()` |
| `core/triggers/types/time.py` | Modify | Add `model_post_init` to pre-parse cron, use `_cached_cron` in `evaluate()` |
| `core/triggers/server.py` | Rewrite | FastAPI app with REST endpoints replacing raw asyncio server |
| `core/triggers/__main__.py` | Modify | Add `refresh_loop`, swap server startup to uvicorn |
| `pyproject.toml` | Modify | Add `fastapi`, `uvicorn[standard]` dependencies |

## 6. Testing Strategy

- **Unit tests for cache:** Verify `list_all()` returns cached data, `save()`/`delete()` update cache, `refresh()` re-syncs from Redis
- **Unit tests for model caching:** Verify `CompositeTrigger._cached_children` is populated at construction, `TimeTrigger._cached_cron` is populated at construction, both survive `model_copy()`
- **Integration test for FastAPI server:** Hit each REST endpoint, verify correct responses and side effects
- **Import-order sensitivity:** `model_post_init` in CompositeTrigger calls `TriggerRegistry.get()` at construction time. Tests that construct composite triggers must import `core.triggers.types` first (via `types/__init__.py`) to ensure type registration. This is the same constraint as runtime but surfaces earlier (at construction instead of evaluation).
- **Existing tests must pass:** All current trigger engine tests should pass without modification (public API is unchanged)

## 7. Risks

| Risk | Mitigation |
|------|-----------|
| Cache diverges from Redis | 60-second periodic refresh as safety net |
| `model_post_init` breaks serialization | Cached fields use Pydantic `model_config` exclude or private attributes (`_cached_*`) |
| uvicorn conflicts with existing event loop | Run via `uvicorn.Server.serve()` programmatically (proven pattern in home-service) |
| FastAPI adds startup latency | Negligible — app construction is <10ms |
