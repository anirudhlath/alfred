# Spoken-Reply Voice Parity Across Satellite / Web / iOS

**Feature:** Shared TTS seam (`core/channels/voice_models.py::aget_tts`) across all voice surfaces
**Priority:** medium
**Type:** integration

## Prerequisites
- Full runner running (`uv run python -m runner`), infra up (`bash scripts/dev-up.sh`)
- Default config: `ALFRED_TTS_BACKEND` unset or `kokoro`, `KOKORO_VOICE=am_michael` (or unset)
- Web channel reachable and authenticated at `http://localhost:8081`
- A connected Wyoming satellite (e.g. the dev-mac satellite from `docs/voice-satellites.md`)
- iOS client (AlfredKit) connected via the same backend, with an active WebSocket chat session
  (channel `ios`) — not just APNs push

## Test Steps
1. From the web channel, send a chat message that produces a spoken reply; listen.
2. From the connected satellite, say the wake word and a request; listen to the spoken reply.
3. From the iOS app (with its `/ws` chat connection active, not backgrounded), send or speak a
   request; listen to the spoken reply.
4. Compare all three by ear: same voice identity (`am_michael`), same prosody/speaking style,
   comparable audio quality.
5. Fire one URGENT notification via the Redis dispatch stream (snippet in
   `voice-satellite-urgent-announcement-audio-quality.md`) and confirm the satellite and web
   surfaces both speak it in the same Kokoro voice as regular replies.

## Expected Result
- All three surfaces' spoken replies are recognizably the same Kokoro `am_michael` voice — no
  surface is silently mixing in Piper, a different Kokoro voice, or a stale cached instance.
- URGENT notification speech (satellite + web) matches the voice used for regular replies on
  those same surfaces.

## Notes
- All voice surfaces share one in-process `aget_tts()`/`get_tts()` singleton in
  `core/channels/voice_models.py` — this ticket exists to catch integration-level regressions
  (e.g. a code path that constructs a second, differently-configured TTS instance) that
  per-surface unit tests, which mock the TTS instance, would not catch.
- iOS receives URGENT-notification *audio* only when it has an active WebSocket session — per
  `core/channels/web_server.py`, iOS otherwise receives URGENT notifications via APNs (text/push
  only, no synthesized audio); that's expected and not a parity bug to file here. This ticket's
  iOS coverage is about regular chat/voice replies over its `/ws` session, where it shares the
  exact same `synthesize_async` seam as web.
- Not a deep audio-quality audit of any one surface — see
  `kokoro-mac-cpu-audio-quality.md` and `voice-satellite-urgent-announcement-audio-quality.md`
  for that.
