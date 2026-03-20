# Phase 4 Deferred Items

Items identified during the Phase 3 spec audit and Phase 4 planning that are not wiring gaps but are required for production readiness.

## Security & Authentication

### WebAuthn Registration + Login
- **Why deferred:** Full feature requiring frontend `navigator.credentials` API, server-side challenge/response endpoints, and a credential store (SQLite or Redis). Not a wiring gap — the local-claim trust workaround (Task 3 of Phase 4) provides dev-time identity resolution.
- **Impact:** Without this, anyone on the local network can claim to be "sir" via the PWA. Acceptable for dev, not for production.
- **Depends on:** Phase 4A Task 3 (local-claim trust is the interim solution)
- **Files to create:** `core/conscious/webauthn.py`, `web/` registration/login UI flows, credential store
- **Estimated scope:** Medium (1-2 days)

### Voice Enrollment (SpeechBrain Speaker Verification)
- **Why deferred:** Requires audio sample collection UI, SpeechBrain model download/training, voiceprint storage in Redis (`alfred:identity:voiceprint` key already defined in `shared/streams.py`), and integration into `IdentityGate.resolve()`.
- **Impact:** Voice channel cannot authenticate "sir" — falls back to local-claim trust or guest.
- **Depends on:** WebAuthn (for initial enrollment pairing), Phase 4A Task 3
- **Files to create:** `core/identity/speaker_id.py`, enrollment UI in PWA
- **Estimated scope:** Large (2-3 days, ML dependency)

## Intelligence

### Procedural Memory Pattern Detection
- **Why deferred:** The Librarian needs multiple consolidation cycles with real episodic data (2+ weeks of observations) before it can meaningfully detect recurring behavioral patterns (e.g., "sir turns on office lights at 8am on weekdays").
- **Impact:** No learned routines — Alfred can only follow explicitly defined trigger rules, not inferred habits.
- **Depends on:** Phase 4B Tasks 5-6 (entity extraction + semantic updates must work first), real usage generating episodic entries
- **Files to modify:** `core/librarian/consolidator.py` (the `# 5. TODO` stub)
- **Estimated scope:** Medium (1 day, but requires data to test against)

## Performance & UX

### Streaming TTS (WebSocket Audio Chunks)
- **Why deferred:** Current TTS works end-to-end (synthesize full WAV, base64 encode, send over WebSocket). Streaming would reduce time-to-first-audio but is a latency optimization, not a missing feature.
- **Impact:** User hears audio only after full synthesis completes (~1-3s delay for longer responses).
- **Depends on:** Phase 3 Step 5 (TTS is already working)
- **Files to modify:** `core/channels/web_server.py` (WebSocket endpoint), `web/app.js` (audio streaming playback)
- **Estimated scope:** Small-Medium (0.5-1 day)

### Runtime Config Hot-Reload
- **Why deferred:** Config works from env vars at startup via `AlfredConfig.from_env()`. Hot-reload would allow changing proactivity level, cost caps, etc. without restart. The `RUNTIME_CONFIG_KEY` Redis key is already defined in `shared/streams.py` but unused.
- **Impact:** Config changes require service restart.
- **Depends on:** Phase 4A (basic wiring must work first)
- **Files to modify:** `shared/config.py`, `core/conscious/engine.py` (periodic config refresh)
- **Estimated scope:** Small (0.5 day)

## Code Quality (from Phase 4 Simplify Review)

Items identified during the post-implementation simplify review. These are quality improvements, not functional gaps.

### Librarian N+1 LLM Calls
- **Why deferred:** `_extract_entities()` is called per-entry in a loop, making one LLM call per episodic entry. Should batch entries into a single prompt.
- **Impact:** Consolidation cycles with many entries will be slow and expensive.
- **Files to modify:** `core/librarian/consolidator.py` (`_extract_episodic_entries` loop)
- **Estimated scope:** Small (0.5 day)

