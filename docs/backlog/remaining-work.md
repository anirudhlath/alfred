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
| D9 | Proactive notification dispatch + DND + priority routing | Section 8 | NotificationPublisher exists but no routing/DND logic |
| D10 | Channel rate limiting | Section 15 | No middleware, no per-user limits |
| D11 | Streaming TTS to WebSocket | Section 6 | Full blob only, no chunk streaming |

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

## Stubs & Placeholders (Still in Code)

These are implemented as interfaces/stubs but have no real logic:

| Stub | Location | Notes |
|------|----------|-------|
| SpeakerID confidence=0.0 | `core/voice/speaker_id.py` | No SpeechBrain model |
| ProactivityRelevanceScore returns 0.5 | Proactivity scoring | Hardcoded neutral |
| sqlite-vec not used | Episodic search | Full-table-scan cosine fallback |
| Decay tiers defined (4) but only 2 used | Episodic store | hot/cold only |
