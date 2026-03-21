# Proactive Notification System — Design Spec

**Date:** 2026-03-20
**Status:** Approved
**Backlog Item:** D9 (Proactive notification dispatch + DND + priority routing)

---

## 1. Overview

Alfred proactively reaches out to the user through multiple channels based on urgency, context, and user availability. The system uses a deterministic dispatcher for fast routing, while the Conscious Engine (System 2) handles contextual urgency escalation. DND state is managed through memory and composed from existing primitives (Pillar 5: Fluid Intelligence).

### Goals

- Route notifications to appropriate channels based on urgency
- Respect user availability (DND) without polling
- Support all notification sources: triggers, integrations, conscious engine suggestions, system alerts
- Deliver urgent notifications immediately regardless of DND state
- Compose DND and scheduling from existing primitives (triggers, memory, state) — no purpose-built tools

### Non-Goals (Deferred)

- Notification deduplication/cooldown (backlog P3)
- LLM-powered dispatcher sub-agent (backlog P2)
- Polling → pub/sub migration for Signal Bridge (backlog P1)
- Sleep-based DND via Librarian pattern detection (depends on D3)

---

## 2. Architecture

### Notification Flow

```
Source (trigger / integration / conscious engine / cost tracker)
  │
  ▼
NotificationPublisher.publish(title, body, urgency, source)
  │
  ▼
Dispatcher.dispatch(notification)
  │
  ├─ DNDChecker.is_active()
  │    ├─ Check manual DND state in Redis memory
  │    └─ Check calendar for active meeting
  │
  ├─ If DND active + not urgent → append to alfred:notifications:deferred → return
  │
  ├─ If urgent OR DND inactive:
  │    ├─ ChannelRegistry.get_adapters_for_urgency(urgency)
  │    └─ asyncio.gather(*[adapter.deliver(notification) for adapter in adapters])
  │
  ▼
Done
```

### Deferred Drain (Triggered, Not Polled)

```
DND expires → time trigger fires → TriggerEngine publishes ActionRequest
  │
  ▼
DrainDeferredAction: read all from alfred:notifications:deferred
  │
  ▼
Re-submit each through Dispatcher.dispatch() (DND now inactive → delivers normally)
```

### Urgency Escalation Path

- Source sets base urgency (e.g., trigger fires with `urgency="informational"`)
- If the event routes through Conscious Engine, it can escalate based on context (time of day, user state, pattern recognition) before publishing
- Dispatcher never downgrades, only routes based on final urgency

---

## 3. Notification Schema

Added to `bus/schemas/events.py`:

```python
class Urgency(str, Enum):
    INFORMATIONAL = "informational"
    IMPORTANT = "important"
    URGENT = "urgent"

class Notification(BaseModel):
    notification_id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    body: str
    urgency: Urgency
    source: str
    timestamp: datetime
```

---

## 4. Dispatcher

**Module:** `core/notifications/dispatcher.py`

The dispatcher is a thin deterministic router. No LLM calls, no complex logic. It:

1. Receives a `Notification` from `NotificationPublisher`
2. Checks DND state via `DNDChecker`
3. If DND active and urgency is not urgent → appends to `alfred:notifications:deferred` (Redis list)
4. Otherwise → queries `ChannelRegistry` for adapters supporting the notification's urgency level → delivers in parallel via `asyncio.gather`

```python
class NotificationDispatcher:
    def __init__(self, redis: AioRedis, dnd_checker: DNDChecker) -> None: ...

    async def dispatch(self, notification: Notification) -> None:
        """Route notification to appropriate channels, respecting DND."""
        ...

    async def drain_deferred(self) -> None:
        """Drain all deferred notifications through the dispatcher."""
        ...
```

### Changes to NotificationPublisher

`NotificationPublisher.publish()` currently writes directly to `NOTIFICATIONS_STREAM` via `xadd`. It will be refactored to call `NotificationDispatcher.dispatch()` instead. The dispatcher handles channel routing.

