# Event Bus

- `schemas/events.py` — Single source of truth for all event types
- `bridge.py` — MQTT ↔ Redis Streams forwarder (no business logic)

MQTT is the edge layer (HA/devices). Redis Streams is the internal backbone.

## Running

```bash
uv run python -m bus  # starts the MQTT-Redis Bridge
```
