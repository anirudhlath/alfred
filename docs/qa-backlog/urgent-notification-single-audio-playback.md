# URGENT Notification Plays Audio Exactly Once in Browser

**Feature:** WebSocket adapter TTS synthesis for URGENT notifications (D28)
**Priority:** critical
**Type:** regression

## Prerequisites
- Alfred server running (`uv run python -m runner`)
- Browser open at `http://localhost:8081`
- User has interacted with the page (clicked or pressed a key) to unlock AudioContext
- Piper TTS loaded (first URGENT notification may trigger model download — wait for it)

## Test Steps
1. Open browser devtools → Network tab, filter to WS; open Console tab
2. Trigger an URGENT notification (e.g. via the conscious engine or a smoke-test script that publishes directly to the Redis dispatch stream with urgency=urgent)
3. Observe the WebSocket message payload in devtools (Network → WS → message frame)
4. Listen and observe the browser tab

## Expected Result
- Exactly one WebSocket message of type `notification` is received
- The message payload contains an `audio` field (base64-encoded WAV)
- The notification title and body are shown in the chat log or browser notification
- Audio plays once — the spoken text should be `"<title>: <body>"` (Piper TTS voice)
- No second audio burst follows (the old double-TTS bug would play audio a second time via the now-removed `voice_notification` message)

## Notes
- The old bug: a separate `voice_notification` WebSocket message was sent by the (now-removed) VoiceChannelAdapter, causing audio to play twice for URGENT notifications
- The fix consolidates TTS into the single `notification` message via the WebSocket adapter
- If Piper TTS model has not been downloaded yet, the first synthesis may take ~10–30s; subsequent ones are fast
- Test with both a short title/body and a long one to verify audio quality is acceptable
