# Voice Satellite: Browser Voice Enrollment + Satellite Speaker-ID Match

**Feature:** Voice Enrollment Card (`web/src/pages/VoiceEnrollmentCard.tsx`) + `SpeakerID` (`core/voice/speaker_id.py`) end-to-end
**Priority:** critical
**Type:** e2e

## Prerequisites
- Full stack running: Redis Stack + Mosquitto (`brew install redis-stack mosquitto && brew services start redis-stack mosquitto`), `home-service` on port 8000, real LLM key,
  `uv run python -m runner`
- `config/satellites.yaml` with a `127.0.0.1` entry and the macOS dev satellite running (real
  mic/speaker), as in `voice-satellite-real-mic-full-loop.md`
- A real browser (Chrome/Safari) on the same trusted network as the server (localhost or
  Tailscale) with a registered WebAuthn passkey and an active authenticated session — `/api/voice/enroll`
  is gated by both `require_trusted_network` and `require_authenticated`
  (`core/channels/web_server.py`)
- Microphone permission available to the browser

## Test Steps
1. Open the web app (`http://localhost:8081`), log in via passkey, navigate to Settings
2. Locate the "VOICE ENROLLMENT" card and confirm it shows `0 / 3` samples and the first
   prompt ("Alfred, what's on my calendar for tomorrow morning?")
3. Click the mic button, read the first prompt aloud, click again to stop recording — confirm
   the counter advances to `1 / 3` and the prompt updates to the second sentence
4. Repeat for samples 2 and 3 (each with its own prompt)
5. After the 3rd sample, confirm the card auto-submits (`POST /api/voice/enroll`) and the UI
   transitions to "Voiceprint enrolled. Satellites will recognize your voice."
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
- Steps 2-5: recording flow works end to end in a real browser with a real mic — 3 real
  samples recorded, submitted, and the UI reflects success
- Step 6-8: the enrolled voiceprint is later matched on a live satellite utterance spoken by
  the SAME person who enrolled — `method=voice_id` appears in the log with a confidence at or
  above the `SPEAKER_ID_THRESHOLD` default (0.45 cosine, mapped to a reported confidence
  between 0.7-0.95)
- Step 9: a different speaker's voice does not spuriously match the enrolled voiceprint

## Notes
- This exercises the ONLY parts of the enrollment path automated tests cannot reach: a real
  `getUserMedia`/`MediaRecorder` flow in an actual browser (`web/src/chat/VoiceButton.tsx`),
  and cosine similarity behavior of the real ECAPA-TDNN model against two genuinely different
  human voices. `tests/core/voice/test_speaker_id.py` and
  `tests/core/channels/test_voice_enroll.py` only exercise this with synthetic/injected
  embeddings.
- The enrollment card hardcodes `identity: "sir"` (see `VoiceEnrollmentCard.tsx`) — there is
  no UI yet for enrolling additional household members under other names; a recognized
  non-"sir" voiceprint would currently be downgraded to guest regardless of match confidence
  (tracked in `docs/backlog/low/satellite-multi-user-voice-identity.md`) — step 9 above is
  expected to show `local_claim`/guest-adjacent behavior, not a crash
- If the ECAPA model hasn't been downloaded yet, first use will trigger an auto-download to
  `data/models/spkrec-ecapa-voxceleb` — expect a delay on the very first enrollment/identify
  call
- Try re-enrolling (running the flow a second time) to confirm the voiceprint updates rather
  than erroring — enrollment is an overwrite (mean-normalized `HSET`), not additive
