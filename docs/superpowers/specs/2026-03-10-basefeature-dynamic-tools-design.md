# BaseFeature & Dynamic Tool Registry

**Status:** Approved
**Date:** 2026-03-10
**Scope:** SDK BaseFeature abstraction, core ToolRegistry, Reflex Engine dynamic prompt

## Problem

The Reflex Engine hardcodes tool names and descriptions in `core/reflex/engine.py`. This violates Pillar 2 (Decoupled Domains) — tools should be discovered at runtime from the SDK tool registry, not maintained by hand. Adding a tool requires editing engine source code.

## Design Goals

1. Tool definitions live in the microservice, auto-extracted from Python code (docstrings + type hints)
2. Features group related tools into a single registrable unit
3. Alfred core discovers tools at runtime from Redis — no hardcoded tool lists
4. Adding a new tool = adding a method to a feature class. Nothing else.

## Architecture

```
Microservice (home-service)           Alfred Core
┌─────────────────────────┐    ┌──────────────────────────────┐
│ LightingFeature         │    │ ToolRegistry                 │
│   @tool dim_lights()    │    │   HGETALL alfred:tool_registry│
│   @tool turn_off()      │    │   parse manifests            │
│                         │    │   return list[ToolInfo]       │
│ SceneFeature            │    │         │                     │
│   @tool set_scene()     │    │         ▼                     │
│         │               │    │ ReflexEngine                  │
│         ▼               │    │   build_system_prompt(tools)  │
│ AlfredClient            │    │   process_event(event)        │
│   discover_features()   │    └──────────────────────────────┘
│   register() ──HSET──▶ Redis: alfred:tool_registry
│   unregister() ─HDEL─▶
└─────────────────────────┘
```

## SDK Layer (`alfred_sdk`)

### `@tool` Decorator

Marks a method on a `BaseFeature` subclass as a tool. Zero required arguments — everything is auto-extracted.

```python
from alfred_sdk import BaseFeature, tool

class LightingFeature(BaseFeature):
    feature_name = "lighting"

    @tool
    async def dim_lights(self, room: str, level: int) -> dict:
        """Dim the lights in a room.

        Args:
            room: The room to dim.
            level: Brightness level 0-100.
        """
        ...
```

- **Tool name:** `{feature_name}.{method_name}` → `lighting.dim_lights`
- **Description:** First line of docstring
- **Parameters:** Type from type hints, description from Google-style `Args:` docstring section. If a parameter has a type hint but no docstring entry, `description` is omitted. If there is no docstring at all, description is the empty string. Complex types (e.g., `dict[str, Any]`) use their `str()` representation. Default values are included when present.
- **Overrides:** `@tool(description="...", name="...")` when defaults aren't enough
- **Docstring format:** Google style only (`Args:` section with `name: description` entries). This matches the examples throughout the codebase.

### `BaseFeature` Base Class

Groups related tools. Auto-discovers `@tool`-decorated methods.

```python
class BaseFeature:
    feature_name: str  # Required — set by subclass

    def get_tools(self) -> list[ToolMeta]:
        """Iterate @tool-decorated methods, extract metadata."""
        ...
```

- No global instance registry — `discover_features()` creates and manages instances directly
- `get_tools()` introspects the instance for `@tool` methods and builds metadata
- Feature description comes from the class docstring

### `AlfredClient` Integration

```python
client = AlfredClient(service_name="home-service", ...)

# Auto-discover: scan package, find BaseFeature subclasses, instantiate with ctx
client.discover_features(package="alfred_ext.features", ctx=HomeServiceContext(ha=ha))

# register() now collects tools from discovered features
await client.register()    # HSET alfred:tool_registry <service> <manifest>

# Graceful shutdown
await client.unregister()  # HDEL alfred:tool_registry <service>
```

**`discover_features(package, ctx)`:**
1. `pkgutil.walk_packages()` to import all modules in the package
2. Inspect each module's members for `BaseFeature` subclasses defined in that module (not `__subclasses__()`, which leaks across packages)
3. Instantiate each with `ctx` (shared context object)
4. For each instance, iterate `@tool`-decorated methods and register bound methods in the dispatch table
5. Returns `list[BaseFeature]` — the client stores these internally

**Dispatch:** `client.dispatch("lighting.dim_lights", params)` routes to the bound method on the feature instance. The dispatch table is a flat `dict[str, Callable]` keyed by qualified tool name (`{feature_name}.{method_name}`). On name collision, the later registration wins and a warning is logged.

### Context Pattern

Each microservice defines its own context type with shared dependencies:

```python
class HomeServiceContext:
    def __init__(self, ha: HomeAssistantClient) -> None:
        self.ha = ha
```

Features receive this context and pull what they need:

```python
class LightingFeature(BaseFeature):
    feature_name = "lighting"

    def __init__(self, ctx: HomeServiceContext) -> None:
        super().__init__()
        self.ha = ctx.ha
```

## Redis Registry

### Key Structure

- **Hash key:** `alfred:tool_registry`
- **Field:** service name (e.g., `"home-service"`)
- **Value:** JSON manifest

### Manifest Schema

```json
{
  "service_name": "home-service",
  "service_endpoint": "http://localhost:8000/mcp",
  "features": [
    {
      "name": "lighting",
      "description": "Smart home lighting controls.",
      "tools": [
        {
          "name": "lighting.dim_lights",
          "description": "Dim the lights in a room.",
          "parameters": {
            "room": {"type": "str", "description": "The room to dim."},
            "level": {"type": "int", "description": "Brightness level 0-100."}
          }
        }
      ]
    }
  ]
}
```

### Manifest Pydantic Models (write-side, in SDK)

```python
class ToolParameter(BaseModel):
    type: str
    description: str = ""
    default: Any = None

class ToolManifest(BaseModel):
    name: str
    description: str
    parameters: dict[str, ToolParameter]

class FeatureManifest(BaseModel):
    name: str
    description: str
    tools: list[ToolManifest]

class ServiceManifest(BaseModel):
    service_name: str
    service_endpoint: str
    features: list[FeatureManifest]
```

These enforce Pillar 3 (deterministic communication) on the write side. `ToolRegistry` on the read side uses `ToolInfo` dataclasses for lightweight in-memory representation.

### Lifecycle

- **Register:** `HSET` on startup. Overwrites existing entry (no duplicates).
- **Unregister:** `HDEL` on graceful shutdown (SIGTERM/SIGINT via signal handlers). Uses the same throwaway Redis connection pattern as `register()`. Idempotent — safe to call if already unregistered (HDEL on a missing field is a no-op).
- **Crash:** Stale entry remains. Tool calls fail gracefully (HomeAgent returns error ActionResult). Entry is overwritten on restart. No TTL or heartbeat — the failure mode is benign. (Heartbeat is deferred to a future iteration; the main spec mentions it but the cost/complexity is not justified for the current tool count.)

## Core Layer

### `ToolRegistry` (`core/reflex/tool_registry.py`)

Thin read layer over Redis. No caching — Redis HGETALL is sub-millisecond and tools change rarely.

```python
@dataclass(frozen=True)
class ToolInfo:
    name: str              # e.g. "lighting.dim_lights"
    description: str       # From docstring
    parameters: dict       # {param_name: {type, description}}
    feature_name: str      # e.g. "lighting"
    feature_description: str
    target_service: str    # e.g. "home-service"

class ToolRegistry:
    def __init__(self, redis: AioRedis) -> None:
        self._redis = redis

    async def get_tools(self) -> list[ToolInfo]:
        """HGETALL, parse all manifests, return flat tool list."""
        ...
```

### Reflex Engine Changes

- `ReflexEngine.__init__` takes a `ToolRegistry` instead of nothing
- `SYSTEM_PROMPT` becomes a method `_build_system_prompt(tools)` that formats tools dynamically
- `_TARGET_SERVICE` constant is removed — `target_service` comes from the tool's metadata
- `_parse_response` validates that `target_service` is in the set of registered services (extracted from the same `get_tools()` call used to build the prompt)

**Generated prompt (example):**

```
You are Alfred's Reflex Engine — a fast-acting steward for a smart home.

Given an event and the user's preferences, decide if an action is needed.

Rules:
- Only act if the event clearly matches a user preference
- If no action is needed, respond with: {"action": "none"}
- If an action IS needed, respond with:
  {"tool_name": "<tool>", "target_service": "<service>", "parameters": {<params>}}

Available tools:

## lighting [home-service] — Smart home lighting controls.
- lighting.dim_lights(room: str, level: int) — Dim the lights in a room.
- lighting.turn_off_lights(room: str) — Turn off all lights in a room.

## scenes [home-service] — Smart home scene management.
- scenes.set_scene(scene_name: str) — Activate a Home Assistant scene.

Respond ONLY with valid JSON. No explanation.
```

### Fail-Fast at Startup

The `__main__.py` calls `await registry.get_tools()` before entering the event loop. If it returns an empty list, raise `RuntimeError` immediately — do not enter the loop. This is a startup check, not a lazy check on first event.

## Future Extensions (not in this implementation)

- **Domain-filtered prompts:** When tool count grows, filter by event domain/entity to keep context short
- **Two-stage prompts:** Broadcast feature summaries, fetch tool details on demand
- **Feature-level routing:** SLM selects a feature first, then gets its tools

## Tool Registration Standard