### AioRedis Type Alias Deduplication
- **Why deferred:** Multiple modules define or import `AioRedis` differently. Should have a single canonical import path.
- **Impact:** Inconsistent typing, mild confusion for contributors.
- **Files to modify:** `shared/types.py` (new canonical location), all consumers
- **Estimated scope:** Small (0.5 day)

### Reflex/Conscious MemoryReader Consolidation
- **Why deferred:** Both the Reflex Engine and Conscious Engine have ways to read memory. Could share a common reader.
- **Impact:** Mild code duplication.
- **Files to modify:** `core/conscious/memory_reader.py`, `core/reflex/` memory access
- **Estimated scope:** Small (0.5 day)

### STT/TTS Lazy-Loader DRY
- **Why deferred:** `_get_stt()` and `_get_tts()` in `web_server.py` follow identical patterns (global sentinel, try/except import, False sentinel). Could use a generic lazy-loader.
- **Impact:** Minor duplication in web_server.py.
- **Files to modify:** `core/channels/web_server.py`
- **Estimated scope:** Tiny (< 0.5 day)

### Identity "sir" Constant
- **Why deferred:** The string `"sir"` appears as a literal in identity resolution. Should be a named constant.
- **Impact:** Magic string, minor maintainability.
- **Files to modify:** `core/conscious/identity.py`
- **Estimated scope:** Tiny (< 0.5 day)

### MemoryReader File Caching
- **Why deferred:** MemoryReader re-reads preference Markdown files on every request. Could cache with TTL.
- **Impact:** Extra disk I/O per request (negligible for dev, relevant for production).
- **Files to modify:** `core/conscious/memory_reader.py`
- **Estimated scope:** Small (0.5 day)

### RoutineStore In-Memory Index
- **Why deferred:** `list_all()` globs and parses every YAML file on each call. Could maintain an in-memory index.
- **Impact:** Slow if many routines accumulate. Currently fine with few routines.
- **Files to modify:** `core/memory/routines/store.py`
- **Estimated scope:** Small (0.5 day)

### Engine Constructor Parameter Grouping
- **Why deferred:** `ConsciousEngine.__init__` has many parameters. Could use a config/deps dataclass.
- **Impact:** Long parameter lists, minor readability.
- **Files to modify:** `core/conscious/engine.py`
- **Estimated scope:** Small (0.5 day)

## Architecture Review Deferred Items (2026-03-19)

### M1: Configurable max_tokens for LLM Calls
- **Why deferred:** `max_tokens=2048` is hardcoded in `engine.py:_call_llm`. Should be configurable via `AlfredConfig` for long briefings that may need 3000+ tokens.
- **Files to modify:** `shared/config.py`, `core/conscious/engine.py`
- **Estimated scope:** Tiny (< 0.5 day)

### M3: Episodic Stream Unbounded Growth
- **Why deferred:** `EPISODIC_STREAM` Redis stream is never trimmed. `_apply_decay()` in the Librarian is a placeholder. Should add `XADD ... MAXLEN ~` or XTRIM in the decay pass.
- **Files to modify:** `core/memory/episodic/store.py`, `core/librarian/consolidator.py`
- **Estimated scope:** Small (0.5 day)

### M4: Librarian Deferred litellm Imports
- **Why deferred:** `import litellm` and `import json` are inside method bodies in `consolidator.py`. Should be module-level (json) or conditional with TYPE_CHECKING (litellm).
- **Files to modify:** `core/librarian/consolidator.py`
- **Estimated scope:** Tiny (< 0.5 day)

### M7: Onboarding Test Coverage Gap
- **Why deferred:** `test_onboarding_endpoint_saves_preferences` only checks HTTP status, not that files were actually written. Should use `tmp_path` and assert file contents.
- **Files to modify:** `tests/core/channels/test_web_server.py`
- **Estimated scope:** Tiny (< 0.5 day)
