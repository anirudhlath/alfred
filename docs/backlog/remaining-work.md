# Remaining Work — Ground-Truth Audit (2026-03-20)

Consolidated from 6-agent parallel audit of master branch. Replaces all prior backlog files.
Prior files (`context-provider.md`, `evals-runner.md`, `phase3-review.md`, `phase4-deferred.md`,
`trigger-engine-simplification.md`) are superseded by this document.

---

## Tier 1: Critical (System doesn't fully work without these)

### ~~C1: Librarian Never Scheduled~~ — DONE (Phase 5)
Periodic scheduler wired into conscious process (1hr default, `LIBRARIAN_INTERVAL_SECONDS` env var).

### ~~C2: Signal Bridge `_send_signal()` is a Stub~~ — DONE (Phase 5)
Implemented `asyncio.create_subprocess_exec("signal-cli", ...)` with error handling.

### ~~C3: Episodic Stream Unbounded Growth~~ — DONE (Phase 5)
Added `maxlen=10000, approximate=True` to `EpisodicStore.write()` xadd call.

### ~~C4: Librarian N+1 LLM Calls~~ — DONE (Phase 5)
Batched entity extraction into single LLM call via `_extract_entities_batch()`.

---

## Tier 2: Important (Should fix before production)

### ~~I1: AioRedis Type Alias in Wrong Place~~ — DONE (Phase 5)
Moved to `shared/types.py`, all 16+ consumers updated.

### ~~I2: MemoryReader No File Caching~~ — DONE (Phase 5)
Added 60s TTL caching to `core/memory/reader.py`.

### ~~I3: max_tokens Hardcoded to 2048~~ — DONE (Phase 5)
Configurable via `CLAUDE_MAX_TOKENS` env var in `AlfredConfig`.

### ~~I4: Identity "sir" Magic String~~ — DONE (Phase 5)
Extracted to `IDENTITY_SIR` / `IDENTITY_GUEST` constants.

### ~~I5: Librarian Deferred litellm Imports~~ — DONE (Phase 5)
Module-level `json`, structured deferred `litellm` imports.

### ~~I6: Onboarding Test Captures Files But Never Asserts Contents~~ — DONE (Phase 5)
Added YAML frontmatter parsing assertions for personal.md and proactivity.md.

### ~~I7: Reflex/Conscious Dual MemoryReader~~ — DONE (Phase 5)
Consolidated to single `core/memory/reader.py`, deleted both old implementations.

### ~~I8: RoutineStore Globs+Parses YAML Every Call~~ — DONE (Phase 5)
Added in-memory cache with invalidation on save/delete.

---

## ~~Tier 3: Low Priority~~ — ALL DONE (Phase 6)

### ~~L1: Sensor Triggers Evaluated on Tick~~ — DONE
Added `responds_to_tick: ClassVar[bool]` to `BaseTrigger`, set `False` on `SensorTrigger`, filtered in `_evaluate_all`.

### ~~L2: Shared Stream Entry Parsing Duplicated~~ — DONE
Extracted `decode_stream_value()` to `shared/streams.py`, updated 4 consumers.

### ~~L3: TriggerFeature `_store = None # type: ignore`~~ — DONE
Replaced with `Optional[TriggerStore]` and `_store_or_raise` guard property.

### ~~L4: STT/TTS Lazy-Loader Boilerplate Duplicated~~ — DONE
Extracted `_lazy_load()` factory in `web_server.py`.

### ~~L5: Engine Constructor 16 Parameters~~ — DONE
Introduced `ConsciousConfig` + `ConsciousDeps` dataclasses with backward-compatible kwargs.

---

## Tier 4: Deferred Features (Need own phase/plan)

### Security & Authentication

| ID | Feature | Spec | Notes |
|----|---------|------|-------|
| D1 | WebAuthn registration + login | Security | Prod-blocking. navigator.credentials API + server endpoints + credential store |
| D2 | Voice enrollment (SpeechBrain) | Security | Depends on D1. Audio samples + model training + voiceprint storage |

### Intelligence & Memory

| ID | Feature | Spec Section | Notes |
|----|---------|-------------|-------|
| D3 | Librarian pattern detection → procedural memory | Section 4 | Needs 2+ weeks of episodic data. consolidator.py:268-270 is TODO |
| D4 | Librarian decay processing | Section 4 | `_apply_decay()` returns 0. Need XTRIM + archival |
| D5 | Episodic hot-storage search | Section 4 | Only cold SQLite search exists. No Redis Stream search |
| D6 | Semantic memory conflict resolution | Section 4 | Atomic overwrite only. No merge/conflict detection |
| D7 | Procedural memory promotion + hit rate | Section 4 | No state transitions (candidate→active→archived). No hit tracking |
| D8 | System 2 observation of System 1 actions | Section 2 | No observation path from Reflex → Conscious |

### Notifications & Channels

