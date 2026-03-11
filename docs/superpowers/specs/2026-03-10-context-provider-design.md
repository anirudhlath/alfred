# ContextProvider & HA Entity Snapshot Design

## Problem

The Reflex Engine (System 1) makes tool calls based on HA entities, but has no awareness of what entities actually exist. Entity IDs are guesswork — the SLM hallucinates room names and entity formats. The LLM needs a persistent, structured snapshot of the smart home state to make informed decisions.

## Solution

A `ContextProvider` protocol in the SDK that lets any service publish structured context data to Redis. The Reflex Engine reads and renders this context into its prompt, giving the SLM full situational awareness.

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Scope | Controllable + readable entities (not system entities) | Lights, scenes, climate for actions; sensors for situational awareness. System entities add noise. |
| Storage | Redis (`alfred:context:{service_name}`) | Runtime data, not user-authored. Redis is the existing backbone. TTL handles stale data. |
| Format | Structured JSON grouped by domain | Clean for programmatic access. Consumer decides rendering. |
| Prompt integration | Dedicated `context_reader.py` module | Async with in-memory TTL cache (same pattern as `_get_tools_and_prompt()` in `engine.py`). |
| SDK enforcement | `ContextProvider` protocol + `BaseFeature` integration | Protocol is first-class in SDK. Features get it for free via `get_context()`. No hardcoded prompts. |
| Snapshot writer | home-service (existing refresh loop) | Already queries HA states periodically. Minimal new code. |
| Replaces per-tool enrichment | Yes — snapshot supersedes `setup()`/`to_manifest()` | The snapshot provides all entity IDs and state in the prompt. The SLM can read valid entity IDs from the Home State section rather than from individual tool parameter descriptions. One context source > scattered per-tool enrichment. |

## Data Models

All context payloads are Pydantic-validated (Pillar 3: Deterministic Communication).

```python
class ContextEntry(BaseModel):
    """A single entity's state snapshot."""
    entity_id: str
    state: str
    attributes: dict[str, Any] = {}

class ContextSnapshot(BaseModel):
    """Structured context from a service, grouped by domain."""
    controllable: dict[str, list[ContextEntry]] = {}
    sensors: dict[str, list[ContextEntry]] = {}
```

Features return `ContextSnapshot` sections. `AlfredClient.register()` merges them into a single `ContextSnapshot` per service before writing to Redis.

## Architecture

### SDK Layer

**New file: `sdk/alfred_sdk/context.py`**

Defines the protocol and data models:

```python
class ContextProvider(Protocol):
    async def get_context(self) -> ContextSnapshot:
        """Return structured context data for this provider."""
        ...
```

**Modified: `sdk/alfred_sdk/feature.py`**

`BaseFeature` implements `ContextProvider` structurally — gains a default `async get_context()` returning an empty `ContextSnapshot()`. Features override to provide domain-specific context. `BaseFeature` satisfies the `ContextProvider` protocol implicitly (structural subtyping).

**Modified: `sdk/alfred_sdk/client.py`**

During `register()`, after writing tool manifests to `alfred:tool_registry`:

1. Iterates all discovered features
2. Calls `await feature.get_context()` on each
3. Merges results: `controllable` dicts are merged by domain key (last writer wins per domain — each feature owns its domain, so no collisions in practice)
4. Serializes the merged `ContextSnapshot` to JSON
5. Writes via `redis.set("alfred:context:{service_name}", json, ex=600)` — SET with 600s (10 min) TTL

### home-service Layer

**LightingFeature.get_context()** queries `self.ha.get_states()` live and returns:

```python
ContextSnapshot(
    controllable={
        "light": [
            ContextEntry(entity_id="light.living_room", state="on", attributes={"brightness": 255}),
            ContextEntry(entity_id="light.bedroom", state="off"),
        ]
    }
)
```

**ScenesFeature.get_context()** — same pattern for scene entities.