The existing `channel` parameter is removed — it was a delivery target hint (`"cost_alert"`) that is now replaced by `source` in the `Notification` model. `source` serves as provenance metadata; delivery targets are determined by the Dispatcher + ChannelRegistry based on urgency.

### Urgency Migration

The existing code uses string urgency values that don't match the new enum:

| Old value | New enum | Used by |
|-----------|----------|---------|
| `"normal"` (default) | `Urgency.INFORMATIONAL` | Default in `publish()` |
| `"high"` | `Urgency.URGENT` | `CostTracker.send_alert_if_needed()` |

---

## 5. DND State Management

**Module:** `core/notifications/dnd.py`

DND is user state — it belongs in the memory namespace.

**Redis key:** `alfred:memory:dnd`

```json
{"active": true, "until": "2026-03-20T15:00:00Z", "reason": "User requested", "source": "manual"}
```

### DND Sources (at launch)

1. **User-initiated** — User tells Alfred "do not disturb until 7am" or "hold my calls" via any channel. The Conscious Engine interprets the intent, writes DND state to Redis, and creates a time trigger for the expiry. Pure Pillar 5 composition — no dedicated DND tool.

2. **Calendar** — At notification time, query `apple_calendar.get_today_events()` and check if `now` falls within any event window. Cache result with 60s TTL to avoid hammering CalDAV.

### Future: Sleep-Based DND (Emergent)

Not implemented at launch. The Librarian (D3) will eventually detect patterns like "user stops responding at ~11pm, resumes at ~7am." That pattern becomes a learned routine in procedural memory. The Conscious Engine reads it and factors it into urgency decisions. Eventually crystallizes into a Reflex-level rule. Never hardcode what should be learned (Pillar 5).

### DND Checker

```python
class DNDChecker:
    def __init__(self, redis: AioRedis, calendar_adapter: AppleCalendarAdapter | None) -> None: ...

    async def is_active(self) -> DNDStatus:
        """Check manual override first, then calendar. First match wins."""
        ...

class DNDStatus(BaseModel):
    active: bool
    reason: str | None = None
    source: str | None = None  # "manual" | "calendar"
    until: datetime | None = None
```

### Deferred Notification Drain (No Polling)

When DND is set, an expiry mechanism fires when DND ends:

- **Manual DND:** Conscious Engine creates a time trigger at the expiry time. When it fires, it publishes an ActionRequest that calls `drain_deferred()`.
- **Calendar DND:** When the Dispatcher defers a notification due to a calendar meeting, it creates a one-shot time trigger for the meeting end time to drain deferred notifications (with an idempotency guard — skip if a drain trigger for that end time already exists).

No periodic checks. The trigger system is the callback mechanism.

---

## 6. Channel Adapters

**Module:** `core/notifications/channels.py`

### Base Class

```python
class ChannelAdapter(ABC):
    name: ClassVar[str]
    supported_urgencies: ClassVar[set[Urgency]]

    @abstractmethod
    async def deliver(self, notification: Notification) -> None: ...

    def supports_urgency(self, urgency: Urgency) -> bool:
        return urgency in self.supported_urgencies
```

### Auto-Discovery

`ChannelRegistry` discovers all `ChannelAdapter` subclasses via `@ChannelRegistry.register()` decorator — same pattern as `IntegrationRegistry`. No manual registration calls at startup.

```python
class ChannelRegistry:
    _registry: ClassVar[dict[str, type[ChannelAdapter]]] = {}

    @classmethod
    def register(cls) -> Callable:
        """Decorator. Registration happens at import time."""
        ...

    @classmethod
    def get_adapters_for_urgency(cls, urgency: Urgency) -> list[ChannelAdapter]:
        """Return all registered adapters that support the given urgency level."""
        ...
```

### Concrete Adapters

**SignalChannelAdapter**
- Wraps existing Signal Bridge `_send_signal()` logic
- Supports: informational, important, urgent
- Formats as `"{title}: {body}"`

