# Alfred Web App Rebuild — Design

**Date:** 2026-06-10
**Status:** Approved (brainstormed + validated with visual mockups)
**Replaces:** the vanilla JS app in `web/` (full replacement)

## Goal

One web app that is both Alfred's daily interface and its observatory: chat/voice front and center, with the entire system — event bus, memory, triggers, notifications, health — visible as a living, breathing whole. Built on shadcn/ui. Clean, ergonomic, no costume theming; the signature look is **Mission Control**: dark, monospace telemetry, color-coded sources.

## Decisions (validated with user)

| Question | Decision |
|---|---|
| App shape | One app, **chat-first** — chat is home; monitoring is back-of-house |
| Visual direction | **Mission Control** — dark, dense, monospace telemetry, multi-color status |
| Shell layout | **Cockpit** — chat center stage, slim always-live telemetry rail (collapsible) |
| Admin scope | Everything observable: live activity, memory browser, health, triggers + notifications |
| Admin powers | **Observe + key controls** (curated mutations, no free-form editing) |
| Replacement | Full — old `web/` deleted once parity confirmed |
| Platform | Desktop-first (iOS app covers mobile); responsive but not mobile-optimized |
| Architecture | **Vite SPA + extended FastAPI** — no Node at runtime |

## Context

The current app (`web/`, ~2K lines vanilla JS) covers login, onboarding, chat/voice, and integration settings. All user-facing backend APIs are solid and reused unchanged: WebAuthn auth (`/api/auth/*`), chat/voice WebSocket (`/ws`), integrations (`/api/integrations/*`), onboarding (`/api/onboarding`), device registration (`/api/devices/*`).

There is **no observability API**. All system state (Redis streams, memory layers, triggers, DND/deferred queue, cost, sessions, devices) lives in Redis/SQLite/files with no HTTP surface. This project therefore has two halves: a new frontend, and a new admin/telemetry API layer.

## Architecture

```
web/ (Vite + React + TS + shadcn)  ──build──▶  web/dist/ (static)
                                                    │ served by
FastAPI web channel (port 8081)  ◀──────────────────┘
 ├── existing: /api/auth, /ws, /api/integrations, /api/onboarding, /api/devices
 ├── NEW: /api/admin/*  (REST: reads + controls; cookie auth + trusted network)
 └── NEW: /ws/telemetry (WebSocket: live Redis stream fan-out; cookie gate, 4001)
                │
                ├── reads: Redis streams/hashes/keys, SQLite (episodic cold,
                │          credentials), memory files (Markdown/YAML)
                └── controls: direct Redis writes (shared state, e.g. DND)
                              or ACTIONS_STREAM publish (process-owned behavior)
```

### Frontend stack

- **Build:** Vite, React, TypeScript (strict). Output `web/dist/`, served by the existing FastAPI static mount (`html=True` SPA fallback). No Node.js in production.
- **UI:** shadcn/ui + Tailwind. Theme via shadcn CSS variables, dark-first.
- **Data:** TanStack Query for REST server state; thin WebSocket client layer (chat + telemetry) with auto-reconnect; small client store for connection/session state.
- **Routing:** React Router (8 routes).
- **Charts:** Recharts via shadcn chart primitives (cost gauge, events/min sparkline).
- **Type:** JetBrains Mono for telemetry/data; clean sans (Inter) for chat/body.

### Design system — Mission Control

- Dark-first palette on near-black blue (`#090c12` family) with hairline borders.
- **Source colors, used consistently everywhere** (rail, feeds, dashboards):
  reflex = cyan, conscious = green, memory = amber, triggers = pink,
  status good/warn/bad = green/amber/red.
- Aliveness is expressed by data motion (ticking feeds, fading entries, pulse dots), not decorative animation.
- Alfred's chat voice: no bubble — cyan left-border block; user messages are conventional bubbles. Each Alfred response shows its work in a small monospace line: tool calls, latency, cost.

## Frontend: shell + routes

**Shell (Cockpit):** left icon rail (nav + live pulse dots), main content, right telemetry rail (collapsible):
- **Vitals grid:** cost today vs cap, services up, events/min, DND state.
- **Live ticker:** real-time event lines via `/ws/telemetry`, color-coded by source, older entries fade. Click → `/activity` anchored at that event.
- **⌘K command palette** (shadcn Command): navigate to any page, fire any control (DND on/off, drain deferred, run Librarian, end session).
- Top bar: connection truth (`ONLINE` / `RECONNECTING` / `OFFLINE`), session id + age.

