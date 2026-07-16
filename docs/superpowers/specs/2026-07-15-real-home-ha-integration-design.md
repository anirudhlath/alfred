# Real-Home HA Integration — Token-to-Live Onboarding & Proactive State Ingest

**Date:** 2026-07-15
**Status:** Approved
**Repos:** `alfred` (sdk, core, bus), `home-service`

## Vision

Enter a Home Assistant URL + long-lived token in Alfred's web UI, and Alfred
initializes everything itself: connects to the real apartment HA
(`http://192.168.50.159:8123`), discovers every entity/device/area, generates
its full control surface, and stays proactive to live state changes. No manual
`.env` edits, no HA-side automation setup, no hand-written per-domain features.

## Current State (why this is needed)

- home-service reads `HA_HOST`/`HA_TOKEN` from `.env` only; entity IDs are
  guessed from room names (`light.living_room`) instead of discovered; only
  lights + scenes are exposed (backlog D19).
- Live state ingest depends on an HA-side automation (in the dev
  `home-assistant` repo) publishing to MQTT `home/state_changed`. The real
  apartment HA has no such automation, so Reflex would be blind.
- Event-conditional (sensor) triggers historically never fired; PR #22 (merged)
  fixed this — the trigger engine now consumes `alfred:home:state_changed`
  with its own consumer group. The fix is load-bearing for this design but
  requires no further work.
- The D25 secrets manager (CredentialSchema → keyring → settings UI) covers
  only in-core `IntegrationRegistry` adapters, not sovereign services.

## Decisions (from brainstorm)

1. **Scope:** full control surface — every controllable domain HA reports.
2. **Observation:** two-tier. Tier 1: observe everything (context, triggers).
   Tier 2: only an auto-seeded, runtime-adjustable **attention set** fires the
   Reflex SLM.
3. **Autonomy:** tiered. Reflex → benign domains only. Conscious → everything,
   but `critical` tools additionally require user confirmation — even for
   direct user commands (v1; relaxing later is easy, the reverse is not).
4. **Architecture:** generalize the platform (Approach A). No HA special cases
   in core; sovereign services declare credential needs via the SDK; the
   capability surface is generated from HA's own registries.

## Non-Goals

- Refreshing eval scenarios to real-apartment entities (follow-up after go-live).
- Channel rate limiting, streaming TTS, and other production-hardening backlog.
- Wiring signal-bridge into the credential flow (it inherits the mechanism;
  its adoption is a separate ticket).
- Multi-home / multi-HA-instance support.

---

## Section 1 — Credential Flow for Sovereign Services

### SDK manifest extension (`alfred/sdk`)

- Service manifest gains two optional fields:
  - `credentials_schema: CredentialSchema | None` — the SDK defines its own
    copy of the small `CredentialSchema`/`CredentialField` Pydantic models
    (field shape identical to `core/integrations/base.py`; the JSON contract
    is the coupling, per Pillar 3 — the SDK must not import core).
  - `credentials_endpoint: str | None` — absolute URL core POSTs credentials
    to (e.g. `http://localhost:8000/credentials`).
- `AlfredClient.register()` publishes a `ServiceRegistered` event to
  `alfred:events` (`EVENTS_STREAM`) after writing the tool registry — a new
  bus event type in `bus/schemas/events.py`. The SDK already holds a Redis
  connection for registration, so no new coupling is introduced.

home-service declares:

| field | type | notes |
|-------|------|-------|
| `url` | `url` | default `http://homeassistant.local:8123`; user sets `http://192.168.50.159:8123` |
| `token` | `password` | HA long-lived access token |

### Core: merged integrations API (`core/channels/web_server.py`)

- `GET /api/integrations` additionally reads `alfred:tool_registry` and
  includes any registered service that declares a `credentials_schema`.
  External entries are marked (e.g. `"kind": "service"`) but use the same
  schema shape, so the existing schema-driven `IntegrationCard` in the SPA
  renders them with zero frontend special-casing. The HA card therefore
  appears in Settings and the onboarding integrations step automatically.
