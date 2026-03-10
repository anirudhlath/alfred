# Trigger Engine — System Design Specification

**Date:** 2026-03-10
**Status:** Approved
**Phase:** 2
**Author:** Anirudh Lath + Claude (Lead Engineer)

---

## 1. Purpose

The Trigger Engine adds **proactive behavior** to Alfred. Where the Reflex Engine is reactive (event arrives → decide action), the Trigger Engine fires actions based on time schedules, sensor conditions, or composite rules — without waiting for a new event.

Triggers are never hardcoded. They are created dynamically by the Reflex Engine (via tool calls), by users (via explicit commands), or by future agents. The engine evaluates them continuously and either executes actions directly or emits events for the Reflex Engine to reason about.

---

## 2. Architecture Overview

The Trigger Engine is an internal core service in `core/triggers/`, running as its own process (`python -m core.triggers`). It follows the same patterns as the Reflex Runner and Bridge — a long-running async process in the monorepo.

### Position in Alfred OS

```
Event Bus (Redis Streams)
    │
    ├──→ Reflex Engine (reactive: event → action)
    │
    └──→ Trigger Engine (proactive: conditions → action or event)
              │
              ├── Evaluates TimeTriggers on tick (1s interval)
              ├── Evaluates SensorTriggers on incoming events
              ├── Evaluates CompositeTriggers on both
              │
              └── On fire:
                  ├── action set → publish ActionRequest to alfred:actions
                  └── no action  → publish TriggerFired to alfred:events (Reflex handles)
```

### CRUD via Tool Registry

The engine exposes trigger management as a `BaseFeature` with `@tool` methods, registered in the tool registry like any other service. The Reflex Engine sees `triggers.create_trigger`, `triggers.list_triggers`, etc. in its tool list and can call them via the standard HTTP/JSON-RPC dispatch path.

---

## 3. BaseTrigger ABC and Type Registry

### BaseTrigger

```python
class BaseTrigger(ABC, BaseModel):
    """Abstract trigger. Subclasses define evaluation logic and conditions schema.

    Every concrete subclass MUST define a `conditions` field typed to its own
    nested `Conditions` Pydantic model. This is how TriggerRegistry.build_conditions_docs()
    introspects available schemas for dynamic tool descriptions.
    """

    trigger_id: str          # UUID, auto-generated
    trigger_type: str        # Matches TriggerRegistry key
    name: str                # Human-readable label
    enabled: bool = True     # Can be paused without deleting
    one_shot: bool = False   # Delete after firing
    created_by: str          # "reflex-engine", "user", etc.
    created_at: datetime
    last_fired: datetime | None = None
    action: ActionPayload | None = None  # Direct execution or emit for Reflex

    @abstractmethod
    def evaluate(self, context: TriggerContext) -> bool:
        """Return True if this trigger should fire now."""
```

### ActionPayload

```python
class ActionPayload(BaseModel):
    """Action to execute when a trigger fires.

    Contains the subset of ActionRequest fields needed to describe the action.
    The Trigger Engine converts this to a full ActionRequest on fire, setting
    source="trigger-engine" and generating event metadata (event_id, timestamp).
    """

    tool_name: str
    target_service: str
    parameters: dict[str, Any] = {}
```

### TriggerContext

```python
class TriggerContext(BaseModel):
    """Read-only context passed to evaluate()."""

    now: datetime
    event: StateChangedEvent | None = None  # Present when evaluating against an incoming event
```

### TriggerRegistry

Decorator-based, open for extension. No enums, no hardcoded type lists.

```python
class TriggerRegistry:
    _registry: dict[str, type[BaseTrigger]] = {}

    @classmethod
    def register_type(cls, trigger_type: str) -> Callable:
        """Class decorator: @TriggerRegistry.register_type("time")"""

    @classmethod
    def get(cls, trigger_type: str) -> type[BaseTrigger]:
        """Look up trigger class by type string. Raises KeyError if unknown."""

    @classmethod
    def available_types(cls) -> list[str]:
        """Return all registered trigger type names."""

    @classmethod
    def build_conditions_docs(cls) -> str:
        """Introspect all registered types and their Conditions schemas.
        Returns a formatted string for dynamic tool descriptions."""
```