**WebSocketChannelAdapter**
- Pushes notification JSON to all connected WebSocket sessions
- Supports: important, urgent
- If no sessions connected, silently skips (Signal is the reliable fallback)

**VoiceChannelAdapter**
- Uses PiperTTS to synthesize notification text to audio (ONNX Runtime with GPU acceleration — CUDA on prod, CoreML on dev)
- Sends audio bytes over WebSocket as `voice_notification` message
- Supports: urgent only
- If no web sessions open, silently skips

### Urgency → Channel Matrix

| Urgency | Signal | WebSocket | Voice |
|---------|--------|-----------|-------|
| informational | yes | - | - |
| important | yes | yes | - |
| urgent | yes | yes | yes |

---

## 7. Web Channel Changes

### New WebSocket Message Types

The web channel currently sends `response` type messages over WebSocket. Two new types are added:

```json
{"type": "notification", "title": "...", "body": "...", "urgency": "important"}
```

```json
{"type": "voice_notification", "audio": "<base64 PCM>", "title": "..."}
```

### Session Access

The `WebSocketChannelAdapter` and `VoiceChannelAdapter` need access to connected WebSocket sessions. The web server's session manager exposes a reference for adapters to push to all active sessions.

### No Offline Queue

If no web sessions are connected, WebSocket and Voice adapters silently skip. Signal always delivers — it's the reliable channel.

---

## 8. Integration Points

### Existing Code Changes

| Component | Change |
|-----------|--------|
| `NotificationPublisher` | Calls `Dispatcher.dispatch()` instead of direct `xadd` to stream |
| `CostTracker` | Update `urgency="high"` to `Urgency.URGENT`, drop `channel` param |
| `Trigger Engine` | No change — already publishes `ActionRequest` / `TriggerFired` |
| `Conscious Engine` | Sets urgency based on context before calling publish. Composes DND state + expiry triggers for user-initiated DND |
| `Signal Bridge` | Extracted send logic into `SignalChannelAdapter`. Bridge still handles inbound messages |
| `Web Server` | Exposes WebSocket session manager. Handles new message types on client side |

### New Redis Keys

| Key | Type | Purpose |
|-----|------|---------|
| `alfred:memory:dnd` | String (JSON) | DND state |
| `alfred:notifications:deferred` | List | Notifications queued during DND |

### New Redis Key Constants (in `shared/streams.py`)

```python
DND_STATE_KEY = "alfred:memory:dnd"
DEFERRED_NOTIFICATIONS_KEY = "alfred:notifications:deferred"
```

---

## 9. Pillar Alignment

| Pillar | How This Design Respects It |
|--------|-----------------------------|
| **1. Proactivity** | Notifications originate from triggers, integrations, and proactive engine suggestions. DND expiry uses time triggers, not polling. |
| **2. Decoupling** | Channel adapters are independent, auto-discovered. Adding a new channel = add a subclass. |
| **3. Deterministic Comms** | `Notification` is a Pydantic model. WebSocket messages are typed JSON. No natural language between components. |
| **4. Stateful Memory** | DND is user state in the memory namespace. Proactivity level read from semantic memory. |
| **5. Fluid Intelligence** | DND is composed from primitives (memory state + time triggers), not a purpose-built tool. Sleep-based DND deferred to emerge through Librarian pattern detection rather than hardcoded config. |

---

## 10. Testing Strategy

- **Unit tests:** Dispatcher routing logic, DND checker (manual + calendar), channel adapter delivery, urgency filtering
- **Integration tests:** End-to-end notification flow with Redis (publish → dispatch → channel delivery)
- **DND tests:** Manual DND set/expire, calendar meeting detection, deferred queue drain via trigger
- **Channel tests:** Signal adapter sends formatted message, WebSocket adapter pushes to connected sessions, Voice adapter synthesizes and pushes audio, all adapters gracefully skip when unavailable