**Data freshness:** `get_context()` queries HA live each time it's called. It's invoked during `register()`, which runs on the 5-minute refresh loop. If HA is unreachable, `get_context()` raises and the error is logged by the refresh loop (same as current behavior for tool re-registration).

**Reverts:** `setup()`, `to_manifest()` overrides, and `initialize_features()` are removed. The snapshot replaces per-tool parameter enrichment — the SLM reads valid entity IDs from the `## Home State` prompt section instead of from individual tool parameter descriptions.

**server.py:** The periodic refresh loop simplifies to just `await client.register()`, which now handles both tool registration and context publishing. No separate `initialize_features()` step.

### Consumer Layer (Reflex Engine)

**New file: `core/reflex/context_reader.py`**

- Async module that reads from Redis and caches in-memory with TTL
- Same caching pattern as `_get_tools_and_prompt()` in `engine.py`: `time.monotonic()` + TTL comparison
- Phase 1 shortcut: reads `alfred:context:home-service` directly (hardcoded to one service). This is temporary tech debt — when sub-agents land, each agent will be configured with which context keys to read.
- Deserializes JSON into `ContextSnapshot` (Pydantic validation on read)
- Renders into Markdown for the prompt:

```markdown
## Home State

### Lights
- light.living_room: on (brightness: 255)
- light.bedroom: off

### Scenes
- scene.movie_night

### Sensors
- sensor.temperature: 72°F
```

**Modified: `core/reflex/engine.py`**

Context is injected in `process_event()`, between the system prompt and preferences:

```python
prompt = (
    f"{system_prompt}\n\n"
    f"## Home State\n{rendered_context}\n\n"
    f"## User Preferences\n{preferences}\n\n"
    f"## Event\n..."
)
```

The `## Home State` section is NOT part of `_SYSTEM_PROMPT_TEMPLATE` — it's assembled in `process_event()` alongside preferences and event data.

### TTL Strategy

| Component | TTL | Purpose |
|---|---|---|
| Redis key (`alfred:context:*`) | 10 min | Auto-expire if service dies |
| `context_reader.py` in-memory cache | 5 min | Avoid Redis reads on every event |
| Worst-case staleness | 15 min | Acceptable — entity topology changes rarely |

The 5-minute write interval (home-service refresh loop) keeps the Redis key alive well within its 10-minute TTL.

## Files Changed

| File | Action |
|---|---|
| `sdk/alfred_sdk/context.py` | **New** — `ContextProvider` protocol, `ContextEntry`, `ContextSnapshot` models |
| `sdk/alfred_sdk/feature.py` | **Modified** — add default `async get_context()` returning empty `ContextSnapshot` |
| `sdk/alfred_sdk/client.py` | **Modified** — collect + merge + write context during `register()` |
| `shared/streams.py` | **Modified** — add `CONTEXT_KEY_PREFIX = "alfred:context:"` constant |
| `home-service/alfred_ext/features/lighting.py` | **Modified** — add `get_context()`, revert `setup()`/`to_manifest()` |
| `home-service/alfred_ext/features/scenes.py` | **Modified** — add `get_context()`, revert `setup()`/`to_manifest()` |
| `home-service/alfred_ext/register.py` | **Modified** — revert `initialize_features()` |
| `home-service/app/server.py` | **Modified** — simplify refresh loop |
| `core/reflex/context_reader.py` | **New** — async Redis reader with TTL cache, renders to Markdown |
| `core/reflex/engine.py` | **Modified** — inject rendered context into prompt via `process_event()` |

## Backlog

- **Agent-scoped context visibility** — when sub-agents land, each agent reads only its relevant `alfred:context:*` keys (e.g. home agent reads `home-service`, calendar agent reads `calendar-service`). Replace hardcoded `home-service` key in `context_reader.py`.
- **Option C entities** — include system entities, automations, scripts for full HA visibility