| ID | Feature | Spec Section | Notes |
|----|---------|-------------|-------|
| ~~D9~~ | ~~Proactive notification dispatch + DND + priority routing~~ | ~~Section 8~~ | DONE — NotificationDispatcher with DND awareness, priority routing, 3 channel adapters, drain triggers |
| D10 | Channel rate limiting | Section 15 | No middleware, no per-user limits |
| D21 | Indefinite DND drain via keyspace notification | Section 8 | When DND has no `until`, deferred queue strands until next expiry-based drain or restart. Use Redis keyspace notifications on DND_STATE_KEY deletion to trigger immediate drain |
| ~~D22~~ | ~~TriggerFired → user notification bridge~~ | ~~Section 1+8~~ | DONE — TriggerFired events consumed by Reflex process: immediate DND-aware notification + SLM reasoning for additional actions. Urgency field on BaseTrigger and TriggerFired |
| D23 | Frontend audio queue | Section 6+8 | Response TTS and notification TTS play simultaneously. Need a sequential audio queue so notifications wait for current playback to finish |
| D26 | Duplicate WebSocket notification delivery | Section 8 | Both `conscious-delivery` and `channels-delivery` consumer groups deliver via WebSocket adapter → notifications and TTS fire twice. Fix: WebSocket+Voice adapters should only be in channels process; conscious process should only have Signal adapter |
| D27 | Browser push notifications (Web Notifications API) | Section 8 | Notifications only appear in-chat via WebSocket. Add `Notification.requestPermission()` + `new Notification()` in frontend so triggers surface as native OS notifications even when the tab is in background |
| D11 | Streaming TTS to WebSocket | Section 6 | Full blob only, no chunk streaming |

### Infrastructure & Security

| ID | Feature | Spec Section | Notes |
|----|---------|-------------|-------|
| D24 | Client-side geolocation for weather | Section 9 | Weather integration uses hardcoded lat/long from .env. Should use browser Geolocation API, pass coordinates with request context, and fall back to configured default |
| ~~D25~~ | ~~Secrets manager for integration credentials~~ | ~~Section 15~~ | DONE — keyring-based credential storage with self-describing CredentialSchema, REST API, settings UI, onboarding integration step |

### Resilience & Operations

| ID | Feature | Spec Section | Notes |
|----|---------|-------------|-------|
| D12 | Redis-down in-memory buffer | Section 15 | No fallback when Redis unavailable |
| D13 | Runtime config hot-reload | Section 14 | `RUNTIME_CONFIG_KEY` defined in streams.py but unused |
| D14 | Nested OTel spans + trace propagation | Section 11 | @traced exists, no nesting or Redis propagation |
| D15 | Logging file + OTLP sinks | Section 13 | Console only. No file rotation, no OTLP export |
| D16 | ALFRED_TRACE production flag | Evals | No conditional trace toggle |

### Evals & Testing

| ID | Feature | Spec Section | Notes |
|----|---------|-------------|-------|
| D17 | Eval pytest auto-discovery | Evals | Scenarios loadable but not parameterized as pytest items |
| D18 | Intermediate step assertions | Evals | No tool selection/preference influence checkpoints |

### External Integrations

| ID | Feature | Spec Section | Notes |
|----|---------|-------------|-------|
| D19 | Context Provider Option C entities | Design spec | home-service only exposes lights + scenes, not automations/scripts/input_booleans |
| D20 | Reflex Engine via DomainRouter | Section 2 | Reflex bypasses DomainRouter, uses HomeAgent directly |

---

## Tier 5: Performance Improvements

Items that won't block development but should be addressed before scale/production.

| ID | Improvement | Location | Notes |
|----|-------------|----------|-------|
| ~~P1~~ | ~~Signal Bridge polling → Redis pub/sub~~ | ~~`core/channels/signal_bridge/bridge.py`~~ | DONE — Removed polling. Dispatcher now routes to SignalChannelAdapter directly |
| P2 | Notification dispatcher as sub-agent | `core/notifications/` | Instead of hardcoded routing rules, make the dispatcher an LLM-powered sub-agent that reasons about context, urgency, channel selection, and DND. Would allow natural-language routing policies and learning from user feedback |
| P3 | Notification dedup/cooldown | `core/notifications/` | Hash-based dedup with Redis TTL key (`notification:{source}:{title_hash}`). Default 5min cooldown, configurable per urgency (urgent = no cooldown). Prevents notification storms from repeated sensor triggers or multiple sources detecting same situation |
| P4 | PiperTTS GPU acceleration | `core/voice/tts.py` | Currently loads ONNX model with default CPU execution provider. Configure CUDA EP on prod (RTX 4090) and CoreML EP on dev (M4 Max) for faster synthesis |
| P5 | Settings page CSS card styling | `web/style.css`, `web/settings.js` | Integration cards render functionally but CSS classes aren't applying properly — cards appear unstyled. Likely a specificity or class name mismatch issue |
| P6 | Reflex-driven notification intelligence for TriggerFired | `core/reflex/`, `core/notifications/` | Instead of immediate fire-and-forget notification on TriggerFired, let the Reflex SLM decide whether/what/how to notify (urgency, wording, suppression). More intelligent but adds latency + inference cost. Experiment with whether SLM-driven notification decisions outperform static ones |

---

## Tier 6: Public Launch

Items needed before non-engineering users interact with Alfred.

| ID | Item | Notes |
|----|------|-------|
| PL1 | User guide for pro users | Explain Alfred's behavior, notification channels, DND logic, proactivity levels, voice commands, and how the system works — written for non-engineering power users, not developers |

---

## Stubs & Placeholders (Still in Code)

These are implemented as interfaces/stubs but have no real logic:

| Stub | Location | Notes |
|------|----------|-------|
| SpeakerID confidence=0.0 | `core/voice/speaker_id.py` | No SpeechBrain model |
| ProactivityRelevanceScore returns 0.5 | Proactivity scoring | Hardcoded neutral |
| sqlite-vec not used | Episodic search | Full-table-scan cosine fallback |
| Decay tiers defined (4) but only 2 used | Episodic store | hot/cold only |