Adding a new trigger type requires:
1. Subclass `BaseTrigger`
2. Define a `Conditions` Pydantic model as a nested class
3. Implement `evaluate()`
4. Decorate with `@TriggerRegistry.register_type("my_type")`

No changes to CRUD tools, engine loop, or any other code.

---

## 4. Concrete Trigger Types

### TimeTrigger

Fires on cron schedule or specific datetime.

```python
@TriggerRegistry.register_type("time")
class TimeTrigger(BaseTrigger):
    trigger_type: str = "time"

    class Conditions(BaseModel):
        cron: str | None = None       # Cron expression (e.g., "0 7 * * 1-5")
        run_at: datetime | None = None  # One-time fire at specific datetime

    conditions: Conditions
```

`evaluate()` checks cron match against `context.now` or `run_at <= context.now`.

### SensorTrigger

Fires when an incoming event matches conditions.

```python
@TriggerRegistry.register_type("sensor")
class SensorTrigger(BaseTrigger):
    trigger_type: str = "sensor"

    class Conditions(BaseModel):
        entity_id: str
        state_match: str | None = None
        attribute_match: dict[str, Any] | None = None

    conditions: Conditions
```

`evaluate()` checks `context.event` against entity_id, state, and attribute filters.

### CompositeTrigger

Fires when N of M child conditions are met. Enables multi-condition triggers like "TV on AND after sunset."

```python
@TriggerRegistry.register_type("composite")
class CompositeTrigger(BaseTrigger):
    trigger_type: str = "composite"

    class Conditions(BaseModel):
        children: list[dict[str, Any]]  # Each child is a {trigger_type, conditions} pair
        require: int                     # How many children must evaluate to True

    conditions: Conditions
```

`evaluate()` instantiates child triggers (via `TriggerRegistry`), evaluates each against the full `TriggerContext` (which always includes `now` and optionally an `event`), and counts how many are satisfied. Both the tick loop and event listener pass the same `TriggerContext` shape — the tick loop sets `event=None`, the event listener sets it to the current event. This means a composite with mixed time+sensor children works correctly: the time child evaluates against `context.now`, the sensor child evaluates against `context.event` (returning False if no event is present). Both evaluation paths evaluate all composites on every tick/event.

---

## 5. Storage and Persistence

### Primary Store: Redis

- Redis hash key: `alfred:triggers`
- Field: `trigger_id` → Value: JSON-serialized trigger (via Pydantic `.model_dump_json()`)
- All CRUD operations go through Redis first — it is the runtime source of truth

### Disk Snapshots: `core/memory/triggers/`

- One YAML file per trigger: `core/memory/triggers/{trigger_id}.yaml`
- Written on every create/update/delete and periodically (every 5 minutes)
- Human-readable, git-diffable, manually editable
- Example:

```yaml
---
trigger_id: "abc-123"
trigger_type: sensor
name: "Dim lights when TV on"
enabled: true
one_shot: false
created_by: reflex-engine
created_at: 2026-03-10T14:30:00Z
last_fired: null
conditions:
  entity_id: media_player.living_room
  state_match: "on"
action:
  tool_name: smart_home.dim_lights
  target_service: home-service
  parameters:
    room: living_room
    level: 30
---
```

### Recovery

On startup:
1. Load from Redis
2. If Redis is empty (cold start, flush), rehydrate from YAML files on disk
3. Redis takes precedence when populated

### TriggerStore Interface

```python
class TriggerStore:
    """Redis CRUD + YAML snapshot/rehydration."""

    async def load(self) -> list[BaseTrigger]:
        """Load all triggers from Redis, falling back to disk."""

    async def save(self, trigger: BaseTrigger) -> None:
        """Write to Redis + snapshot to YAML."""

    async def delete(self, trigger_id: str) -> None:
        """Remove from Redis + delete YAML file."""

    async def list_all(self, enabled_only: bool = False) -> list[BaseTrigger]:
        """Return all triggers, optionally filtered."""

    async def snapshot_all(self) -> None:
        """Dump all triggers to YAML (periodic task)."""

    def rehydrate_from_disk(self) -> list[BaseTrigger]:
        """Read all YAML files and return trigger instances."""
```