- `PUT /api/integrations/{name}/credentials` for external services: validate
  against the registry-declared schema → store fields in the OS keyring under
  the service name (core remains the single credential authority; secrets
  never touch Redis or non-keyring disk) → POST the fields to the service's
  `credentials_endpoint` over the trusted network (localhost/Tailscale gate,
  same as today's credential endpoints).
- `GET /api/integrations/{name}/status` for external services proxies to the
  service's `/health`.

### Self-healing re-push (event-driven, no polling)

- The channels process consumes `ServiceRegistered` events from
  `EVENTS_STREAM` with its own consumer group. On receipt, if the keyring
  holds credentials for that service, POST them to its `credentials_endpoint`.
- home-service keeps credentials in memory only. On restart it registers →
  `ServiceRegistered` → core re-pushes → home-service reconnects to HA within
  ~1s. The same machinery serves any future sovereign service.

### home-service credential endpoint

- `POST /credentials` (trusted network only): accepts `{url, token}`, applies
  them live (connect/reconnect `HAConnection`), returns resulting health so
  the settings card can immediately show connected-or-failed.
- `.env` `HA_HOST`/`HA_TOKEN` remain as a dev fallback if no pushed
  credentials exist, but the UI flow is authoritative.

---

## Section 2 — home-service: Discovery, Generated Capabilities, State Ingest

### HAConnection (WebSocket-first)

- Persistent connection to HA WebSocket API (`/api/websocket`): token auth,
  `subscribe_events(state_changed)`, subscriptions to registry-updated events,
  fetches of entity/device/area registries and the service catalog
  (`get_services`). Service calls execute over the same socket
  (`call_service`).
- Reconnect with exponential backoff; on reconnect: resubscribe, refresh
  registries, re-register context.
- The existing httpx REST client remains only as a thin fallback for
  `/api/states` snapshots.
- `/health` reports real state: `connected | auth_failed | unreachable |
  degraded`, plus entity/area counts and seconds since last state event.

### EntityIndex

- Built from HA registries: `entity_id → {friendly_name, domain,
  device_class, area, device}`.
- All tool execution resolves areas / friendly names → real entity IDs through
  the index. The `to_entity_id()` name-guessing convention is deleted.
- Rebuilt automatically on registry-updated events (renames/additions in HA
  are picked up live, no restart).

### CapabilityGenerator (no hand-written domain features)

- Crosses the HA service catalog with the EntityIndex to generate the tool
  surface. Registered tool manifests gain two fields:
  - `audience: "reflex" | "conscious"`
  - `risk: "benign" | "elevated" | "critical"`
- **Reflex tier:** compact high-level tools for benign domains (lights,
  switches, media players, scenes) with live area/entity values injected into
  parameter descriptions (existing pattern) — keeps the SLM prompt small.
- **Conscious tier:** everything above plus generated tools for remaining
  domains (climate, covers, locks, vacuums, scripts, …) and a generic
  `call_service` effector as the escape hatch.
- **Risk mapping is data, not code:** YAML default shipped with home-service
  mapping domain/device_class → risk (`lock`, `alarm_control_panel`,
  `cover` with garage device_class → `critical`; `script`, `automation`,
  `climate` → `elevated`; rest `benign`), overridable without code changes.
- `/mcp` JSON-RPC dispatch is unchanged from `HomeAgent`'s perspective.

### State forwarder

- Every `state_changed` WebSocket event becomes a bus-schema
  `StateChangedEvent` published to MQTT `home/state_changed`. The existing
  bridge → `alfred:home:state_changed` → Reflex pipeline is untouched. The
  HA-side automation requirement is retired.
- ALL events forward (Tier 1: triggers and context need full visibility);
  SLM gating happens in core (Section 3).
- Context snapshots update live from the event flow instead of only on the
  5-minute re-registration cycle.
- Dev parity: the dev/template HA speaks the same WebSocket API, so dev and
  the real apartment exercise identical code paths.

---

## Section 3 — Core: Attention, Trigger Fix, Autonomy, Confirmations

### Attention set (Reflex SLM gating)

- Redis key `alfred:attention:home` holds entity IDs Reflex reacts to.
- **Lazy, data-driven seeding:** when Reflex first sees an entity, a YAML seed
  rule (domains + device classes: lights, media players, doors / motion /
  occupancy binary_sensors, climate, locks, presence) decides membership. No
  bulk setup step.
- **Runtime-adjustable primitive:** internal Conscious tools `attention_add` /
  `attention_remove` / `attention_list` (same in-process pattern as memory
  tools); the Librarian may promote/demote entities during consolidation.
- Reflex fires only on real state transitions (`new_state != old_state`, not
  attribute-only updates) with a per-entity cooldown (~5s) to collapse bursts.
- Triggers and context ignore the attention set — full visibility.

### Sensor triggers (already fixed — dependency, not work)

PR #22 (merged 2026-07) made the trigger engine consume
`alfred:home:state_changed` with its own consumer group. Once the state
forwarder (Section 2) feeds that stream from the real HA, sensor/composite
triggers evaluate against any entity — no attention gating, no SLM cost
("tell me when the dryer finishes" on a chatty power sensor). This design
adds no trigger-engine changes; e2e verification of sensor triggers against
live HA data joins the QA backlog.

### Tiered autonomy — enforced twice

1. **Prompt layer:** Reflex builds its tool prompt only from registry tools
   tagged `audience: "reflex"`.
2. **Dispatch layer (defense in depth):** the dispatch path
   (DomainRouter/HomeAgent) checks the registry; an `ActionRequest` from
   Reflex targeting a tool with risk above `benign` is rejected, logged, and
   recorded as a `ReflexObservation` — a hallucinated tool name cannot
   actuate a lock.

### Confirmation flow for critical actions

- Any `ActionRequest` targeting a `risk: "critical"` tool is intercepted at
  dispatch: the full request is stored at `alfred:pending_actions:{id}`
  (TTL 5 min) and an URGENT notification goes out via the existing
  NotificationDispatcher ("Alfred wants to unlock the front door — confirm?").
- Confirmation paths:
  - Web UI: notification renders a confirm button →
    `POST /api/actions/{id}/confirm` (auth-gated).
  - Any chat channel: Conscious gets an internal `confirm_pending_action`
    tool, so "yes, go ahead" works over Signal/iOS/web chat.
- On confirm, the stored `ActionRequest` is republished to `alfred:actions`
  (`ACTIONS_STREAM`) carrying a confirmation marker; the dispatch path
  executes marked requests without re-interception. On expiry → silent
  expiration with a log entry.
- **v1 rule:** critical actions require confirmation even when directly
  user-initiated. The LLM never self-certifies "the user told me to."

---

## Section 4 — Resilience, Testing, Rollout

### Resilience & error handling

- home-service: backoff reconnect → resubscribe → registry refresh → context
  re-registration; `/health` exposes degraded states rather than a hollow
  "ok"; MQTT publish failures log at WARNING with bounded buffering (no
  unbounded queues).
- Core: failed credential push surfaces on the settings card and retries on
  the next `ServiceRegistered` event; invalid token shows "auth failed."
- `alfred:home:state_changed` gets an XADD `maxlen` cap so a chatty apartment
  cannot grow Redis unboundedly.

### Testing

- home-service: fake HA WebSocket server fixture (auth handshake, registries,
  state events, service calls); unit tests for EntityIndex resolution,
  CapabilityGenerator output (audience/risk tagging), state forwarder mapping,
  credential endpoint.
- alfred: unit tests for manifest schema fields, merged integrations API,
  ServiceRegistered re-push consumer, attention seeding/gating/cooldown,
  dispatch-layer enforcement, confirmation flow (pending → confirm →
  execute; pending → expire).
- E2E: dev/template HA over the same WebSocket path; live smoke script for the
  apartment (discovery + one reversible light toggle).
- Manual verification items → `docs/qa-backlog/` per convention.

### Rollout

1. Land everything working against the dev HA (identical code path).
2. Go live: Settings → HA card → enter `http://192.168.50.159:8123` + token
   minted from the HA profile page → save. Done.
3. Follow-up (out of scope here): refresh eval fixtures via
   `python -m evals capture-context` against the real home.

### Documentation & backlog effects

- Update `docs/home-service.md` (or create), `docs/architecture.md` diagrams,
  and both repos' CLAUDE.md operational notes.
- Closes: `docs/backlog/low/d19-context-provider-option-c-entities.md`.
- Depends on: sensor-trigger stream fix (PR #22, already merged).
- Retires: HA-side MQTT state automation (dev `home-assistant` repo keeps
  working but is no longer load-bearing for ingest).
