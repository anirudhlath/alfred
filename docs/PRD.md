# Alfred — Product Requirements Document

**Status:** Living document. Capability statuses current as of **2026-07-17**.
**Maintenance rule:** any PR that adds or changes a user-facing capability updates the
relevant row(s) in the [Capability Catalog](#4-capability-catalog) in the same branch.

---

## 1. What is Alfred

Alfred is an ambient, voice-first butler for your home. It runs on your own hardware,
watches the environment you tell it to watch, acts before being asked, and remembers
what matters to you — while treating the cloud as a hired specialist, not a landlord.

Two minds share the work. A fast local reflex (a small model on your own GPU) handles
routine reactions in under half a second: lights when you sit down to a movie, a nudge
when the front door stays open. A slower conscious mind (a frontier cloud model) handles
conversation, planning, and anything requiring judgment — and only that, to keep costs
and data exposure bounded. Between them sits a biologically inspired memory that records
episodes, distills preferences, and crystallizes repeated patterns into routines the
reflex can execute without thinking.

Alfred is not an app you open. It is a presence in the home: reachable by voice, web,
iOS, or Signal, and increasingly capable of reaching you first when something deserves
your attention.

## 2. Who it's for

- **The resident** — the primary user. Talks to Alfred naturally on any channel, gets
  proactive help grounded in their real routines, and controls their home without
  writing a single automation rule.
- **Household guests** — bounded access. Guests can use everyday controls (lights,
  media) without gaining the resident's identity, memory, or risky capabilities.
- **The self-hoster / contributor** — operates their own instance. Everything is
  open source (AGPL), runs on commodity hardware plus one GPU, and extends through a
  documented SDK rather than forks.

## 3. Product principles

These are promises, not aspirations. A change that breaks one of these is a bug.

1. **Proactive, not intrusive.** Alfred acts on real signals and learned routines. It
   respects do-not-disturb, defers what can wait, and asks permission for anything
   consequential. Suggestions that keep getting dismissed stop coming.
2. **Local-first and private.** Sensor data, voice audio, memory, and credentials stay
   on hardware you own. The cloud sees only what conscious reasoning strictly needs.
   Credentials live in the OS keychain — never in config files, never on the message bus.
3. **Everything describes itself.** Devices, services, and tools register what they are
   and what they need; Alfred renders forms, builds prompts, and routes actions from
   those descriptions. Adding a capability never means editing Alfred's core.
4. **Deterministic and auditable.** Every message between components is a typed,
   validated schema. No component whispers natural language to another. What Alfred did,
   and why, is inspectable after the fact.
5. **Learns routines; doesn't demand programming.** The resident never writes an
   automation. Alfred's conscious mind composes behavior from primitives, the librarian
   promotes repeated patterns to routines, and the reflex executes them fast.
6. **Sovereign services.** External integrations are standalone applications that work
   without Alfred and connect through one SDK. Alfred failing never breaks your home;
   a service failing never breaks Alfred.

## 4. Capability Catalog

Legend: **Shipped** (on master, tested) · **In review** (built, PR open) ·
**In progress** (spec + plan exist, being built) · **Planned** (spec'd, not started).

### 4.1 Conversation & channels

> *"Alfred, did anything need my attention while I was out?" — asked from the couch,
> from a phone on the train, or over Signal from another country. Same butler, same memory.*

| Capability | Status | Reference |
|---|---|---|
| Web app (Mission Control SPA: chat, notifications, triggers, memory, system health, settings) | Shipped | `docs/web-frontend.md` |
| Native iOS app (chat, notifications, settings, Face ID, push) | Shipped | `alfred-ios` repo |
| Signal messaging (inbound requests + outbound notifications) | Shipped | `docs/architecture.md` |
| Voice in the browser/app (speech-to-text, spoken replies) | Shipped | `docs/architecture.md` |
| Physical wake-word satellites ("Hey Alfred" devices per room, room-aware context) | In review | spec `2026-07-15-voice-satellite-design.md`, PR #29 |
| Speaker identification (who is talking, voice enrollment) | In review | PR #29 |
| Multi-step conversations with session continuity (30-min sessions, any channel) | Shipped | `docs/architecture.md` |

### 4.2 Proactivity & triggers

> *You mention a package arriving tomorrow. Without being asked, Alfred sets a reminder,
> watches the door sensor, and tells you when it's there — then forgets the trigger.*

| Capability | Status | Reference |
|---|---|---|
| Dynamic triggers created by conversation (time, schedule, sensor, composite) | Shipped | `docs/trigger-engine.md` |
| Sensor-driven triggers on live home state (verified end-to-end) | Shipped | PR #22 |
| Sub-5-second reminder firing (scheduled wakeups replacing polling) | In review | PR #27 |
| Client-timezone awareness (reminders in your timezone, wherever you are) | In review | PR #27 |
| Proactive notifications with urgency levels, DND windows, deferred delivery | Shipped | `docs/notifications.md` |
| Delivery to Signal, web (with spoken announcement when urgent), and iOS push | Shipped | `docs/notifications.md` |
| Reflex "attention set" — Alfred tunes which entities wake the fast mind, and can retune itself | Planned | spec `2026-07-15-real-home-ha-integration-design.md` (Plan 3) |

### 4.3 Memory

> *Weeks after you mentioned preferring dim evenings, Alfred still dims the evening
> lights — and can tell you why: it remembers the conversation that taught it.*

| Capability | Status | Reference |
|---|---|---|
| Episodic memory: significant events recorded with hot (fast) and cold (archival) tiers | Shipped | spec `2026-03-24-phase3-memory-completion-design.md` |
| Semantic memory: preferences and profile as human-readable documents | Shipped | same |
| Procedural memory: routines with a full lifecycle (detected → confirmed → decayed) | Shipped | same |
| Nightly librarian consolidation: conflict resolution, pattern detection, contextual decay | Shipped | same |
| Two-stage recall: automatic context assembly + deliberate memory search during reasoning | Shipped | same |
| Significance scoring (a heuristic amygdala deciding what is worth remembering) | Shipped | same |
| System 2 observation of System 1 (the conscious mind learns from reflex actions) | Shipped | spec `2026-04-16-d8-system2-observation-design.md` |

### 4.4 Smart home

> *Enter your Home Assistant address and a token in Settings — Alfred discovers every
> room and device, streams live changes, and your home becomes something you talk to.*

| Capability | Status | Reference |
|---|---|---|
| Lights and scenes control via the home-service companion app | Shipped | `alfred-home-service` repo |
| Token-to-live Home Assistant onboarding (credentials via UI, zero config files) | In review | PR #28 ships the mechanism; the HA card itself lands with Plan 2 |
| Full-home device discovery from HA's own registries (rooms, devices, friendly names) | Planned | HA integration spec, Plan 2 |
| Generated control surface for every controllable domain (climate, covers, locks, media…) | Planned | same |
| Live state streaming without HA-side setup (WebSocket ingest) | Planned | same |
| Tiered autonomy: reflex touches only benign devices; risky domains need the conscious mind | Planned | same, Plan 3 |
| Confirmation required for critical actions (locks, alarm, garage) — even when you asked | Planned | same, Plan 3 |

### 4.5 Integrations & credentials

> *A new service starts up and introduces itself; a credential card appears in Settings.
> Enter the key once — Alfred delivers it, checks health, and re-delivers after restarts.*

| Capability | Status | Reference |
|---|---|---|
| Weather, Apple Calendar (real CalDAV), Apple Health, Robinhood adapters | Shipped | `docs/secrets.md` |
| Secure credential storage (OS keychain) with schema-driven settings forms | Shipped | spec `2026-03-22-secrets-manager-design.md` |
| Sovereign services declare credential needs; Alfred renders, stores, pushes, re-pushes | In review | PR #28 |
| Calendar event creation/management from conversation | Shipped | `docs/secrets.md` |

### 4.6 Security & privacy

> *Your partner's phone can turn on the lights. It cannot unlock the door, read your
> memory, or impersonate you — and registering a new device takes a fingerprint, not a password.*

| Capability | Status | Reference |
|---|---|---|
| Passkey (WebAuthn) login: biometric sign-in, no passwords stored | Shipped | `docs/webauthn.md` |
| Trusted-network gating for sensitive operations (localhost + Tailscale only) | Shipped | `docs/webauthn.md` |
| Identity confidence levels per channel (Signal-verified vs local claim) | Shipped | `docs/architecture.md` |
| Guest access choices captured at onboarding (which controls guests may use) | Shipped | onboarding wizard |
| Guest boundary enforcement via tiered autonomy | Planned | HA integration spec, Plan 3 |
| Daily cloud-spend cap with alerting before the ceiling | Shipped | `docs/architecture.md` |

### 4.7 Operations & quality

> *One command starts the whole staff; a dashboard shows every stream, service, and
> recent decision; and an eval suite scores the butler's judgment before each change ships.*

| Capability | Status | Reference |
|---|---|---|
| Unified runner: one process supervises all services, restarts crashes, hot-reloads code | Shipped | `docs/architecture.md` |
| Admin dashboard: live telemetry, stream inspection, trigger management | Shipped | `docs/admin-api.md` |
| Eval harness: regression + live modes, custom judgment metrics, run comparison | Shipped | `docs/evals-runner.md` |
| Model warmup at startup (no cold-start latency on first request) | Shipped | spec `2026-04-16-startup-warmup-design.md` |
| Self-describing health for external services surfaced in Settings | In review | PR #28 |

## 5. What Alfred is not

- **Not a cloud service.** There is no hosted Alfred, no account server, no telemetry
  phoning home. You run it; you own it.
- **Not multi-tenant.** One instance serves one household. Guests are bounded users of
  your instance, not tenants.
- **Not an Alexa/Google Home clone.** No skill store, no far-field speaker ecosystem
  ambitions; satellites are cheap open hardware. Alfred competes on judgment and memory,
  not on playing music in every room (though it can).
- **Not a rules engine.** If a capability requires the resident to write IF-THEN
  automations, it has failed this document. Home Assistant remains the device layer;
  Alfred is the judgment layer above it.

## 6. Success criteria

| Dimension | Bar |
|---|---|
| Reflex latency | Event → action in **< 500 ms** on local hardware |
| Reminder latency | Trigger fire → notification in **< 5 s** |
| Onboarding | A new service or the whole home connects with **one token in the UI** — zero config-file edits |
| Proactivity quality | Suggestions accepted more often than dismissed (tracked via routine lifecycle; dismissed suggestions decay away) |
| Cost | Normal daily operation stays under the configured cloud budget; the resident hears about it *before* the cap bites |
| Memory quality | Eval-suite judgment metrics do not regress release-over-release |
| Sovereignty | Killing Alfred leaves the home functional; killing any service leaves Alfred functional |

## 7. Roadmap

**Near** — land what's in review (instant reminders #27, credential flow #28, voice
satellites #29); then the Home Assistant integration Plans 2–3: full-home discovery and
live state, followed by the attention set, tiered autonomy, and critical-action
confirmations. Outcome: the real apartment becomes Alfred's body.

**Mid** — the satellite hardware line (Raspberry Pi provisioning, custom "hey Alfred"
wake word, per-room presence); guest enforcement throughout; routine-aware contextual
actions and deviation detection (the librarian noticing you're *not* doing your usual).

**Horizon** — from the expanded-vision spec: crystallized autonomous execution (learned
routines promoted to fully autonomous behavior with consent), richer channel continuity
(start a conversation by voice, finish it on the phone), and additional sovereign
services built on the SDK by whoever shows up — that's what the credential flow, the
SDK, and this document are for.

## 8. Where the depth lives

| Want | Read |
|---|---|
| System architecture & diagrams | `docs/architecture.md` |
| Event bus & schemas | `docs/event-bus.md` |
| Building a service on the SDK | `docs/sdk.md` |
| Credentials & secrets | `docs/secrets.md` |
| Triggers | `docs/trigger-engine.md`, `docs/notifications.md` |
| Original & expanded vision | `docs/superpowers/specs/2026-03-10-project-alfred-design.md`, `…2026-03-19-alfred-expanded-vision-design.md` |
| Every feature's design history | `docs/superpowers/specs/` |
| Deferred work | `docs/backlog/` (priority tiers) |