`BaseFeature` + `@tool` is the **only** way to define tools. The old `@mcp_tool` decorator and `@client.tool()` method are deleted. `sdk/alfred_sdk/mcp.py` is removed entirely.

Tool names follow the `{feature_name}.{method_name}` convention (e.g., `lighting.dim_lights`).

## Migrated Home-Service Example

### Before (`home-service/alfred_ext/register.py`)

```python
client = AlfredClient(service_name="home-service", ...)
ha = HomeAssistantClient(...)

@client.tool(name="smart_home.dim_lights", description="Dim lights in a room to a level (0-100)")
async def dim_lights(room: str, level: int) -> dict:
    entity_id = f"light.{room}"
    await ha.call_service("light", "turn_on", entity_id, brightness=level)
    return {"entity_id": entity_id, "brightness": level}
# ... more standalone functions
```

### After

**`home-service/alfred_ext/register.py`** (simplified):

```python
from alfred_sdk import AlfredClient
from app.ha_client import HomeAssistantClient

ha = HomeAssistantClient(...)
client = AlfredClient(service_name="home-service", ...)

class HomeServiceContext:
    def __init__(self, ha: HomeAssistantClient) -> None:
        self.ha = ha

client.discover_features(
    package="alfred_ext.features",
    ctx=HomeServiceContext(ha=ha),
)
```

**`home-service/alfred_ext/features/lighting.py`** (new):

```python
from alfred_sdk import BaseFeature, tool
from alfred_ext.register import HomeServiceContext

class LightingFeature(BaseFeature):
    """Smart home lighting controls."""

    feature_name = "lighting"

    def __init__(self, ctx: HomeServiceContext) -> None:
        super().__init__()
        self.ha = ctx.ha

    @tool
    async def dim_lights(self, room: str, level: int) -> dict:
        """Dim the lights in a room.

        Args:
            room: The room to dim.
            level: Brightness level 0-100.
        """
        entity_id = f"light.{room}"
        brightness = int(level * 2.55)  # Convert 0-100 to 0-255
        await self.ha.call_service(
            "light", "turn_on", {"entity_id": entity_id, "brightness": brightness}
        )
        return {"entity_id": entity_id, "brightness": level}

    @tool
    async def turn_off_lights(self, room: str) -> dict:
        """Turn off all lights in a room.

        Args:
            room: The room to turn off.
        """
        entity_id = f"light.{room}"
        await self.ha.call_service("light", "turn_off", {"entity_id": entity_id})
        return {"entity_id": entity_id, "state": "off"}
```

**`home-service/alfred_ext/features/scenes.py`** (new):

```python
from alfred_sdk import BaseFeature, tool
from alfred_ext.register import HomeServiceContext

class SceneFeature(BaseFeature):
    """Smart home scene management."""

    feature_name = "scenes"

    def __init__(self, ctx: HomeServiceContext) -> None:
        super().__init__()
        self.ha = ctx.ha

    @tool
    async def set_scene(self, scene_name: str) -> dict:
        """Activate a Home Assistant scene.

        Args:
            scene_name: The scene to activate.
        """
        entity_id = f"scene.{scene_name}"
        await self.ha.call_service("scene", "turn_on", {"entity_id": entity_id})
        return {"scene": scene_name, "activated": True}
```

## Files Changed

### New Files
- `sdk/alfred_sdk/feature.py` — `BaseFeature`, `@tool`, `ToolMeta`, docstring parser, Pydantic manifest models
- `core/reflex/tool_registry.py` — `ToolRegistry`, `ToolInfo`
- `home-service/alfred_ext/features/lighting.py` — `LightingFeature`
- `home-service/alfred_ext/features/scenes.py` — `SceneFeature`

### Deleted Files
- `sdk/alfred_sdk/mcp.py` — Replaced by `@tool` decorator in `feature.py`

### Modified Files
- `sdk/alfred_sdk/client.py` — Remove `tool()` method, add `discover_features()`, `unregister()`, feature-only `register()` and `dispatch()`
- `sdk/alfred_sdk/__init__.py` — Export `BaseFeature`, `tool` (remove `mcp_tool`)
- `core/reflex/engine.py` — Dynamic prompt, accept `ToolRegistry`, remove hardcoded tools
- `core/reflex/__main__.py` — Wire `ToolRegistry` into engine, fail-fast startup
- `home-service/alfred_ext/register.py` — Rewrite for `discover_features()`

### Documentation Updates
- `alfred/CLAUDE.md` — Update design principles for BaseFeature-only architecture
- `sdk/CLAUDE.md` — Replace with BaseFeature-only exports
- `core/CLAUDE.md` — Add ToolRegistry to components
- `.claude/rules/sdk/sdk-design.md` — BaseFeature-only pattern
- `.claude/rules/core/reflex-engine.md` — Add "reads tools from ToolRegistry"
