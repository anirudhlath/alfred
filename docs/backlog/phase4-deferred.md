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
