# Voice Satellite: Voice Enrollment + Satellite Speaker-ID Match

**Feature:** `POST /api/voice/enroll` + `SpeakerID` (`core/voice/speaker_id.py`) end-to-end
**Priority:** critical
**Type:** e2e

## Prerequisites
- Full stack running: Redis Stack + Mosquitto (`brew install redis-stack mosquitto && brew services start redis-stack mosquitto`), `home-service` on port 8000, real LLM key,
  `uv run python -m runner`
- `config/satellites.yaml` with a `127.0.0.1` entry and the macOS dev satellite running (real
  mic/speaker), as in `voice-satellite-real-mic-full-loop.md`
- An authenticated session cookie from a device on the trusted network (localhost or
  Tailscale) — `/api/voice/enroll` is gated by both `require_trusted_network` and
  `require_authenticated` (`core/channels/web_server.py`)
- A way to record three samples and post them: **PWA phase 1 has no enrollment surface**
  (see the first note below), so drive the endpoint directly — `curl` with the session cookie,
  or the HTTP client of your choice

## Test Steps
1. Record three samples of the same person speaking a normal sentence each (any recorder;
   the handler decodes whatever `decode_to_pcm16k` accepts)
2. `POST` all three to `/api/voice/enroll` with `identity: "sir"` and the session cookie
3. Confirm the response is a success, not a 401 (no session) or 403 (untrusted origin)
4. Repeat the post with three fresh samples to confirm re-enrollment is an overwrite, not an
   error — see the last note
5. (Skipped in phase 1 — there is no card to watch. The equivalent UI assertions belong with
   the Workshop's enrollment surface when it ships.)
6. Check the channels process log for `Enrolled voiceprint for 'sir' (3 samples)`
7. Walk to (or sit near) the dev-mac satellite, say the wake word, and speak a normal request
   in your own voice
8. Check the conscious process log for the line `Identity resolved: sir (method=voice_id,
   confidence=<value>)` — this is the log line in `core/conscious/engine.py` that proves the
   satellite utterance was matched to your enrolled voiceprint rather than falling back to
   the default `local_claim` trust
9. Have a second person (a different voice) say the wake word and speak to the same
   satellite; check that the log shows either `method=local_claim` (identity defaults to
   "sir" via the pipeline's hardcoded default claim) rather than a false-positive
   `voice_id` match on the other person's voice — since only one identity is enrolled

## Expected Result
- Steps 1-4: three real samples of a real human voice are accepted and stored — this case
  exists because synthetic embeddings cannot exercise the real ECAPA model
- Step 6-8: the enrolled voiceprint is later matched on a live satellite utterance spoken by
  the SAME person who enrolled — `method=voice_id` appears in the log with a confidence at or
  above the `SPEAKER_ID_THRESHOLD` default (0.45 cosine, mapped to a reported confidence
  between 0.7-0.95)
- Step 9: a different speaker's voice does not spuriously match the enrolled voiceprint

## Notes
- **There is no enrollment UI in PWA phase 1.** The Settings page that carried the Voice
  Enrollment card was removed in the hard cut; `docs/voice-satellites.md` and
  `docs/backlog/low/pwa-phase1-followups.md` §5 both record that the Workshop reinstates it in
  phase 2. The client's own microphone path (`web/src/room/HoldToTalk.tsx`) records for chat
  only and never posts to `/api/voice/enroll`.
- This case exercises what automated tests cannot reach: the cosine-similarity behaviour of
  the real ECAPA-TDNN model against two genuinely different human voices.
  `tests/core/voice/test_speaker_id.py` and `tests/core/channels/test_voice_enroll.py` only
  exercise this with synthetic/injected embeddings.
- Enroll under `identity: "sir"` — there is no path yet for enrolling additional household
  members under other names; a recognized
  non-"sir" voiceprint would currently be downgraded to guest regardless of match confidence
  (tracked in `docs/backlog/low/satellite-multi-user-voice-identity.md`) — step 9 above is
  expected to show `local_claim`/guest-adjacent behavior, not a crash
- If the ECAPA model hasn't been downloaded yet, first use will trigger an auto-download to
  `data/models/spkrec-ecapa-voxceleb` — expect a delay on the very first enrollment/identify
  call
- Try re-enrolling (running the flow a second time) to confirm the voiceprint updates rather
  than erroring — enrollment is an overwrite (mean-normalized `HSET`), not additive
