# alfred-sdk

Publishable Python package. The ONLY coupling between Alfred and external apps.

- Must work standalone — no imports from alfred core, bus, or domains
- Keep dependencies minimal: only `pydantic>=2.0` and `redis>=5.0` (telemetry optional)
- Not published to PyPI — container builds install from source path

## Files

- `alfred_sdk/__init__.py` — Core exports: `AlfredClient`, `BaseFeature`, `tool`, telemetry decorators
- `alfred_sdk/feature.py` — `BaseFeature` class, `@tool` decorator, Google docstring parser, manifest builders
- `alfred_sdk/client.py` — `AlfredClient`: discovery, dispatch, registration (Redis HSET)
- `alfred_sdk/events.py` — Standalone wire-compatible event schemas (mirrors `bus/schemas/events.py`)
- `alfred_sdk/context.py` — `ContextSnapshot`, `ContextEntry`, `ContextProvider` protocol
- `alfred_sdk/telemetry.py` — In-memory decorators: `@track_latency`, `@track_tokens`, `@track_event`

## Key Patterns

- `BaseFeature` + `@tool` is the ONLY way to define tools — auto-extracts metadata from docstrings + type hints
- `@tool` supports `@tool` and `@tool(name="custom.name", description="...")` — qualified as `{feature_name}.{method_name}` by default
- `AlfredClient.discover_features(package="my_app.features")` scans for `BaseFeature` subclasses, instantiates, populates dispatch table
- `client.register()` → `HSET alfred:tool_registry` + context write with 10min TTL
- `client.unregister()` → `HDEL alfred:tool_registry` on graceful shutdown
- `client.dispatch("feature.tool_name", params)` routes to bound method (async + sync supported)

## Testing

```bash
pytest sdk/                       # all SDK tests
pytest sdk/alfred_sdk/tests/      # unit tests (feature, client, telemetry)
pytest sdk/tests/                 # integration tests (schema compatibility)
```

## Gotchas

- `events.py` are standalone copies of `bus/schemas/events.py` — `test_schema_compatibility.py` verifies round-trip serialization; must pass after any schema changes
- Never put `conftest.py` in `tests/` — causes namespace collision with `sdk/tests/` (both have `__init__.py`). Use root `conftest.py` for repo-wide fixtures.
- Tool name collisions: last registration wins, logged as warnings
- Context key `alfred:context:{service-name}` expires in 600s (10min) — long-running services need periodic re-registration
- Complex type hints stored as `str()` representation in manifests (e.g., `dict[str, Any]`)