---

## 6. Trigger Engine Service

### Entry Point: `python -m core.triggers`

### Main Loop: Two Concurrent Async Tasks

**1. Tick loop (1-second interval)**
- Iterates all enabled triggers
- Evaluates `TimeTrigger` cron/datetime against current time
- Evaluates `CompositeTrigger` children that include time conditions
- Fires any that match

**2. Event listener**
- Subscribes to `alfred:events` Redis Stream (own consumer group: `trigger-engine`)
- Filters for `StateChangedEvent` only (matching the Reflex Runner pattern; other event types are ignored)
- For each event, evaluates all enabled `SensorTrigger`s and `CompositeTrigger`s with sensor children
- Fires any that match

### Fire Logic

When a trigger fires:
1. If `action` is set → convert `ActionPayload` to full `ActionRequest` (with `source="trigger-engine"`, auto-generated `event_id`, `timestamp`) → publish to `alfred:actions` stream
2. If `action` is None → publish `TriggerFired` event (with `source="trigger-engine"`) to `alfred:events` (Reflex reasons about it)
3. If `one_shot` → delete the trigger
4. Update `last_fired` timestamp in store
5. Log observation to scratchpad via Redis List

### Startup Sequence

1. Connect to Redis
2. Import `core.triggers.types` (auto-registers trigger types via decorators)
3. `TriggerStore.load()` — Redis first, disk fallback
4. `TriggerEngine` starts tick loop + event listener
5. `AlfredClient` discovers `TriggerFeature`, calls `client.register()`
6. HTTP server starts for tool dispatch (MCP/JSON-RPC)
7. Runs until SIGINT/SIGTERM → `client.unregister()` on shutdown

---

## 7. CRUD Tools via BaseFeature

```python
class TriggerFeature(BaseFeature):
    """Manage dynamic triggers — create, list, update, delete."""

    feature_name = "triggers"

    @tool
    async def create_trigger(
        self,
        name: str,
        trigger_type: str,
        conditions: dict[str, Any],
        action: dict[str, Any] | None = None,
        one_shot: bool = False,
    ) -> dict[str, Any]:
        """Create a new trigger."""
        # 1. TriggerRegistry.get(trigger_type) → class
        # 2. Class.Conditions(**conditions) → Pydantic validates
        # 3. ActionPayload(**action) if provided → Pydantic validates
        # 4. Store in Redis + snapshot to YAML
        # 5. Return trigger dict

    @tool
    async def list_triggers(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        """List all triggers."""

    @tool
    async def update_trigger(
        self,
        trigger_id: str,
        conditions: dict[str, Any] | None = None,
        action: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing trigger's conditions, action, or name."""

    @tool
    async def delete_trigger(self, trigger_id: str) -> dict[str, str]:
        """Delete a trigger by ID."""

    @tool
    async def toggle_trigger(self, trigger_id: str, enabled: bool) -> dict[str, Any]:
        """Enable or disable a trigger."""
```

### Dynamic Tool Descriptions

`TriggerFeature` overrides `get_tools()` to inject dynamically-built descriptions from `TriggerRegistry.build_conditions_docs()`. This means the Reflex Engine's tool prompt includes the available trigger types and their condition schemas — the SLM knows exactly what parameters to pass.

Example generated description for `create_trigger`:
```
Create a new trigger.

Available trigger types and their conditions:
  - time: {cron?: str (cron expression), run_at?: datetime (ISO 8601)}
  - sensor: {entity_id: str, state_match?: str, attribute_match?: dict}
  - composite: {children: list of {trigger_type, conditions}, require: int}

action (optional): {tool_name: str, target_service: str, parameters: dict}
If omitted, fires a TriggerFired event for the Reflex Engine to handle.
```

---

## 8. Event Schema Changes

### New: TriggerFired

```python
class TriggerFired(BaseEvent):
    """A trigger's conditions were met. Emitted when trigger has no direct action."""

    event_type: str = "trigger_fired"
    source: str = "trigger-engine"
    trigger_id: str
    trigger_name: str
    trigger_type: str  # "time", "sensor", "composite", or any registered type
    context: dict[str, Any]  # What caused it to fire (time, event summary, etc.)
```

