# Shared

Cross-cutting utilities used by multiple top-level packages (core, bus, domains).

## Files

- `config.py` — `AlfredConfig` frozen dataclass (30+ fields), `from_env()` loads `.env` via python-dotenv
- `streams.py` — Redis stream/key constants (single source of truth, incl. trigger-sync op constants)
- `redis_streams.py` — `create_redis()` constructor (socket_timeout=None — see Gotchas) + `StreamBatch` type alias + typed `read_group()`/`read()`/`forward_range()`/`revrange()` wrappers around `xreadgroup`/`xread`/`xrange`/`xrevrange` (owns the one stub-gap `# type: ignore` per wrapper)
- `usertime.py` — User timezone helpers: `is_valid_timezone()`, `get_user_timezone()` (stored → `ALFRED_TIMEZONE` → UTC), `set_user_timezone()` (write-on-change + coherence poke)
- `secrets.py` — Keyring wrapper: `get_secret()` / `set_secret()` + async versions (`aget_secret()` etc.) via `asyncio.to_thread()`
- `types.py` — `AioRedis` type alias (canonical location for cross-package use)
- `fs.py` — `atomic_write()` via mkstemp + rename (safe concurrent writes)
- `logging.py` — Loguru setup with stdlib intercept (captures uvicorn, httpx, aiomqtt, etc.)
- `traced.py` — `@traced` decorator for OTel span instrumentation (sync + async, optional name override)
- `tracing.py` — `ReflexTraceRecord` / `ConsciousTraceRecord` dataclasses + `init_tracing()` with optional OTLP export
- `otel.py` — TracerProvider init with Resource + optional OTLP BatchSpanProcessor
- `type_map.py` — `PYTHON_TO_JSON_SCHEMA` mapping + `friendly_type()` for LLM-friendly type strings

## Key Stream Constants

```python
EVENTS_STREAM = "alfred:events"
ACTIONS_STREAM = "alfred:actions"
HOME_STATE_STREAM = "alfred:home:state_changed"
REFLEX_OBSERVATIONS_STREAM = "alfred:reflex:observations"
USER_REQUESTS_STREAM = "alfred:user:requests"
NOTIFICATION_DISPATCH_STREAM = "alfred:notifications:dispatch"
TOOL_REGISTRY_KEY = "alfred:tool_registry"
CONTEXT_INDEX = "idx:context"       # RediSearch index name
CONTEXT_PREFIX = "ctx:"             # RediSearch key prefix
```

## Rules

- Keep this package dependency-free (no imports from core, bus, domains, or sdk)
- All Redis stream key strings MUST be defined here — never hardcode in consuming modules
- New shared utilities belong here only if used by 2+ top-level packages

## Gotchas

- `__init__.py` is empty — no centralized re-exports; import from specific modules
- Two tracing files: `traced.py` (per-function `@traced` decorator) vs `tracing.py` + `otel.py` (global provider init at startup)
- `.env` loaded automatically at `config.py` import time — walks up to parent of `shared/`
- `AlfredConfig` is frozen (immutable dataclass) — prevents accidental mutation
- `friendly_type()` is LLM-aware: converts `datetime` → `"string (ISO 8601)"` for Claude prompts
- `TraceRecord` is a backward-compat alias for `ReflexTraceRecord`
- Root `conftest.py` has autouse `_mock_keyring` fixture — all tests use `InMemoryKeyring`, never OS keychain
- redis-py 8 defaults `socket_timeout` to 5s (was `None`), which races idle blocking stream reads (`block=`) — always construct async Redis clients via `create_redis()` in this module, never `redis.asyncio.from_url()` directly (SDK is the sole exception — see `sdk/CLAUDE.md`)
- Every FastAPI app passes `telemetry=FASTAPI_TELEMETRY` (`otel.py`). FastAPI 0.142+ otherwise adds its own OTLP/HTTP exporter at startup from `OTEL_EXPORTER_OTLP_ENDPOINT`, which names `init_tracing`'s gRPC endpoint, so every batch is reset at the gRPC port ([#333](https://github.com/anirudhlath/alfred/issues/333)). Its request and WebSocket spans still export through `init_tracing`'s provider