| Route | Content |
|---|---|
| `/` | Cockpit: chat + voice. Existing `/ws` protocol unchanged — text/audio messages, transcription display, TTS playback, session restore, urgency-coded notification toasts (URGENT plays audio). Voice record button with live level indicator. |
| `/activity` | Full live feed of all streams (events, user requests/responses, reflex observations, actions, notifications dispatch). Filter by source/stream, pause/resume, search; click any event → full JSON inspector (pretty-printed Pydantic payload). History backfill via REST pagination. |
| `/memory` | Tabs: **Episodic** (semantic search across hot Redis + cold SQLite; significance scores; hot/cold badge), **Semantic** (preference/profile Markdown rendered), **Routines** (procedural YAML with lifecycle state), **Scratchpad** (today's observations). |
| `/triggers` | Active triggers: condition, state, last fired; per-trigger **fire now** / **disable** controls. Notification history, deferred queue (with **drain**), DND state + toggle. |
| `/health` | Service status, Redis/Ollama/LM Studio connectivity, integration health checks, cost gauge vs cap, active sessions (with **end session**), registered devices. Each panel loads and fails independently. |
| `/settings` | Integration credentials (existing API: save/test/clear), registered devices, logout. |
| `/login` | WebAuthn passkey login with Conditional UI. |
| `/onboarding` | 6-step wizard rebuilt in shadcn: passkey registration, personal preferences, proactivity level, guest mode, integrations, done. Existing endpoints. |

## Backend: new surface

### Admin REST (`core/channels/admin_api.py`, mounted at `/api/admin/*`)

All endpoints require auth cookie **and** trusted network (same dependency as credential endpoints).

**Reads:**

| Endpoint | Returns |
|---|---|
| `GET /overview` | Snapshot for rail vitals + health page: services, Redis/Ollama/LM Studio reachability, integration health, cost today vs cap, session/device counts, DND state |
| `GET /streams` | Known streams (from `shared/streams.py`) with lengths |
| `GET /streams/{name}?count=&before=` | Paginated history (XREVRANGE) of any known stream |
| `GET /memory/episodic?q=&limit=` | Search hot + cold episodic with significance scores |
| `GET /memory/semantic` | Preference/profile Markdown files + content |
| `GET /memory/routines` | Procedural routines + lifecycle state |
| `GET /memory/scratchpad` | Current scratchpad content |
| `GET /triggers` | Active trigger registry |
| `GET /notifications/deferred` | Deferred notification queue |
| `GET /sessions` | Active conscious sessions + WS connections |
| `GET /devices` | Registered push devices |

**Controls** — each maps to an operation the system already performs; no new behavior:

| Endpoint | Mechanism |
|---|---|
| `POST /dnd` (set/clear) | Direct Redis write (shared DND hash) |
| `POST /notifications/drain` | Publish `drain_deferred_notifications` to `ACTIONS_STREAM` (existing consumer) |
| `POST /triggers/{id}/fire` | Publish to `ACTIONS_STREAM` |
| `POST /triggers/{id}/disable` | Publish to `ACTIONS_STREAM` |
| `POST /librarian/run` | Publish to `ACTIONS_STREAM` (conscious process owns Librarian) |
| `DELETE /sessions/{id}` | Direct Redis session delete |

Rule of thumb: shared Redis state → direct write; process-owned behavior → `ACTIONS_STREAM`. New action types added to the existing actions consumer where missing.

### Telemetry WebSocket (`/ws/telemetry`)

- Same cookie auth gate as `/ws`; unauthenticated → close 4001.
- Client: `{"type": "subscribe", "streams": ["events", "reflex_observations", ...]}` and `unsubscribe`. Stream names validated against `shared/streams.py`.
- Server: per-connection blocking `XREAD` task on subscribed streams starting at `$`; each new entry pushed immediately as `{"type": "entry", "stream": ..., "id": ..., "event": {...}}`. Event-driven end to end — **no client polling**.
- Cockpit rail subscribes broadly; pages subscribe to what they display.
- Redis hiccups: server sends a status frame, retries with backoff; client surfaces state in top bar.

## Error handling

- Both WebSockets auto-reconnect with exponential backoff; top-bar status reflects reality. On reconnect: chat restores session (existing protocol), telemetry re-subscribes.
- REST 401 / WS 4001 → route to `/login`.
- Dashboard panels fail independently — inline error state per card, never a blank page.
- Admin reads are defensive: missing streams/files → empty results, not 500s. Controls return explicit success/failure surfaced as toasts.
- Voice: mic-permission and STT failures produce clear in-chat error states.

## Testing

- **Backend (pytest, existing mocked-infra patterns):** admin endpoint coverage including auth gating (401, 403 off-network, 4001); telemetry subscribe/fan-out; controls assert exact `ACTIONS_STREAM` payloads / Redis writes.
- **Frontend:** `tsc --strict`, ESLint, Vitest + React Testing Library for load-bearing components: message rendering (work-shown line), feed reducer, command palette actions, reconnect logic.
- **Manual QA:** mic capture, passkey flows, real multi-process telemetry → `docs/qa-backlog/` entries per convention.

## Build, dev, migration

- **Dev:** `npm run dev` — Vite HMR, proxying `/api` and `/ws` to FastAPI :8081. Python runner untouched.
- **Prod:** `npm run build` → `web/dist/`; `web_server.py` static mount points at `web/dist/`. Containerfile gains a Node build stage that bakes `dist/` into the image (fixes the existing gap where the frontend isn't shipped in containers).
- **Migration:** new app replaces `web/` wholesale; old vanilla files deleted in the same change once parity is confirmed. `.gitignore` gains `node_modules/`, `web/dist/`.

## Out of scope (backlog candidates)

- Mobile-optimized layouts (iOS app covers mobile)
- Editing memory/trigger files from the UI ("full admin")
- Historical cost analytics beyond the 48h Redis key
- OTel trace visualization (SigNoz exists for this)
- Streaming TTS / frontend audio queue (D23), client geolocation (D24)