### Breaking Change: TriggerCreated

The existing `TriggerCreated` in `events.py` is a **breaking change** — the Phase 1 placeholder used `action: ActionRequest` (required) and type labels `"scheduled | event_conditional | composite"`. The new schema uses `action: dict[str, Any] | None` (optional) and type labels that match `TriggerRegistry` keys (`"time"`, `"sensor"`, `"composite"`, etc.). Since `TriggerCreated` was a Phase 2 placeholder with no consumers yet, this is safe to replace in-place.

```python
class TriggerCreated(BaseEvent):
    """A trigger was dynamically created."""

    event_type: str = "trigger_created"
    source: str = "trigger-engine"
    trigger_id: str
    trigger_type: str  # Matches TriggerRegistry key
    name: str
    created_by: str
    conditions: dict[str, Any]
    action: dict[str, Any] | None = None
    one_shot: bool = False
```

---

## 9. File Structure

```
core/triggers/
├── __init__.py
├── __main__.py          # Entry point: python -m core.triggers
├── models.py            # BaseTrigger ABC, ActionPayload, TriggerContext
├── registry.py          # TriggerRegistry (decorator-based)
├── types/
│   ├── __init__.py      # Imports all types (triggers auto-register on import)
│   ├── time.py          # TimeTrigger
│   ├── sensor.py        # SensorTrigger
│   └── composite.py     # CompositeTrigger
├── store.py             # TriggerStore: Redis CRUD + YAML snapshot/rehydration
├── engine.py            # TriggerEngine: tick loop + event listener + fire logic
├── feature.py           # TriggerFeature: BaseFeature + @tool CRUD
├── server.py            # HTTP endpoint for tool dispatch (MCP/JSON-RPC)
└── tests/
    ├── test_models.py
    ├── test_registry.py
    ├── test_store.py
    ├── test_engine.py
    └── test_feature.py
```

---

## 10. Dependencies and Changes

### New dependencies
- `croniter` — for cron expression parsing in `TimeTrigger`
- `pyyaml` — for YAML serialization of trigger snapshots

### What changes
- **`bus/schemas/events.py`** — `TriggerCreated` is replaced (breaking change, no existing consumers). `TriggerFired` is added. See Section 8.

### What does NOT change
- **Reflex Engine** — no code changes; it just sees new tools in the registry
- **SDK** — no changes; `TriggerFeature` uses existing `BaseFeature` + `@tool`
- **Bridge** — no changes; events flow through existing streams
- **Home Agent** — no changes; receives `ActionRequest` as before

### New directory
- `core/memory/triggers/` — YAML snapshot storage (gitignored — runtime data, same rationale as `scratchpad.md`)

---

## 11. Data Flows

### SLM Creates a Trigger

```
User: "dim the lights at sunset every day"
  → StateChangedEvent (voice command) arrives on Event Bus
  → Reflex Engine processes event
  → SLM sees triggers.create_trigger in tool list
  → Outputs: ActionRequest {
      target_service: "trigger-engine",
      tool_name: "triggers.create_trigger",
      parameters: {
        name: "dim lights at sunset",
        trigger_type: "time",
        conditions: {cron: "0 * * * *"},  # simplified; real sunset calc is future work
        action: {tool_name: "smart_home.dim_lights", target_service: "home-service",
                 parameters: {room: "living_room", level: 30}}
      }
    }
  → Action dispatcher forwards to Trigger Engine HTTP endpoint
  → TriggerFeature.create_trigger() validates and stores
  → TriggerCreated event published to bus
```

### Trigger Fires with Direct Action

```
TimeTrigger cron matches current time
  → TriggerEngine.fire(trigger)
  → trigger.action is set
  → Publish ActionRequest to alfred:actions stream
  → Domain agent picks up and executes
  → Log to scratchpad
  → If one_shot: delete trigger
```

### Trigger Fires without Action (Reflex Handles)

```
SensorTrigger: media_player.living_room turns "on"
  → TriggerEngine.fire(trigger)
  → trigger.action is None
  → Publish TriggerFired to alfred:events
  → Reflex Engine picks up TriggerFired event
  → SLM reasons about what action to take given preferences
  → Outputs ActionRequest
```
