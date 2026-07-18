# Event Bus

MQTT is the edge layer (HA/devices). Redis Streams is the internal backbone. The bridge is a thin forwarder — no business logic.

## Files

- `schemas/events.py` — Single source of truth for all event types (12 types: BaseEvent, StateChangedEvent, ActionRequest, ActionResult, TelemetryEvent, ToolRegistration, TriggerFired, UserRequest, AlfredResponse, TriggerCreated, ReflexObservation, ServiceRegistered)
- `bridge.py` — MQTT ↔ Redis Streams forwarder (topic conversion, bidirectional loops)
- `__main__.py` — Entry point, loads config, runs bridge

## Running

```bash
uv run python -m bus  # starts the MQTT-Redis Bridge
```

## Key Patterns

- Topic mapping: `home/state_changed` ↔ `alfred:home:state_changed` (simple `/` ↔ `:` swap)
- MQTT payloads stored as `{"event": "<json-bytes>"}` in Redis xadd — raw JSON string, not nested object
- Redis→MQTT loop uses blocking `xread(block=1000)` with per-stream last-seen ID tracking
- `UrgencyLevel = Literal["informational", "important", "urgent"]` is defined here — bus must NOT import `Urgency` enum from `core/notifications/schema.py` to avoid bus→core dependency

## Gotchas

- No connection recovery — bridge crashes on Redis/MQTT disconnect; relies on process supervisor (runner) to restart
- `xread` returns `dict[bytes | str | memoryview, ...]` — manual decoding required for both keys and values
- ActionRequest/ActionResult correlated by `request_id` but not enforced at bus layer
- TriggerFired and TriggerCreated hardcode `source = "trigger-engine"` — cannot be overridden

## Testing

```bash
pytest bus/  # 19 tests (bridge + schema validation + roundtrips)
```

Tests use `AsyncMock` — no real Redis/MQTT required.
