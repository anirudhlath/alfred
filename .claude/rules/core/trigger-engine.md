---
paths:
  - "core/triggers/**"
---

# Trigger Engine Rules

- Trigger types are open for extension via `@TriggerRegistry.register_type()` — never hardcode type lists
- `core/triggers/types/__init__.py` must import all type modules to trigger registration decorators
- `BaseTrigger` subclasses MUST define a nested `Conditions(BaseModel)` class for schema introspection
- `TriggerFeature.get_tools()` injects dynamic descriptions — never hardcode condition schemas in tool docstrings
- Storage: Redis hash `alfred:triggers` is the runtime source of truth; YAML snapshots are for cold-start recovery only
- Fire logic: if `action` is set → publish `ActionRequest`; if `None` → publish `TriggerFired` for Reflex to handle
- All Redis stream keys come from `shared.streams` — never use string literals
- `AioRedis` type alias comes from `core.reflex.runner` — never redefine
- `ensure_consumer_group` comes from `core.reflex.runner` — never reimplement
- Sync file I/O in async methods must use `run_in_executor`
