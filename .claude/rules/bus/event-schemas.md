---
paths:
  - "bus/**"
---

# Event Bus Rules

- bus/schemas/events.py is the SINGLE SOURCE OF TRUTH for all event types
- All events are Pydantic v2 BaseModel subclasses
- Events are immutable once published — never mutate after creation
- MQTT topics map to Redis Stream keys via the bridge
- The bridge is a thin forwarder — no business logic
- Use consumer groups in Redis Streams for load balancing
