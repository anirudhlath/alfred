# Remaining Work — Ground-Truth Audit (2026-03-20)

Consolidated from 6-agent parallel audit of master branch. Replaces all prior backlog files.
Prior files (`context-provider.md`, `evals-runner.md`, `phase3-review.md`, `phase4-deferred.md`,
`trigger-engine-simplification.md`) are superseded by this document.

---

## Tier 1: Critical (System doesn't fully work without these)

### C1: Librarian Never Scheduled
- **Impact:** Consolidation only runs via manual `python -m core.librarian`. Scratchpad grows forever, no learning happens, semantic memory never updates.
- **Fix:** Add Librarian to unified runner with a daily schedule (APScheduler or periodic asyncio task).
- **Files:** `runner/__main__.py`, possibly `core/librarian/__main__.py`
- **Scope:** Small

### C2: Signal Bridge `_send_signal()` is a Stub
- **Impact:** Notifications reach Redis stream but never reach the user's phone. Bridge is read-only outbound.
- **Fix:** Implement `asyncio.create_subprocess_exec("signal-cli", ...)` call.
- **Files:** `core/channels/signal_bridge/bridge.py:42-45`
- **Scope:** Small

### C3: Episodic Stream Unbounded Growth
- **Impact:** `EPISODIC_STREAM` Redis stream is never trimmed. Redis memory grows indefinitely.
- **Fix:** Add `MAXLEN ~10000` to `xadd()` in `EpisodicStore.write()`, or implement XTRIM in Librarian `_apply_decay()`.
- **Files:** `core/memory/episodic/store.py:53`, `core/librarian/consolidator.py:221-228`
- **Scope:** Small

### C4: Librarian N+1 LLM Calls
- **Impact:** `_extract_entities()` makes 1 Claude API call per scratchpad line. 100 lines = 100 calls + significant cost.
- **Fix:** Batch all lines into a single prompt that returns entities for each line.
- **Files:** `core/librarian/consolidator.py:89-152`
- **Scope:** Small

---

## Tier 2: Important (Should fix before production)

### I1: AioRedis Type Alias in Wrong Place
- **Impact:** Defined in `core/reflex/runner.py:26`, imported across 7+ modules. Violates `shared/` as single source of truth.
- **Fix:** Move to `shared/types.py`, update all imports.
- **Files:** `shared/types.py` (create), all consumers
- **Scope:** Tiny

### I2: MemoryReader No File Caching
- **Impact:** `get_preferences()` and `get_profile()` re-read and glob all `.md` files on every request. Only proactivity is cached.
- **Fix:** Add TTL-based caching (e.g., 60s) for preference and profile content.
- **Files:** `core/conscious/memory_reader.py`
- **Scope:** Small

### I3: max_tokens Hardcoded to 2048
- **Impact:** Long briefings (morning summary with calendar + weather + portfolio) may need 3000+ tokens.
- **Fix:** Add `max_tokens` to `AlfredConfig`, use in `engine.py:235`.
- **Files:** `shared/config.py`, `core/conscious/engine.py:235`
- **Scope:** Tiny

### I4: Identity "sir" Magic String
- **Impact:** Literal `"sir"` in 4 places in `identity.py`. Magic string, fragile.
- **Fix:** Extract to constant (e.g., `IDENTITY_SIR = "sir"`, `IDENTITY_GUEST = "guest"`).
- **Files:** `core/conscious/identity.py:26,44,72,74`
- **Scope:** Tiny

### I5: Librarian Deferred litellm Imports
- **Impact:** `import litellm` and `import json` inside method bodies in consolidator.py. Reload every call, mypy may miss type errors.
- **Fix:** Module-level for json, conditional with TYPE_CHECKING for litellm.
- **Files:** `core/librarian/consolidator.py:94-96,171`
- **Scope:** Tiny

### I6: Onboarding Test Captures Files But Never Asserts Contents
- **Impact:** Test populates `written_files` dict but only checks HTTP 200. Silently passes if files have wrong content.
- **Fix:** Assert file contents, YAML frontmatter structure, field values.
- **Files:** `tests/core/channels/test_web_server.py:62-110`
- **Scope:** Tiny

### I7: Reflex/Conscious Dual MemoryReader
- **Impact:** Function-based reader in `core/reflex/memory_reader.py` and class-based in `core/conscious/memory_reader.py`. No shared implementation.
- **Fix:** Consolidate into single implementation in `core/memory/` or have Reflex use the Conscious reader.
- **Files:** `core/reflex/memory_reader.py`, `core/conscious/memory_reader.py`
- **Scope:** Small

### I8: RoutineStore Globs+Parses YAML Every Call
- **Impact:** `list_all()` does full directory scan and YAML parse per call. Slow with many routines.
- **Fix:** Add in-memory index, refresh on write or periodic interval.
- **Files:** `core/memory/routines/store.py:49-58`
- **Scope:** Small

---

## Tier 3: Low Priority (Nice-to-have, not blocking)

### L1: Sensor Triggers Evaluated on Tick
- **Impact:** `evaluate_tick()` evaluates all triggers including SensorTriggers that always return False without an event.
- **Fix:** Add `responds_to_tick: bool` class attribute, skip in tick loop.
- **Files:** `core/triggers/engine.py:72-81`, `core/triggers/models.py`
- **Scope:** Tiny

### L2: Shared Stream Entry Parsing Duplicated
- **Impact:** Identical `raw.decode() if isinstance(raw, bytes) else raw` pattern in triggers and reflex.
- **Fix:** Extract to `shared/streams.py` utility.
- **Files:** `core/triggers/__main__.py:81`, `core/reflex/runner.py:64`
- **Scope:** Tiny

### L3: TriggerFeature `_store = None # type: ignore`
- **Impact:** Latent `AttributeError` if TriggerFeature used without context. Mypy suppressed.
- **Fix:** Use `Optional[TriggerStore]` with guard helper.
- **Files:** `core/triggers/feature.py:37`
- **Scope:** Tiny

### L4: STT/TTS Lazy-Loader Boilerplate Duplicated
- **Impact:** `_get_stt()` and `_get_tts()` in web_server.py are identical patterns.
- **Fix:** Extract generic lazy-loader factory.
- **Files:** `core/channels/web_server.py:27-58`
- **Scope:** Tiny

### L5: Engine Constructor 16 Parameters
- **Impact:** `ConsciousEngine.__init__` has 16 params. Hard to read and maintain.
- **Fix:** Group into config/deps dataclass.
- **Files:** `core/conscious/engine.py:61-77`
- **Scope:** Small

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
